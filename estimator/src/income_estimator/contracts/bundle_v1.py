"""Deployment bundle contract 1.0: the immutable unit a production estimator is loaded from.

A bundle is a directory. It carries the artifacts the promoted estimator reads, the evidence that
promoted them, and a manifest that pins every one of them by digest. Nothing in it is resolved
relative to this repository, so the same directory works on a machine that has only the wheel.

The manifest exists because the artifacts are not independent. A calibration is a set of offsets on
the residual of one particular capacity model, so a calibration paired with capacity bytes it was
never fitted against keeps its ``p10``/``p90`` label over a quantity nobody measured. The runtime
already refuses that specific pairing; the manifest generalizes it, pinning the feature set the
model was trained against, the input contracts it can read, and the reports that justify it, so a
deployment can be checked as one thing rather than as a pile of files that happen to be nearby.

Every reference is content-addressed. Paths are relative and are conveniences for finding a file;
the SHA-256 is what identifies it. ``capacity-estimator-0.5.0.json`` was once rewritten in place
under an unchanged ``model_version``, which is the reason no version string here is load-bearing on
its own.

The manifest deliberately does not record its own digest. The bundle digest is the SHA-256 of the
manifest bytes, computed at load; because the manifest pins every other file by digest, that one
number covers the whole bundle transitively.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

BUNDLE_CONTRACT_VERSION = "1.0"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BUNDLE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class BundleModel(BaseModel):
    """Strict immutable base for manifest records."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class BundleFileRefV1(BundleModel):
    """One file inside the bundle, identified by digest rather than by name."""

    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        if not _SHA256.match(self.sha256):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        if self.path.startswith("/") or "\\" in self.path:
            raise ValueError("path must be relative and use forward slashes")
        if ".." in self.path.split("/"):
            raise ValueError("path must not escape the bundle directory")
        return self


class BundleArtifactRefV1(BundleFileRefV1):
    """A runtime artifact, which additionally declares the version it claims to be."""

    version: str = Field(min_length=1)


class BundleProvenanceV1(BundleModel):
    """Where the promotion decision for these artifacts is recorded.

    These files are evidence, not runtime inputs: the loader verifies their digests and never reads
    their contents. They travel with the bundle so a deployment can answer "why is this the promoted
    model" without access to the repository that built it.

    Two lockbox readings, because they measure different bytes. ``lockbox_report`` is the reading
    that promoted the calibration; ``release_lockbox_report`` is a reading of the exact artifact
    this bundle ships, which differs from the promoted one by the added support envelope. Only the
    second is a statement about what a deployment will actually run.
    """

    capacity_report: BundleFileRefV1
    calibration_report: BundleFileRefV1
    lockbox_report: BundleFileRefV1 | None = None
    release_lockbox_report: BundleFileRefV1 | None = None
    decision_record: str = Field(min_length=1)


class BundleManifestV1(BundleModel):
    """Bundle contract 1.0.

    ``estimator_package_version`` is a compatibility floor rather than an equality check: a bundle
    built for ``0.11.0`` is expected to keep working under later patch and minor releases of the
    loader, and the loader refuses only a package older than the bundle expects. The feature set is
    the opposite. It is checked exactly, by both name and schema fingerprint, because a capacity
    model reads features positionally by name and a renamed or re-binned feature silently changes
    what the model is scoring.
    """

    schema_version: Literal["1.0"] = "1.0"
    bundle_id: str = Field(min_length=1)
    bundle_version: str = Field(min_length=1)
    estimator_package_version: str = Field(min_length=1)
    estimator_version: str = Field(min_length=1)
    capacity: BundleArtifactRefV1
    calibration: BundleArtifactRefV1
    feature_set_version: str = Field(min_length=1)
    feature_schema_fingerprint: str = Field(min_length=16, max_length=64)
    accepted_input_contract_versions: tuple[str, ...] = Field(min_length=1)
    output_contract_version: str = Field(min_length=1)
    explanation_contract_version: str = Field(min_length=1)
    support_envelope_version: str | None = None
    provenance: BundleProvenanceV1

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if not _BUNDLE_ID.match(self.bundle_id):
            raise ValueError(
                "bundle_id must be lowercase alphanumeric with dots, dashes, or underscores"
            )
        paths = [
            self.capacity.path,
            self.calibration.path,
            self.provenance.capacity_report.path,
            self.provenance.calibration_report.path,
        ]
        for optional in (self.provenance.lockbox_report, self.provenance.release_lockbox_report):
            if optional is not None:
                paths.append(optional.path)
        if len(paths) != len(set(paths)):
            raise ValueError("every bundle file must have a distinct path")
        if self.capacity.sha256 == self.calibration.sha256:
            raise ValueError("capacity and calibration must not be the same file")
        return self

    def files(self) -> tuple[BundleFileRefV1, ...]:
        """Every file the manifest pins, runtime artifacts first."""

        refs: list[BundleFileRefV1] = [
            self.capacity,
            self.calibration,
            self.provenance.capacity_report,
            self.provenance.calibration_report,
        ]
        for optional in (self.provenance.lockbox_report, self.provenance.release_lockbox_report):
            if optional is not None:
                refs.append(optional)
        return tuple(refs)


__all__ = [
    "BUNDLE_CONTRACT_VERSION",
    "BundleArtifactRefV1",
    "BundleFileRefV1",
    "BundleManifestV1",
    "BundleProvenanceV1",
]
