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


SUPPORT_ENVELOPE_METHOD = "calibration-feature-range"


class FeatureRange(UncertaintyModel):
    """The range one feature took across the calibration population."""

    minimum: float
    maximum: float

    @model_validator(mode="after")
    def validate_range(self) -> FeatureRange:
        if self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self

    def contains(self, value: float) -> bool:
        return self.minimum <= value <= self.maximum


class SupportEnvelopeArtifact(UncertaintyModel):
    """The conditions this calibration is a claim about, and nothing wider.

    An `80%` interval is a statement about the population it was calibrated on. Outside that
    population the corrections were never measured, and the label keeps its wording while losing its
    meaning; on held-out stress conditions earlier calibrations covered as little as `0.49`, with
    nothing at inference time to say so.

    Abstaining is the honest answer there. The output contract already carries a reason for an
    absent quantile, and a refusal a caller can see beats a number that reads as measured and is
    not.

    The envelope is the range each feature took across the calibration population, over the features
    the interval's width actually depends on: the ones the residual quantile ensembles split on,
    plus the selector's conditioner. Extrapolating those is exactly where the claim has no
    support.
    Features outside that set are not fenced, because the interval does not condition on them.

    A missing value is in support. Missingness is modelled rather than imputed everywhere else
    here, and calibration saw plenty of it; an absent feature is a condition the calibration
    covers, not an extrapolation beyond it.
    """

    schema_version: Literal["1.0"] = "1.0"
    method: Literal["calibration-feature-range"] = SUPPORT_ENVELOPE_METHOD
    ranges: dict[str, FeatureRange]
    calibration_row_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_ranges(self) -> SupportEnvelopeArtifact:
        if not self.ranges:
            raise ValueError("an envelope that fences nothing declares no support")
        return self

    def unsupported(self, features: Mapping[str, float | int | None]) -> tuple[str, ...]:
        """Which fenced features this row falls outside. Empty means in support."""

        outside = []
        for name, allowed in self.ranges.items():
            value = features.get(name)
            if value is not None and not allowed.contains(float(value)):
                outside.append(name)
        return tuple(sorted(outside))


CONDITIONAL_SELECTOR_METHOD = "cell-selector-fixed-or-adaptive"


class CellPolicy(UncertaintyModel):
    """What one cell does: which band it starts from, and how each tail is corrected."""

    branch: Literal["adaptive", "fixed"]
    lower_adjustment: float
    upper_adjustment: float
    score_count: int = Field(gt=0)


class ConditionalSelectorArtifact(UncertaintyModel):
    """Choose the learned band or the fixed band per cell, then correct each tail on its own.

    The width recalibrator failed because the regimes overlap in raw width: at the same learned
    width an income-diverse row needs a wider interval and a stable one needs a narrower one, and a
    monotone function of that width cannot tell them apart. This conditions on a feature that can.

    The conditioner is pre-registered. It was ranked inside the uncertainty-training population,
    against a criterion evaluated on customer splits of that population alone, and the record of
    what was chosen and what it beat is frozen in `conditioner-preregistration.json` before this
    artifact was fitted. Ranking features against the population the gate measures was tried first
    and is disqualifying: it selects a model on the data that is supposed to test it.

    Cells are quartiles of the conditioner crossed with the confidence band. Each cell picks its
    branch on out-of-fold uncertainty-training rows, by corrected width after both branches are
    corrected to hold their tails, so the comparison is like with like and the narrower one wins.
    Each cell's corrections are then fitted on calibration customers, which is the split-conformal
    step, unchanged in method and only finer in partition.

    A cell too thin to fit its own pair falls back rather than inventing one, and a calibration that
    needs the fallback anywhere cannot promote.
    """

    schema_version: Literal["1.0"] = "1.0"
    method: Literal["cell-selector-fixed-or-adaptive"] = CONDITIONAL_SELECTOR_METHOD
    feature_name: str = Field(min_length=1)
    cut_points: tuple[float, ...] = Field(min_length=1)
    applies_to_bands: tuple[str, ...] = WIDTH_RECALIBRATED_BANDS
    cells: dict[str, CellPolicy]
    fallback: CellPolicy | None = None
    selection_version: str = Field(min_length=1)
    selected_on: str = Field(min_length=1)
    preregistration_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_cells(self) -> ConditionalSelectorArtifact:
        if not self.applies_to_bands:
            raise ValueError("a selector that applies to no band selects nothing")
        unknown = set(self.applies_to_bands) - {"high", "medium", "low"}
        if unknown:
            raise ValueError(f"unknown confidence bands: {sorted(unknown)}")
        outside = sorted(
            name
            for name in self.cells
            if name.rsplit("/", 1)[-1] not in self.applies_to_bands
        )
        if outside:
            raise ValueError(f"cells outside the selector's bands: {outside}")
        if list(self.cut_points) != sorted(self.cut_points):
            raise ValueError("cut_points must be ascending")
        if len(set(self.cut_points)) != len(self.cut_points):
            raise ValueError("cut_points must be distinct")
        if not self.cells:
            raise ValueError("a selector with no cells selects nothing")
        return self

    def bucket(self, features: Mapping[str, float | int | None]) -> str:
        """Name the conditioner quartile a row falls in. Missing is its own bucket.

        A row with no value for the conditioner cannot be placed among the quartiles, and folding it
        into the lowest would silently assign it a correction fitted on rows it has nothing in
        common with.
        """

        value = features.get(self.feature_name)
        if value is None:
            return "unknown"
        return f"q{sum(1 for cut in self.cut_points if float(value) > cut) + 1}"

    def applies_to(self, band: str | None) -> bool:
        """Whether one band is selected over at all.

        The `low` band holds both its tails already, at `0.1091` and `0.0922` against `0.10`, and is
        the one part of the model that is not the problem. It keeps its band-level correction
        untouched, so its published intervals are byte-identical with the selector present or
        absent.
        """

        return band is not None and band in self.applies_to_bands

    def policy_for(
        self,
        features: Mapping[str, float | int | None],
        band: str,
    ) -> CellPolicy | None:
        """This row's cell, or the fallback, or nothing when the band is not selected over."""

        if not self.applies_to(band):
            return None
        return self.cells.get(f"{self.bucket(features)}/{band}", self.fallback)

    @property
    def uses_fallback(self) -> tuple[str, ...]:
        """Cells that carry the fallback rather than their own fitted pair."""

        return tuple(sorted(name for name, cell in self.cells.items() if cell is self.fallback))


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
    "CONDITIONAL_SELECTOR_METHOD",
    "SUPPORT_ENVELOPE_METHOD",
    "MAXIMUM_LOG_OFFSET",
    "RESIDUAL_QUANTILE_METHOD",
    "WIDTH_RECALIBRATED_BANDS",
    "WIDTH_RECALIBRATION_METHOD",
    "CellPolicy",
    "FeatureRange",
    "SupportEnvelopeArtifact",
    "ConditionalSelectorArtifact",
    "ResidualQuantileArtifact",
    "ResidualQuantileModel",
    "ScaleStump",
    "WidthRecalibratorArtifact",
]
