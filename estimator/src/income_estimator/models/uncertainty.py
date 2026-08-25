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
    "ResidualQuantileArtifact",
    "ResidualQuantileModel",
    "ScaleStump",
]
