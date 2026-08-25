"""Split-conformal intervals for sustainable monthly income.

The point estimate is a hurdle: a gate decides zero, an anchored regressor sizes the rest. An
interval has to respect both parts.

For a positive prediction the interval is conformal on the log residual. Residuals are collected
from a frozen capacity model on a customer-disjoint calibration population, not from folds: the
point model is never refit here, and the separation that makes the offsets honest is that no
calibration customer appears in the population the capacity model or the quantile model was fitted
on. Their empirical quantiles are frozen into this artifact, and prediction widens the point
estimate by those offsets before back-transforming. Distribution-free, so nothing assumes the
residuals are normal, and they are not.

The `out_of_fold_version` and `fold_count` fields on the artifact are vestigial names from the
k-fold scheme this replaced; `out_of_fold_version` now records which disjoint-population protocol
produced the residuals.

For a predicted zero the interval is not a widened point estimate. When the gate is confident the
answer is `[0, 0]`, which is a claim the evaluation can falsify. When the gate is unsure the upper
bound comes from the positive branch, because the honest statement is "probably nothing, but if
something, about this much". Reporting a symmetric band around zero would imply negative income
below and false precision above.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from income_estimator.models.uncertainty import (
    ResidualQuantileArtifact,
    ResidualQuantileModel,
)

CALIBRATION_METHOD = "split-conformal-log-residual"


CONFIDENCE_BAND_FLOORS: tuple[tuple[str, int], ...] = (
    ("high", 7_000),
    ("medium", 5_000),
    ("low", 0),
)


def confidence_band(confidence_basis_points: int) -> str:
    """Name the band a published confidence score falls in."""

    for name, floor in CONFIDENCE_BAND_FLOORS:
        if confidence_basis_points >= floor:
            return name
    return "low"


class QuantileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BandOffsets(QuantileModel):
    """Conformal offsets fitted on one confidence band's own residuals."""

    lower_log_offset: float = Field(le=0)
    upper_log_offset: float = Field(ge=0)
    residual_count: int = Field(gt=0)


class BandAdjustment(QuantileModel):
    """One band's asymmetric conformal correction to the learned quantile pair.

    ADR 0007. Each tail is corrected separately, at its own `0.90` finite-sample quantile, so the
    artifact carries `p10` and `p90` semantics rather than a joint `80%` claim that either tail may
    be paying for. A correction is negative when the learned band was already too wide there.
    """

    lower_adjustment: float
    upper_adjustment: float
    score_count: int = Field(gt=0)


