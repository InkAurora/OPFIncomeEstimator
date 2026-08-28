"""Load the promoted estimator from an immutable bundle, or refuse to load at all.

``EnsembleIncomeEstimator`` is the research entry point. It takes optional artifact paths, and when
one is missing it still answers, from a weaker component, and says so in the routing reasons. That
is correct for a laboratory and wrong for a deployment: a production caller that receives a number
has no way to notice that the capacity model failed to load and the answer came from
``recurring-streams-0.2.0`` instead.

``ProductionIncomeEstimator`` inverts that default. It takes a bundle directory, verifies every
file the manifest pins, and either returns an estimator bound to exactly those bytes or raises.
There is no partial success and no fallback. Degrading is a decision for the caller to make with
the error in hand, not something to discover later in a report.

The checks are ordered cheapest-first and stop at the first failure, so an operator gets one
actionable error rather than a list:

1. the directory and manifest exist and parse;
2. the manifest matches bundle contract ``1.0``;
3. every pinned file is present and hashes to what the manifest says;
4. the calibration was fitted against exactly these capacity bytes;
5. the artifacts declare the versions the manifest claims for them;
6. the feature set the capacity model was trained against is the one this package computes;
7. the contracts and the package version are ones this build can honour.

Step 5 exists because a digest match proves the bytes, not that the manifest described them
honestly. Step 6 is exact rather than a floor: the capacity model looks features up by name and its
stumps split on binned values, so a renamed, re-binned, or reordered feature does not fail loudly;
it changes what the model is scoring while every version string still agrees.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Self

from pydantic import ValidationError

from income_estimator.contracts.bundle_v1 import (
    BUNDLE_CONTRACT_VERSION,
    BundleManifestV1,
)
from income_estimator.contracts.explanation_v1 import (
    ESTIMATOR_EXPLANATION_CONTRACT_VERSION,
    EstimationExplanationV1,
)
from income_estimator.contracts.output_v1_1 import (
    ESTIMATOR_OUTPUT_CONTRACT_VERSION,
    IncomeEstimateV11,
)
from income_estimator.contracts.production_v1 import (
    PRODUCTION_RESULT_CONTRACT_VERSION,
    ProductionResultV1,
)
from income_estimator.features import FEATURE_SCHEMA_FINGERPRINT, FEATURE_SET_VERSION
from income_estimator.models.quantiles import CalibrationBindingError
from income_estimator.pipeline import EnsembleIncomeEstimator

MANIFEST_FILENAME = "manifest.json"


class BundleError(ValueError):
    """Base for every refusal to load a bundle."""


class BundleManifestError(BundleError):
    """The bundle is missing, unreadable, or not a valid manifest."""


class BundleIntegrityError(BundleError):
    """A pinned file is absent or does not hash to its recorded digest."""


class BundleCompatibilityError(BundleError):
    """The bundle is internally valid but this package cannot honour it."""


def _read_digest(path: Path) -> tuple[bytes, str]:
    payload = path.read_bytes()
    return payload, hashlib.sha256(payload).hexdigest()


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse a dotted release version, ignoring any suffix after the numeric part.

    Deliberately small. Bundles are versioned by this repository's own release process, so the
    full specifier grammar is not needed, and adding a dependency to compare two strings would put
    a resolver between a deployment and its model.
    """

    parts: list[int] = []
    for part in version.split("."):
        digits = ""
        for character in part:
            if not character.isdigit():
                break
            digits += character
        if not digits:
            break
        parts.append(int(digits))
    if not parts:
        raise BundleCompatibilityError(f"cannot compare version {version!r}")
    return tuple(parts)


