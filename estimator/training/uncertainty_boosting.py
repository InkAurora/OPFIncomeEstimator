"""Fit the residual quantile pair that ADR 0006 puts in place of fixed conformal offsets.

Two stump ensembles are boosted under pinball loss, one for the lower residual quantile and one for
the upper. Pinball loss is what makes them quantiles rather than means, and a mean is precisely what
failed: a squared-error fit on the log absolute residual predicted a geometric mean, which sits far
below the tail on the suite that needed widening most.

Rows come from the uncertainty-training population, which is customer-disjoint from the population
that trained the capacity model and from the one that conformalizes this model's output.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from income_estimator.features.schema import FEATURE_NAMES, FEATURE_SET_VERSION
from income_estimator.models.uncertainty import (
    RESIDUAL_QUANTILE_METHOD,
    WIDTH_RECALIBRATED_BANDS,
    ResidualQuantileArtifact,
    ScaleStump,
    WidthRecalibratorArtifact,
)
from training.capacity_boosting import _bin_edges, _binned, _fit_stumps
from training.capacity_datasets import CapacityRow

RESIDUAL_QUANTILE_VERSION = "residual-quantiles-pinball-0.9.0"


@dataclass(frozen=True, slots=True)
class ResidualRow:
    """One row's features and the log residual the routed estimate actually made on it."""

    customer_id: str
    reference_month: str
    features: dict[str, float | int | None]
    log_residual: float


def _pinball_gradient_hessian(targets: list[float], quantile: float):
    """Boost toward a quantile rather than a mean.

    The gradient of pinball loss is constant on each side of the current prediction, `q` when the
    truth is above it and `q - 1` when below, so the ensemble settles where that fraction of the
    mass lies. The hessian is taken as one, which makes the leaf value a scaled gradient average and
    is the usual treatment for a loss whose second derivative is zero almost everywhere.
    """

    def gradient_hessian(scores: list[float]):
        gradients = [
            quantile if target > score else quantile - 1.0
            for target, score in zip(targets, scores)
        ]
        return gradients, [1.0] * len(scores)

    return gradient_hessian


def _empirical_quantile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    index = math.ceil(quantile * len(ordered)) - 1
    return ordered[max(0, min(len(ordered) - 1, index))]


def fit_residual_quantile_model(
    rows: Sequence[ResidualRow],
    *,
    lower_quantile: float = 0.1,
    upper_quantile: float = 0.9,
    rounds: int = 200,
    learning_rate: float = 0.2,
    l2_regularization: float = 1.0,
    minimum_leaf_size: int = 30,
    maximum_bins: int = 32,
) -> ResidualQuantileArtifact:
    """Fit both residual quantiles with the same stump fitter the capacity model uses."""

    if not rows:
        raise ValueError("no residual rows to fit residual quantiles on")

    edges_by_feature = {
        name: _bin_edges([row.features.get(name) for row in rows], maximum_bins)
        for name in FEATURE_NAMES
    }
    bins_by_feature = {
        name: [_binned(row.features.get(name), edges_by_feature[name]) for row in rows]
        for name in FEATURE_NAMES
    }
    targets = [row.log_residual for row in rows]

    def _fit(quantile: float) -> tuple[list[ScaleStump], float]:
        base = _empirical_quantile(targets, quantile)
        trees, _ = _fit_stumps(
            tuple(rows),
            edges_by_feature,
            bins_by_feature,
            initial_scores=[base] * len(rows),
            gradient_hessian=_pinball_gradient_hessian(targets, quantile),
            rounds=rounds,
            learning_rate=learning_rate,
            l2_regularization=l2_regularization,
            minimum_leaf_size=minimum_leaf_size,
        )
        return [
            ScaleStump(
                feature_name=tree.feature_name,
                threshold=tree.threshold,
                missing_left=tree.missing_left,
                left_value=tree.left_value,
                right_value=tree.right_value,
            )
            for tree in trees
        ], base

    lower_trees, lower_base = _fit(lower_quantile)
    upper_trees, upper_base = _fit(upper_quantile)
    return ResidualQuantileArtifact(
        model_version=RESIDUAL_QUANTILE_VERSION,
        method=RESIDUAL_QUANTILE_METHOD,
        feature_version=FEATURE_SET_VERSION,
        lower_quantile=lower_quantile,
        upper_quantile=upper_quantile,
        lower_base_score=round(lower_base, 12),
        upper_base_score=round(upper_base, 12),
        learning_rate=learning_rate,
        lower_trees=tuple(lower_trees),
        upper_trees=tuple(upper_trees),
        training_row_count=len(rows),
        training_customer_count=len({row.customer_id for row in rows}),
    )