class ConformalCalibrationArtifact(QuantileModel):
    schema_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    calibration_version: str = Field(min_length=1)
    method: Literal["split-conformal-log-residual"] = CALIBRATION_METHOD
    capacity_model_version: str = Field(min_length=1)
    capacity_artifact_sha256: str = Field(min_length=64, max_length=64)
    out_of_fold_version: str = Field(min_length=1)
    fold_count: int = Field(ge=2)
    nominal_lower_quantile: float = Field(gt=0, lt=1)
    nominal_upper_quantile: float = Field(gt=0, lt=1)
    lower_log_offset: float = Field(le=0)
    upper_log_offset: float = Field(ge=0)
    band_offsets: dict[str, BandOffsets] = Field(default_factory=dict)
    published_bands: tuple[str, ...] = ()
    residual_quantiles: ResidualQuantileArtifact | None = None
    conformal_widening: float | None = None
    band_adjustments: dict[str, BandAdjustment] = Field(default_factory=dict)
    zero_gate_certain_basis_points: int = Field(ge=0, le=10_000)
    zero_mass_floor_basis_points: int = Field(default=1_000, ge=0, le=10_000)
    calibration_row_count: int = Field(gt=0)
    calibration_customer_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_quantiles(self) -> ConformalCalibrationArtifact:
        if self.nominal_lower_quantile >= self.nominal_upper_quantile:
            raise ValueError("nominal_lower_quantile must be below nominal_upper_quantile")
        known = {name for name, _ in CONFIDENCE_BAND_FLOORS}
        unknown = set(self.band_offsets) - known
        if unknown:
            raise ValueError(f"unknown confidence bands in band_offsets: {sorted(unknown)}")
        unknown_published = set(self.published_bands) - known
        if unknown_published:
            raise ValueError(
                f"unknown confidence bands in published_bands: {sorted(unknown_published)}"
            )
        adaptive = (self.residual_quantiles, self.conformal_widening)
        if any(part is not None for part in adaptive) and any(part is None for part in adaptive):
            raise ValueError(
                "adaptive calibration needs residual quantiles and a conformal widening"
            )
        unknown_adjusted = set(self.band_adjustments) - known
        if unknown_adjusted:
            raise ValueError(
                f"unknown confidence bands in band_adjustments: {sorted(unknown_adjusted)}"
            )
        if self.band_adjustments and self.residual_quantiles is None:
            raise ValueError("band adjustments correct learned quantiles and need them present")
        return self

    @property
    def is_adaptive(self) -> bool:
        return self.residual_quantiles is not None

    @property
    def nominal_coverage(self) -> float:
        return self.nominal_upper_quantile - self.nominal_lower_quantile

    def publishes(self, confidence_basis_points: int | None) -> bool:
        """Whether an interval may be published for this score.

        ADR 0005 withholds bands whose measured coverage does not hold. An interval labelled `80%`
        that contains the truth far less often is worse than no interval, and the output contract
        already carries a reason for an absent quantile.

        A caller that supplies no score is answered from the global offsets, which is the schema 1.0
        behavior and stays available. An artifact that names no published bands publishes all of
        them, so an older artifact keeps working.
        """

        if confidence_basis_points is None or not self.published_bands:
            return True
        return confidence_band(confidence_basis_points) in self.published_bands

    def offsets_for(self, confidence_basis_points: int | None) -> tuple[float, float]:
        """Return the offset pair for a score, falling back to the global pair.

        A caller that supplies no score, and a band too thin to have been fitted, both land on the
        global offsets. That keeps a reader that knows nothing about bands working exactly as it did
        under schema 1.0.
        """

        if confidence_basis_points is None:
            return self.lower_log_offset, self.upper_log_offset
        band = self.band_offsets.get(confidence_band(confidence_basis_points))
        if band is None:
            return self.lower_log_offset, self.upper_log_offset
        return band.lower_log_offset, band.upper_log_offset

    def adjustments_for(self, confidence_basis_points: int | None) -> tuple[float, float]:
        """Return this score's lower and upper conformal corrections.

        ADR 0007. A single widening applied to both tails of every band is dominated by whichever
        band and tail carry the most mass: high and medium over-covered, and the one constant they
        chose shrank the low band that was already missing its floor. Each band corrects each tail
        on its own scores instead.

        A band with no fitted pair, and a reader that supplies no score, fall back to the single
        widening. That keeps a schema 1.1 artifact behaving exactly as it did, and it is the reason
        the widening stays in the artifact. Calibration refuses to promote on that fallback: it is a
        safety valve for a thin band, not a calibration.
        """

        widening = self.conformal_widening or 0.0
        if confidence_basis_points is None:
            return widening, widening
        adjustment = self.band_adjustments.get(confidence_band(confidence_basis_points))
        if adjustment is None:
            return widening, widening
        return adjustment.lower_adjustment, adjustment.upper_adjustment


def empirical_quantile(values: Sequence[float], quantile: float) -> float:
    """Nearest-rank quantile on sorted values; deterministic and free of interpolation choices."""

    if not values:
        raise ValueError("cannot take a quantile of no values")
    ordered = sorted(values)
    index = math.ceil(quantile * len(ordered)) - 1
    return ordered[max(0, min(len(ordered) - 1, index))]


class CalibrationBindingError(ValueError):
    """A calibration artifact applied to capacity bytes it was not fitted against.

    Every offset in the artifact is a claim about the residual of one particular point estimate.
    Change the model that produces that estimate and the offsets no longer describe anything
    measured; the interval keeps its `p10`/`p90` label and loses its meaning. Loading the two
    artifacts independently made that failure silent, so the binding is checked at construction.
    """


def require_capacity_binding(
    artifact: ConformalCalibrationArtifact,
    *,
    capacity_model_version: str | None,
    capacity_artifact_sha256: str | None,
) -> None:
    """Fail unless the capacity model in hand is the one the calibration was fitted against.

    Both halves are checked. The version catches the wrong model; the digest catches the same
    version rewritten in place, which has already happened once in this repository.

    A calibration with no capacity model at all is also a mismatch. Without it the estimate falls
    back to the recurring-stream component, and the residuals these offsets were fitted on were
    never taken around that number.
    """

    if capacity_model_version is None or capacity_artifact_sha256 is None:
        raise CalibrationBindingError(
            f"calibration {artifact.calibration_version} was fitted against capacity "
            f"{artifact.capacity_model_version} and cannot be applied without it"
        )
    if capacity_model_version != artifact.capacity_model_version:
        raise CalibrationBindingError(
            f"calibration {artifact.calibration_version} was fitted against capacity "
            f"{artifact.capacity_model_version}, not {capacity_model_version}"
        )
    if capacity_artifact_sha256 != artifact.capacity_artifact_sha256:
        raise CalibrationBindingError(
            f"calibration {artifact.calibration_version} was fitted against capacity "
            f"{artifact.capacity_model_version} with sha256 "
            f"{artifact.capacity_artifact_sha256}, but the artifact in hand hashes to "
            f"{capacity_artifact_sha256}"
        )


