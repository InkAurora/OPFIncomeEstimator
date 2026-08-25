"""Per-row residual quantiles for conformalized quantile regression.

Fixed offsets, whether global or per confidence band, give every row the same interval width in log
space. ADR 0006 records why that fails: `income_diverse` covers roughly half its nominal rate while
`life_events` covers everything, and the confidence score does not separate them.

A single predicted error *scale* was measured and rejected. It tracked the level well, spreading
predictions 55x across suites in the right order, but squared error on the log absolute residual
predicts a geometric mean and is biased low exactly where the tail is heavy. On `income_diverse` the
realized residual ran 1.5x the prediction at the median and 3.4x at the 90th percentile, so
normalizing by it left the same suite under-covered. A scale cannot correct the shape of a
distribution, only its level.

This model predicts the lower and upper residual quantiles directly, one boosted stump ensemble
each, fitted under pinball loss. The conformal step then corrects both tails using untouched
customers, which is what recovers the coverage the learned quantiles do not carry on their own.

ADR 0007. That correction is empirical, not a finite-sample guarantee. The conformity scores are
customer-months, roughly twelve correlated rows per customer, so the finite-sample rank adjustment
is computed over a sample whose effective size is far below its count. Until the conformal unit is
the customer, this is customer-disjoint calibration with customer-clustered error bars, and no
document may claim more.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RESIDUAL_QUANTILE_METHOD = "boosted-stumps-pinball-residual-quantiles"

# The learned bounds are log-space offsets, so a bound beyond this would place the interval several
# orders of magnitude away from the point estimate whatever the conformal widening does.
MAXIMUM_LOG_OFFSET = 4.0


class UncertaintyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScaleStump(UncertaintyModel):
    feature_name: str = Field(min_length=1)
    threshold: float
    missing_left: bool
    left_value: float
    right_value: float


class ResidualQuantileArtifact(UncertaintyModel):
    """Learned lower and upper residual quantiles, before conformal widening."""

    schema_version: Literal["1.0"] = "1.0"
    model_version: str = Field(min_length=1)
    method: Literal["boosted-stumps-pinball-residual-quantiles"] = RESIDUAL_QUANTILE_METHOD
    feature_version: str = Field(min_length=1)
    target: Literal["log_residual"] = "log_residual"
    lower_quantile: float = Field(gt=0, lt=1)
    upper_quantile: float = Field(gt=0, lt=1)
    lower_base_score: float
    upper_base_score: float
    learning_rate: float = Field(gt=0)
    lower_trees: tuple[ScaleStump, ...] = ()
    upper_trees: tuple[ScaleStump, ...] = ()
    training_row_count: int = Field(gt=0)
    training_customer_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_trees(self) -> ResidualQuantileArtifact:
        if not self.lower_trees or not self.upper_trees:
            raise ValueError("both residual quantile ensembles need at least one stump")
        if self.lower_quantile >= self.upper_quantile:
            raise ValueError("lower_quantile must be below upper_quantile")
        return self


WIDTH_RECALIBRATION_METHOD = "tail-power-width-recalibration"

WIDTH_RECALIBRATED_BANDS: tuple[str, ...] = ("high", "medium")


class WidthRecalibratorArtifact(UncertaintyModel):
    """One monotone power transform per tail of the learned band.

    `corrected = scale * raw ** slope`, on each tail's excursion from the point estimate. Measured
    on the validation population, the learned band is miscalibrated in slope rather than level: it
    is `7.4x` the fixed-band width in its widest quartile, which covered `0.943` against a nominal
    `0.80`, and no wider than fixed-band in its narrowest, which missed `p90` on `0.308` of rows. A
    constant would move both the same way and fix neither.

    A `slope` below one compresses the range: the ratio `corrected / raw` is `scale * raw ** (slope
    - 1)`, decreasing in `raw`, so narrow bands are enlarged and extreme ones pulled in. `slope = 1`
    is a pure rescale and `slope = 0` collapses every band to one width, which is why it is bounded
    to `[0, 1]` rather than fitted freely: outside that range the transform stops being a
    compression and starts being an expansion of exactly the defect it exists to remove.

    Each tail has its own pair. The two tails fail in opposite directions on the suite that drove
    this, and one shared pair cannot express that.

    The transform applies to the `high` and `medium` bands only. The `low` band reaches its floor
    and both its tails on its own, at `0.7987` coverage with `0.1091` and `0.0922` misses; it is
    the one part of the model that is already right, and it bypasses the transform exactly.
    """

    schema_version: Literal["1.0"] = "1.0"
    method: Literal["tail-power-width-recalibration"] = WIDTH_RECALIBRATION_METHOD
    lower_scale: float = Field(gt=0)
    lower_slope: float = Field(ge=0, le=1)
    upper_scale: float = Field(gt=0)
    upper_slope: float = Field(ge=0, le=1)
    applies_to_bands: tuple[str, ...] = WIDTH_RECALIBRATED_BANDS
    fold_count: int = Field(ge=2)
    training_row_count: int = Field(gt=0)
    training_customer_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_bands(self) -> WidthRecalibratorArtifact:
        if not self.applies_to_bands:
            raise ValueError("a width recalibrator that applies to no band is not a calibration")
        unknown = set(self.applies_to_bands) - {"high", "medium", "low"}
        if unknown:
            raise ValueError(f"unknown confidence bands: {sorted(unknown)}")
        return self

    def applies_to(self, band: str | None) -> bool:
        """Whether one band's learned width passes through the transform.

        A caller with no band is answered as if untransformed. It cannot be placed in `high` or
        `medium`, and silently applying a correction fitted on those two to a row whose band is
        unknown would be a claim nothing measured.
        """

        return band is not None and band in self.applies_to_bands

    def recalibrate(self, lower: float, upper: float) -> tuple[float, float]:
        """Transform one row's learned log band, tail by tail.

        Each tail is measured as its excursion from zero, so the band still brackets the point
        estimate and a tail that had no width keeps none. Clamped to the same maximum offset the
        quantile model itself is clamped to.
        """

        return (
            -_power(max(0.0, -lower), self.lower_scale, self.lower_slope),
            _power(max(0.0, upper), self.upper_scale, self.upper_slope),
        )


def _power(width: float, scale: float, slope: float) -> float:
    """`scale * width ** slope`, with no width staying no width.

    `0 ** 0` is `1` in IEEE arithmetic, which would turn a degenerate tail into a full-width one at
    `slope = 0`. A tail with no excursion has nothing to recalibrate.
    """

    if width <= 0:
        return 0.0
    return min(MAXIMUM_LOG_OFFSET, scale * width**slope)


class ResidualQuantileModel:
    """Predict the log-space residual band for one row."""

    def __init__(self, artifact: ResidualQuantileArtifact) -> None:
        self.artifact = artifact

    @staticmethod
    def _leaf(tree: ScaleStump, value: float | int | None) -> float:
        if value is None:
            return tree.left_value if tree.missing_left else tree.right_value
        return tree.left_value if float(value) <= tree.threshold else tree.right_value

    def _predict(
        self,
        trees: tuple[ScaleStump, ...],
        base: float,
        features: Mapping[str, float | int | None],
    ) -> float:
        score = base
        for tree in trees:
            score += self.artifact.learning_rate * self._leaf(
                tree, features.get(tree.feature_name)
            )
        return max(-MAXIMUM_LOG_OFFSET, min(MAXIMUM_LOG_OFFSET, score))

    def predict_bounds(
        self,
        features: Mapping[str, float | int | None],
    ) -> tuple[float, float]:
        """Return the learned lower and upper log-residual offsets, ordered."""

        lower = self._predict(
            self.artifact.lower_trees, self.artifact.lower_base_score, features
        )
        upper = self._predict(
            self.artifact.upper_trees, self.artifact.upper_base_score, features
        )
        # Two independently fitted quantile ensembles can cross on a row neither saw often. The
        # band collapses to a point there rather than inverting.
        if lower > upper:
            middle = (lower + upper) / 2
            return middle, middle
        return lower, upper


__all__ = [
    "MAXIMUM_LOG_OFFSET",
    "RESIDUAL_QUANTILE_METHOD",
    "WIDTH_RECALIBRATED_BANDS",
    "WIDTH_RECALIBRATION_METHOD",
    "ResidualQuantileArtifact",
    "ResidualQuantileModel",
    "ScaleStump",
    "WidthRecalibratorArtifact",
]