def residual_rows(rows: Sequence[CapacityRow], routed) -> tuple[ResidualRow, ...]:
    """Pair each positive-truth row with the log residual of its routed estimate."""

    result: list[ResidualRow] = []
    for row in rows:
        truth = row.sustainable_monthly_income_minor
        if truth <= 0:
            continue
        point = routed(row)
        if point is None:
            continue
        result.append(
            ResidualRow(
                customer_id=row.customer_id,
                reference_month=row.reference_month,
                features=row.features,
                log_residual=math.log1p(truth) - math.log1p(max(0, point)),
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class WidthObservation:
    """One out-of-fold learned band and the residual it was supposed to bracket."""

    customer_id: str
    band: str
    raw_lower: float
    raw_upper: float
    log_residual: float


# Slope is searched on a grid rather than by an optimizer: two parameters per tail, one of them
# solved exactly, and a deterministic artifact matters more here than the last decimal of a fit.
_SLOPE_COARSE_STEP = 0.02
_SLOPE_FINE_STEP = 0.002


def _pinball(targets: Sequence[float], predictions: Sequence[float], quantile: float) -> float:
    total = 0.0
    for target, prediction in zip(targets, predictions):
        error = target - prediction
        total += quantile * error if error > 0 else (quantile - 1.0) * error
    return total / len(targets) if targets else 0.0


def _weighted_quantile(pairs: Sequence[tuple[float, float]], quantile: float) -> float:
    """The value at which `quantile` of the total weight lies below. Pairs are `(value, weight)`."""

    ordered = sorted(pairs)
    total = sum(weight for _, weight in ordered)
    if total <= 0:
        return 0.0
    target = quantile * total
    seen = 0.0
    for value, weight in ordered:
        seen += weight
        if seen >= target:
            return value
    return ordered[-1][0]


def _fit_tail(
    widths: Sequence[float],
    targets: Sequence[float],
    quantile: float,
) -> tuple[float, float, float]:
    """Best `(scale, slope, loss)` for `scale * width ** slope` under pinball loss.

    For a fixed slope the prediction is linear in `scale` and the loss is convex and piecewise
    linear, so the optimum sits exactly at the weighted quantile of the ratios `target / width`
    weighted by `width`. That is quantile regression through the origin, solved in closed form, and
    it leaves only the slope to search. Rows whose tail has no width carry no weight in that
    solution, because no scale changes their prediction, but they still count in the loss.
    """

    best: tuple[float, float, float] | None = None

    def evaluate(slope: float) -> tuple[float, float, float]:
        powered = [width**slope if width > 0 else 0.0 for width in widths]
        scale = _weighted_quantile(
            [
                (target / power, power)
                for target, power in zip(targets, powered)
                if power > 0
            ],
            quantile,
        )
        scale = max(1e-9, scale)
        loss = _pinball(targets, [scale * power for power in powered], quantile)
        return scale, slope, loss

    steps = int(round(1.0 / _SLOPE_COARSE_STEP))
    for index in range(steps + 1):
        candidate = evaluate(round(index * _SLOPE_COARSE_STEP, 6))
        if best is None or candidate[2] < best[2]:
            best = candidate
    assert best is not None
    centre = best[1]
    fine = int(round(_SLOPE_COARSE_STEP / _SLOPE_FINE_STEP))
    for offset in range(-fine, fine + 1):
        slope = round(centre + offset * _SLOPE_FINE_STEP, 6)
        if not 0.0 <= slope <= 1.0:
            continue
        candidate = evaluate(slope)
        if candidate[2] < best[2]:
            best = candidate
    return best


def fit_width_recalibrator(
    observations: Sequence[WidthObservation],
    *,
    lower_quantile: float,
    upper_quantile: float,
    fold_count: int,
    bands: Sequence[str] = WIDTH_RECALIBRATED_BANDS,
) -> WidthRecalibratorArtifact:
    """Fit one monotone power transform per tail, on out-of-fold learned bands.

    Out-of-fold is what makes this honest. A transform fitted on bands the quantile model produced
    for rows it had trained on would be correcting that model's optimism about itself, and would
    understate every width it needs to enlarge.

    Only the bands the transform will apply to are fitted on. Including `low` rows would let the one
    band that already holds both its tails pull the parameters that govern the two that do not.

    Each tail is fitted to be its own quantile of the residual. The upper bound answers `p90` and
    the lower bound `p10`, taken as a `p90` of the negated residual so both tails use one fitter.
    """

    selected = [item for item in observations if item.band in set(bands)]
    if not selected:
        raise ValueError("no out-of-fold observations in the recalibrated bands")

    upper_scale, upper_slope, _ = _fit_tail(
        [max(0.0, item.raw_upper) for item in selected],
        [item.log_residual for item in selected],
        upper_quantile,
    )
    lower_scale, lower_slope, _ = _fit_tail(
        [max(0.0, -item.raw_lower) for item in selected],
        [-item.log_residual for item in selected],
        1.0 - lower_quantile,
    )
    return WidthRecalibratorArtifact(
        lower_scale=round(lower_scale, 12),
        lower_slope=round(lower_slope, 12),
        upper_scale=round(upper_scale, 12),
        upper_slope=round(upper_slope, 12),
        applies_to_bands=tuple(bands),
        fold_count=fold_count,
        training_row_count=len(selected),
        training_customer_count=len({item.customer_id for item in selected}),
    )


def conformity_scores(
    rows: Sequence[ResidualRow],
    bounds_of,
) -> tuple[float, ...]:
    """Conformalized quantile regression score: how far outside the published band the truth fell.

    Negative when the band already contains the residual, so a single quantile of this score both
    widens a band that is too tight and tightens one that is too loose.

    `bounds_of(row)` returns the band the runtime would publish for that row, transforms included.
    A correction fitted against an intermediate the runtime never emits corrects nothing.
    """

    scores: list[float] = []
    for row in rows:
        lower, upper = bounds_of(row)
        scores.append(max(lower - row.log_residual, row.log_residual - upper))
    return tuple(scores)


def conformal_widening(scores: Sequence[float], coverage: float) -> float:
    """The finite-sample corrected quantile of the conformity score."""

    if not scores:
        raise ValueError("no conformity scores to calibrate on")
    count = len(scores)
    rank = min(1.0, math.ceil((count + 1) * coverage) / count)
    return _empirical_quantile(scores, rank)


def tail_conformity_scores(
    rows: Sequence[ResidualRow],
    bounds_of,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Split the conformity score into its two tails. ADR 0007.

    `conformity_scores` takes the maximum of the two, which is what makes a single correction a
    joint `80%` statement. Correcting the tails separately makes the lower bound a `p10` claim and
    the upper bound a `p90` claim, each answerable on its own.

    Both are positive when the truth fell outside the learned band on that side and negative when
    the band already had room, so a quantile of either both widens a tail that is too tight and
    tightens one that is too loose.
    """

    lower_scores: list[float] = []
    upper_scores: list[float] = []
    for row in rows:
        lower, upper = bounds_of(row)
        lower_scores.append(lower - row.log_residual)
        upper_scores.append(row.log_residual - upper)
    return tuple(lower_scores), tuple(upper_scores)


def conformal_tail_adjustment(scores: Sequence[float], tail_coverage: float) -> float:
    """The finite-sample corrected quantile of one tail's conformity score.

    Identical arithmetic to `conformal_widening`; named apart because the coverage it is asked for
    is one tail's, `0.90`, not the interval's joint `0.80`.
    """

    return conformal_widening(scores, tail_coverage)


__all__ = [
    "RESIDUAL_QUANTILE_VERSION",
    "ResidualRow",
    "WidthObservation",
    "conformal_tail_adjustment",
    "conformal_widening",
    "conformity_scores",
    "fit_residual_quantile_model",
    "fit_width_recalibrator",
    "residual_rows",
    "tail_conformity_scores",
]