def load_manifest(bundle_path: Path) -> tuple[BundleManifestV1, str]:
    """Parse and validate the manifest, returning it with the bundle digest.

    The bundle digest is the SHA-256 of the manifest bytes. Because the manifest pins every other
    file by digest, and those digests are verified before the estimator is returned, this one value
    identifies the whole bundle.
    """

    directory = Path(bundle_path)
    if not directory.is_dir():
        raise BundleManifestError(f"bundle directory not found: {directory}")
    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise BundleManifestError(f"bundle manifest not found: {manifest_path}")

    payload_bytes, bundle_digest = _read_digest(manifest_path)
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleManifestError(f"bundle manifest is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise BundleManifestError("bundle manifest must be a JSON object")
    declared = payload.get("schema_version")
    if declared != BUNDLE_CONTRACT_VERSION:
        raise BundleManifestError(
            f"bundle contract {declared!r} is not supported; this package reads "
            f"{BUNDLE_CONTRACT_VERSION!r}"
        )
    try:
        manifest = BundleManifestV1.model_validate(payload)
    except ValidationError as error:
        raise BundleManifestError(f"bundle manifest failed validation: {error}") from error
    return manifest, bundle_digest


def verify_bundle(bundle_path: Path) -> tuple[BundleManifestV1, str]:
    """Check every pinned file without constructing an estimator.

    Separate from loading so a deployment can verify a bundle it is about to install, and so the
    integrity gate in CI does not have to pay for model construction.
    """

    directory = Path(bundle_path)
    manifest, bundle_digest = load_manifest(directory)
    for reference in manifest.files():
        target = directory / reference.path
        if not target.is_file():
            raise BundleIntegrityError(f"bundle file missing: {reference.path}")
        _, digest = _read_digest(target)
        if digest != reference.sha256:
            raise BundleIntegrityError(
                f"bundle file {reference.path} hashes to {digest}, "
                f"but the manifest pins {reference.sha256}"
            )
    return manifest, bundle_digest


class ProductionIncomeEstimator(EnsembleIncomeEstimator):
    """The promoted estimator, bound to one verified bundle.

    Constructed only through :meth:`from_bundle`. The direct constructor is inherited and still
    accepts loose paths, which is what the research and debug profiles use; that path does not set
    the bundle attributes and cannot produce a production result.
    """

    def __init__(
        self,
        capacity_model_path: Path | None = None,
        rule_config: Any = None,
        calibration_path: Path | None = None,
        *,
        manifest: BundleManifestV1 | None = None,
        bundle_digest: str | None = None,
        bundle_path: Path | None = None,
    ) -> None:
        super().__init__(capacity_model_path, rule_config, calibration_path)
        self.manifest = manifest
        self.bundle_digest = bundle_digest
        self.bundle_path = bundle_path

    @classmethod
    def from_bundle(cls, bundle_path: Path | str) -> Self:
        """Verify a bundle directory and return an estimator bound to exactly its bytes.

        Raises :class:`BundleError` and never returns a degraded estimator. Every artifact the
        manifest pins is required, including the calibration: a production estimate without an
        interval is a different product, not a slightly worse one, and choosing to ship it is a
        decision that belongs to the caller.
        """

        directory = Path(bundle_path)
        manifest, bundle_digest = verify_bundle(directory)

        capacity_path = directory / manifest.capacity.path
        calibration_path = directory / manifest.calibration.path

        # Constructing the ensemble is what enforces the capacity/calibration binding, and it
        # raises its own error type. Every refusal from this method is a BundleError, so a caller
        # needs one except clause rather than a taxonomy.
        try:
            estimator = cls(
                capacity_path,
                None,
                calibration_path,
                manifest=manifest,
                bundle_digest=bundle_digest,
                bundle_path=directory,
            )
        except CalibrationBindingError as error:
            raise BundleCompatibilityError(f"bundle artifacts are not a bound pair: {error}") from (
                error
            )
        except ValidationError as error:
            raise BundleCompatibilityError(f"bundle artifact failed validation: {error}") from error

        # The manifest is a claim about the artifacts; the artifacts are the fact. A digest match
        # proves the bytes are the ones that were pinned, not that the manifest described them
        # honestly, so the versions are compared after loading rather than trusted from the file.
        capacity_artifact = estimator.capacity.artifact if estimator.capacity is not None else None
        if capacity_artifact is None:
            raise BundleCompatibilityError("bundle capacity artifact did not load")
        if capacity_artifact.model_version != manifest.capacity.version:
            raise BundleCompatibilityError(
                f"manifest names capacity {manifest.capacity.version}, but the artifact declares "
                f"{capacity_artifact.model_version}"
            )
        calibration_artifact = (
            estimator.intervals.artifact if estimator.intervals is not None else None
        )
        if calibration_artifact is None:
            raise BundleCompatibilityError("bundle calibration artifact did not load")
        if calibration_artifact.calibration_version != manifest.calibration.version:
            raise BundleCompatibilityError(
                f"manifest names calibration {manifest.calibration.version}, but the artifact "
                f"declares {calibration_artifact.calibration_version}"
            )

        if manifest.feature_set_version != FEATURE_SET_VERSION:
            raise BundleCompatibilityError(
                f"bundle requires feature set {manifest.feature_set_version}, but this package "
                f"computes {FEATURE_SET_VERSION}"
            )
        if manifest.feature_schema_fingerprint != FEATURE_SCHEMA_FINGERPRINT:
            raise BundleCompatibilityError(
                f"bundle requires feature schema fingerprint {manifest.feature_schema_fingerprint},"
                f" but this package computes {FEATURE_SCHEMA_FINGERPRINT}"
            )
        if capacity_artifact.feature_version != FEATURE_SET_VERSION:
            raise BundleCompatibilityError(
                f"capacity artifact was trained on feature set {capacity_artifact.feature_version},"
                f" but this package computes {FEATURE_SET_VERSION}"
            )
        if capacity_artifact.feature_schema_fingerprint != FEATURE_SCHEMA_FINGERPRINT:
            raise BundleCompatibilityError(
                "capacity artifact was trained on feature schema fingerprint "
                f"{capacity_artifact.feature_schema_fingerprint}, but this package computes "
                f"{FEATURE_SCHEMA_FINGERPRINT}"
            )

        if manifest.output_contract_version != ESTIMATOR_OUTPUT_CONTRACT_VERSION:
            raise BundleCompatibilityError(
                f"bundle expects output contract {manifest.output_contract_version}, but this "
                f"package emits {ESTIMATOR_OUTPUT_CONTRACT_VERSION}"
            )
        if manifest.explanation_contract_version != ESTIMATOR_EXPLANATION_CONTRACT_VERSION:
            raise BundleCompatibilityError(
                f"bundle expects explanation contract {manifest.explanation_contract_version}, but "
                f"this package emits {ESTIMATOR_EXPLANATION_CONTRACT_VERSION}"
            )
        if manifest.estimator_version != estimator.estimator_version:
            raise BundleCompatibilityError(
                f"bundle expects estimator {manifest.estimator_version}, but this package is "
                f"{estimator.estimator_version}"
            )

        from income_estimator import __version__ as package_version

        if _version_tuple(package_version) < _version_tuple(manifest.estimator_package_version):
            raise BundleCompatibilityError(
                f"bundle requires income-estimator {manifest.estimator_package_version} or newer, "
                f"but this package is {package_version}"
            )

        envelope = calibration_artifact.support_envelope
        declared_envelope = manifest.support_envelope_version
        actual_envelope = envelope.schema_version if envelope is not None else None
        if declared_envelope != actual_envelope:
            raise BundleCompatibilityError(
                f"manifest declares support envelope {declared_envelope!r}, but the calibration "
                f"carries {actual_envelope!r}"
            )
        return estimator

    def _envelope(
        self,
        *,
        estimate: IncomeEstimateV11 | None = None,
        explanation: EstimationExplanationV1 | None = None,
    ) -> ProductionResultV1:
        if self.manifest is None or self.bundle_digest is None:
            raise BundleError(
                "this estimator was not loaded from a bundle; use "
                "ProductionIncomeEstimator.from_bundle for a production result"
            )
        from income_estimator import __version__ as package_version

        return ProductionResultV1(
            schema_version=PRODUCTION_RESULT_CONTRACT_VERSION,
            bundle_contract_version=self.manifest.schema_version,
            bundle_id=self.manifest.bundle_id,
            bundle_version=self.manifest.bundle_version,
            bundle_digest=self.bundle_digest,
            estimator_package_version=package_version,
            estimator_version=self.estimator_version,
            feature_set_version=FEATURE_SET_VERSION,
            model_versions=self.model_versions,
            estimate=estimate,
            explanation=explanation,
        )

    def estimate_production(self, request: Any) -> ProductionResultV1:
        """Return an output ``1.1`` estimate stamped with this bundle's identity."""

        return self._envelope(estimate=self.estimate_v1_1(request))

    def explain_production(self, request: Any) -> ProductionResultV1:
        """Return an explanation ``1.0`` report stamped with this bundle's identity."""

        return self._envelope(explanation=self.explain_estimate(request))


__all__ = [
    "MANIFEST_FILENAME",
    "BundleCompatibilityError",
    "BundleError",
    "BundleIntegrityError",
    "BundleManifestError",
    "ProductionIncomeEstimator",
    "load_manifest",
    "verify_bundle",
]
