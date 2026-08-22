"""Split-conformal intervals for sustainable monthly income.

The point estimate is a hurdle: a gate decides zero, an anchored regressor sizes the rest. An
interval has to respect both parts.

For a positive prediction the interval is conformal on the log residual. Out-of-fold residuals are
collected from models that never saw the row, their empirical quantiles are frozen into this
artifact, and prediction widens the point estimate by those offsets before back-transforming.
Distribution-free, so nothing assumes the residuals are normal, and they are not.

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

CALIBRATION_METHOD = "split-conformal-log-residual"


class QuantileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConformalCalibrationArtifact(QuantileModel):
    schema_version: Literal["1.0"] = "1.0"
    calibration_version: str = Field(min_length=1)
    method: Literal["split-conformal-log-residual"] = CALIBRATION_METHOD
    capacity_model_version: str = Field(min_length=1)
    out_of_fold_version: str = Field(min_length=1)
    fold_count: int = Field(ge=2)
    nominal_lower_quantile: float = Field(gt=0, lt=1)
    nominal_upper_quantile: float = Field(gt=0, lt=1)
    lower_log_offset: float = Field(le=0)
    upper_log_offset: float = Field(ge=0)
    zero_gate_certain_basis_points: int = Field(ge=0, le=10_000)
    calibration_row_count: int = Field(gt=0)
    calibration_customer_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_quantiles(self) -> ConformalCalibrationArtifact:
        if self.nominal_lower_quantile >= self.nominal_upper_quantile:
            raise ValueError("nominal_lower_quantile must be below nominal_upper_quantile")
        return self

    @property
    def nominal_coverage(self) -> float:
        return self.nominal_upper_quantile - self.nominal_lower_quantile


def empirical_quantile(values: Sequence[float], quantile: float) -> float:
    """Nearest-rank quantile on sorted values; deterministic and free of interpolation choices."""

    if not values:
        raise ValueError("cannot take a quantile of no values")
    ordered = sorted(values)
    index = math.ceil(quantile * len(ordered)) - 1
    return ordered[max(0, min(len(ordered) - 1, index))]


class ConformalIntervalModel:
    """Apply frozen conformal offsets to a point estimate."""

    def __init__(self, artifact: ConformalCalibrationArtifact) -> None:
        self.artifact = artifact

    @classmethod
    def from_path(cls, path: Path) -> ConformalIntervalModel:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(ConformalCalibrationArtifact.model_validate(payload))

    def interval_minor(
        self,
        point_estimate_minor: int,
        *,
        positive_basis_points: int | None = None,
    ) -> tuple[int, int]:
        """Return the lower and upper bound around one point estimate."""

        if point_estimate_minor <= 0:
            certain = (
                positive_basis_points is None
                or positive_basis_points <= self.artifact.zero_gate_certain_basis_points
            )
            if certain:
                return 0, 0
            return 0, self._back_transform(0.0, self.artifact.upper_log_offset)

        anchor = math.log1p(point_estimate_minor)
        return (
            self._back_transform(anchor, self.artifact.lower_log_offset),
            self._back_transform(anchor, self.artifact.upper_log_offset),
        )

    @staticmethod
    def _back_transform(anchor: float, offset: float) -> int:
        value = math.expm1(max(0.0, min(40.0, anchor + offset)))
        return max(0, math.floor(value + 0.5))

    def covers(self, truth_minor: int, bounds: Mapping[str, int]) -> bool:
        return bounds["lower"] <= truth_minor <= bounds["upper"]


__all__ = [
    "CALIBRATION_METHOD",
    "ConformalCalibrationArtifact",
    "ConformalIntervalModel",
    "empirical_quantile",
]
