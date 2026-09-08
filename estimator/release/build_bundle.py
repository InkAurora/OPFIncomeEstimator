"""Assemble a deployment bundle from promoted artifacts.

The bundle is the release artifact; this script is how it is produced, and it is deterministic so
that producing it twice from the same inputs yields byte-identical output. That matters because the
bundle digest is the SHA-256 of the manifest, and a manifest whose key order or float formatting
drifted between runs would give the same model two identities.

Artifacts are copied rather than referenced. A bundle that pointed back into this repository would
be a bundle only on this machine.

Run from the estimator directory:

    python -m release.build_bundle --output bundles/production-0.12.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from income_estimator.contracts.bundle_v1 import BUNDLE_CONTRACT_VERSION, BundleManifestV1
from income_estimator.contracts.explanation_v1 import ESTIMATOR_EXPLANATION_CONTRACT_VERSION
from income_estimator.contracts.output_v1_2 import ESTIMATOR_OUTPUT_CONTRACT_VERSION
from income_estimator.features import FEATURE_SCHEMA_FINGERPRINT, FEATURE_SET_VERSION
from income_estimator.models.capacity import CapacityEstimatorArtifact
from income_estimator.models.quantiles import ConformalCalibrationArtifact
from income_estimator.pipeline import ENSEMBLE_ESTIMATOR_VERSION

ARTIFACT_ROOT = Path(__file__).parents[1] / "training" / "artifacts"

CRLF = bytes((13, 10))

CAPACITY_SOURCE = ARTIFACT_ROOT / "capacity-estimator-0.6.0.json"
CALIBRATION_SOURCE = ARTIFACT_ROOT / "quantile-calibration-0.12.0.json"
CAPACITY_REPORT_SOURCE = ARTIFACT_ROOT / "capacity-estimator-0.6.0-report.json"
CALIBRATION_REPORT_SOURCE = ARTIFACT_ROOT / "quantile-calibration-0.12.0-report.json"

# `0.11.0` needed two lockbox readings because the support envelope was attached after the first
# one, so the report that promoted the calibration described bytes the bundle did not ship.
# `0.12.0` is written with its envelope already attached, so one reading describes the released
# bytes exactly and there is no earlier reading to keep beside it.
RELEASE_LOCKBOX_REPORT_SOURCE = (
    ARTIFACT_ROOT / "lockbox-conditional-selector-intervals-0.12.0-report.json"
)

DECISION_RECORD = "docs/adr/0009-routing-narrowed-and-recalibrated.md"

ACCEPTED_INPUT_CONTRACT_VERSIONS = ("1.0", "1.1", "1.2")

TRAINING_PROMOTED_STATUS = "PROMOTED"
LOCKBOX_CONFIRMED_STATUS = "RELEASE_CONFIRMED"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy(source: Path, destination: Path) -> str:
    """Copy bytes verbatim and return the digest of what landed.

    ``shutil.copyfile`` is byte-for-byte, so no line-ending translation can change a digest between
    the source tree and the bundle. The digest is taken from the destination on purpose: it is the
    file a deployment will actually verify.

    A CRLF source is refused rather than copied. Every path bundled here is pinned to ``eol=lf`` in
    ``.gitattributes``, so a file that picked up CRLF from a Windows writer would be bundled with a
    digest no other checkout reproduces, and the bundle would fail its own integrity check on the
    first machine that installed it. Normalizing silently would be worse: for the model artifacts
    the exact bytes are load-bearing, because the calibration pins the capacity digest.
    """

    payload = source.read_bytes()
    if CRLF in payload:
        raise ValueError(
            f"{source} contains CRLF line endings, but the repository pins it to eol=lf; "
            "normalize it before bundling or its digest will not survive a checkout"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return _digest(destination)


def _dig(report: Mapping[str, object], source: Path, path: tuple[str, ...]) -> object:
    """Read a nested field, treating an absent one as evidence of the wrong document."""

    node: object = report
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            raise ValueError(
                f"{source.name} has no field {'.'.join(path)}; it is not the promotion evidence "
                "this release requires"
            )
        node = node[key]
    return node


def _check_promotion(
    source: Path,
    *,
    status_at: tuple[str, ...],
    expected_status: str,
    failures_at: tuple[str, ...],
    pins: Mapping[tuple[str, ...], str],
) -> None:
    """Refuse a report unless it records a passing, failure-free run over the bytes shipped."""

    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{source.name} must be a JSON object")

    status = _dig(payload, source, status_at)
    if status != expected_status:
        raise ValueError(
            f"{source.name} records {'.'.join(status_at)}={status!r}, not {expected_status!r}; "
            "a release cannot be assembled from evidence of a run that did not pass"
        )

    failures = _dig(payload, source, failures_at)
    if not isinstance(failures, list):
        raise ValueError(
            f"{source.name} records {'.'.join(failures_at)}={failures!r}, which is not a list of "
            "failures; an unreadable failure list is not an empty one"
        )
    if failures:
        raise ValueError(
            f"{source.name} lists {len(failures)} promotion failure(s) beside its "
            f"{expected_status!r} status: {failures!r}"
        )

    for field, expected in pins.items():
        actual = _dig(payload, source, field)
        if actual != expected:
            raise ValueError(
                f"{source.name} names {'.'.join(field)}={actual!r}, but this bundle ships "
                f"{expected!r}; the evidence describes something other than the bytes being bundled"
            )


def _validate_promotion_evidence(
    capacity: CapacityEstimatorArtifact,
    calibration: ConformalCalibrationArtifact,
    *,
    capacity_digest: str,
    calibration_digest: str,
) -> None:
    """Refuse to bundle model bytes whose evidence does not record a passing release.

    ``_copy`` and the manifest digests prove the bundle holds the bytes this build read. They prove
    nothing about whether those bytes ever passed a gate: a report declaring ``RELEASE_REJECTED``
    with an all-zero digest copies exactly as cleanly as one declaring success. The loader is an
    integrity verifier by contract and deliberately reads no status either, so assembly is the only
    place promotion can be enforced.
    """

    _check_promotion(
        CAPACITY_REPORT_SOURCE,
        status_at=("promotion", "status"),
        expected_status=TRAINING_PROMOTED_STATUS,
        failures_at=("promotion", "failures"),
        pins={
            ("artifact_sha256",): capacity_digest,
            ("model_version",): capacity.model_version,
            ("feature_version",): FEATURE_SET_VERSION,
            ("feature_schema_fingerprint",): FEATURE_SCHEMA_FINGERPRINT,
        },
    )
    _check_promotion(
        CALIBRATION_REPORT_SOURCE,
        status_at=("promotion", "status"),
        expected_status=TRAINING_PROMOTED_STATUS,
        failures_at=("promotion", "failures"),
        pins={
            ("artifact_sha256",): calibration_digest,
            ("calibration_version",): calibration.calibration_version,
            ("capacity_artifact_sha256",): capacity_digest,
            ("capacity_model_version",): capacity.model_version,
        },
    )
    _check_promotion(
        RELEASE_LOCKBOX_REPORT_SOURCE,
        status_at=("status",),
        expected_status=LOCKBOX_CONFIRMED_STATUS,
        failures_at=("failures",),
        pins={
            ("artifact_sha256",): calibration_digest,
            ("calibration_version",): calibration.calibration_version,
            ("capacity_artifact_sha256",): capacity_digest,
            ("capacity_model_version",): capacity.model_version,
        },
    )


def build_bundle(
    output: Path,
    *,
    bundle_id: str,
    bundle_version: str,
    package_version: str,
) -> BundleManifestV1:
    """Write a complete bundle directory and return the manifest that describes it."""

    for source in (
        CAPACITY_SOURCE,
        CALIBRATION_SOURCE,
        CAPACITY_REPORT_SOURCE,
        CALIBRATION_REPORT_SOURCE,
        RELEASE_LOCKBOX_REPORT_SOURCE,
    ):
        if not source.is_file():
            raise FileNotFoundError(f"promoted artifact missing: {source}")

    capacity = CapacityEstimatorArtifact.model_validate(
        json.loads(CAPACITY_SOURCE.read_text(encoding="utf-8"))
    )
    calibration = ConformalCalibrationArtifact.model_validate(
        json.loads(CALIBRATION_SOURCE.read_text(encoding="utf-8"))
    )

    if capacity.feature_version != FEATURE_SET_VERSION:
        raise ValueError(
            f"capacity artifact expects feature set {capacity.feature_version}, "
            f"but this package computes {FEATURE_SET_VERSION}"
        )
    if capacity.feature_schema_fingerprint != FEATURE_SCHEMA_FINGERPRINT:
        raise ValueError(
            "capacity artifact expects feature schema fingerprint "
            f"{capacity.feature_schema_fingerprint}, but this package computes "
            f"{FEATURE_SCHEMA_FINGERPRINT}"
        )
    if calibration.capacity_model_version != capacity.model_version:
        raise ValueError(
            f"calibration {calibration.calibration_version} was fitted against "
            f"{calibration.capacity_model_version}, not {capacity.model_version}"
        )
    capacity_digest = _digest(CAPACITY_SOURCE)
    calibration_digest = _digest(CALIBRATION_SOURCE)
    if calibration.capacity_artifact_sha256 != capacity_digest:
        raise ValueError(
            f"calibration {calibration.calibration_version} pins capacity bytes "
            f"{calibration.capacity_artifact_sha256}, which are not the bytes being bundled"
        )

    _validate_promotion_evidence(
        capacity,
        calibration,
        capacity_digest=capacity_digest,
        calibration_digest=calibration_digest,
    )

    directory = Path(output)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)

    capacity_relative = f"artifacts/{CAPACITY_SOURCE.name}"
    calibration_relative = f"artifacts/{CALIBRATION_SOURCE.name}"
    capacity_report_relative = f"provenance/{CAPACITY_REPORT_SOURCE.name}"
    calibration_report_relative = f"provenance/{CALIBRATION_REPORT_SOURCE.name}"
    release_lockbox_relative = f"provenance/{RELEASE_LOCKBOX_REPORT_SOURCE.name}"

    manifest = BundleManifestV1(
        schema_version=BUNDLE_CONTRACT_VERSION,
        bundle_id=bundle_id,
        bundle_version=bundle_version,
        estimator_package_version=package_version,
        estimator_version=ENSEMBLE_ESTIMATOR_VERSION,
        capacity={
            "path": capacity_relative,
            "version": capacity.model_version,
            "sha256": _copy(CAPACITY_SOURCE, directory / capacity_relative),
        },
        calibration={
            "path": calibration_relative,
            "version": calibration.calibration_version,
            "sha256": _copy(CALIBRATION_SOURCE, directory / calibration_relative),
        },
        feature_set_version=FEATURE_SET_VERSION,
        feature_schema_fingerprint=FEATURE_SCHEMA_FINGERPRINT,
        accepted_input_contract_versions=ACCEPTED_INPUT_CONTRACT_VERSIONS,
        output_contract_version=ESTIMATOR_OUTPUT_CONTRACT_VERSION,
        explanation_contract_version=ESTIMATOR_EXPLANATION_CONTRACT_VERSION,
        support_envelope_version=(
            calibration.support_envelope.schema_version
            if calibration.support_envelope is not None
            else None
        ),
        provenance={
            "capacity_report": {
                "path": capacity_report_relative,
                "sha256": _copy(CAPACITY_REPORT_SOURCE, directory / capacity_report_relative),
            },
            "calibration_report": {
                "path": calibration_report_relative,
                "sha256": _copy(
                    CALIBRATION_REPORT_SOURCE, directory / calibration_report_relative
                ),
            },
            "release_lockbox_report": {
                "path": release_lockbox_relative,
                "sha256": _copy(
                    RELEASE_LOCKBOX_REPORT_SOURCE, directory / release_lockbox_relative
                ),
            },
            "decision_record": DECISION_RECORD,
        },
    )

    (directory / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build-bundle")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1] / "bundles" / "production-0.12.0",
        help="Bundle directory to write; replaced if it already exists",
    )
    parser.add_argument("--bundle-id", default="production-0.12.0")
    parser.add_argument("--bundle-version", default="0.12.0")
    args = parser.parse_args(argv)

    from income_estimator import __version__ as package_version

    manifest = build_bundle(
        args.output,
        bundle_id=args.bundle_id,
        bundle_version=args.bundle_version,
        package_version=package_version,
    )
    digest = hashlib.sha256((Path(args.output) / "manifest.json").read_bytes()).hexdigest()
    print(f"Bundle: {Path(args.output).resolve()}")
    print(f"  bundle_id      {manifest.bundle_id}")
    print(f"  bundle_digest  {digest}")
    print(f"  capacity       {manifest.capacity.version}")
    print(f"  calibration    {manifest.calibration.version}")
    print(f"  feature set    {manifest.feature_set_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