class ConformalIntervalModel:
    """Apply frozen conformal offsets to a point estimate."""

    def __init__(self, artifact: ConformalCalibrationArtifact) -> None:
        self.artifact = artifact
        self.residual_quantiles = (
            ResidualQuantileModel(artifact.residual_quantiles)
            if artifact.residual_quantiles is not None
            else None
        )

    def _offsets(
        self,
        confidence_basis_points: int | None,
        features: Mapping[str, float | int | None] | None,
    ) -> tuple[float, float]:
        """Offsets for one row: adaptive when a scale model and features are both available.

        The learned bounds are this row's own residual quantiles; the conformal corrections are
        fitted on untouched customers and recover the coverage the learned quantiles do not carry
        on their own. Each tail is corrected separately, per band, so the lower bound is a `p10`
        claim and the upper bound a `p90` claim rather than two halves of one `80%` claim.

        ADR 0007. That recovery is empirical, not a finite-sample guarantee: the scores behind it
        are correlated customer-months rather than independent customers.

        Clamping to `min(0, ...)` and `max(0, ...)` keeps the interval bracketing the point
        estimate: a correction may narrow a tail but never move it past `p50`. Without features
        there is nothing to condition on, and the fixed offsets apply.
        """

        if self.residual_quantiles is not None and features is not None:
            lower, upper = self.residual_quantiles.predict_bounds(features)
            lower_adjustment, upper_adjustment = self.artifact.adjustments_for(
                confidence_basis_points
            )
            return min(0.0, lower - lower_adjustment), max(0.0, upper + upper_adjustment)
        return self.artifact.offsets_for(confidence_basis_points)

    @classmethod
    def from_path(cls, path: Path) -> ConformalIntervalModel:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(ConformalCalibrationArtifact.model_validate(payload))

    def interval_minor(
        self,
        point_estimate_minor: int,
        *,
        positive_basis_points: int | None = None,
        confidence_basis_points: int | None = None,
        features: Mapping[str, float | int | None] | None = None,
    ) -> tuple[int, int] | None:
        """Return the lower and upper bound around one point estimate.

        With a residual scale model and this row's features, the width is conditioned on how large
        an error the model expects here. Otherwise the confidence score selects a band's offsets,
        and without that the global pair applies, which is the schema 1.0 behavior. `None` means
        this artifact does not publish an interval for that band.
        """

        if not self.artifact.publishes(confidence_basis_points):
            return None
        lower_offset, upper_offset = self._offsets(confidence_basis_points, features)
        if point_estimate_minor <= 0:
            certain = (
                positive_basis_points is None
                or positive_basis_points <= self.artifact.zero_gate_certain_basis_points
            )
            if certain:
                return 0, 0
            return 0, self._back_transform(0.0, upper_offset)

        anchor = math.log1p(point_estimate_minor)
        upper = self._back_transform(anchor, upper_offset)
        # ADR 0006. The estimate is a hurdle, so the interval has to be able to say "probably
        # something, possibly nothing". A log-space band around a positive point is strictly
        # positive and excludes zero categorically, however unsure the gate was. When the gate
        # leaves meaningful mass on zero, the lower bound is zero.
        if (
            positive_basis_points is not None
            and 10_000 - positive_basis_points >= self.artifact.zero_mass_floor_basis_points
        ):
            return 0, upper
        return self._back_transform(anchor, lower_offset), upper

    @staticmethod
    def _back_transform(anchor: float, offset: float) -> int:
        value = math.expm1(max(0.0, min(40.0, anchor + offset)))
        return max(0, math.floor(value + 0.5))

    def covers(self, truth_minor: int, bounds: Mapping[str, int]) -> bool:
        return bounds["lower"] <= truth_minor <= bounds["upper"]


__all__ = [
    "CALIBRATION_METHOD",
    "CONFIDENCE_BAND_FLOORS",
    "BandAdjustment",
    "BandOffsets",
    "ConformalCalibrationArtifact",
    "ConformalIntervalModel",
    "confidence_band",
    "empirical_quantile",
]
