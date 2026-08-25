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
    ResidualQuantileArtifact,
    ScaleStump,
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


def conformity_scores(
    rows: Sequence[ResidualRow],
    model,
) -> tuple[float, ...]:
    """Conformalized quantile regression score: how far outside the learned band the truth fell.

    Negative when the band already contains the residual, so a single quantile of this score both
    widens a band that is too tight and tightens one that is too loose.
    """

    scores: list[float] = []
    for row in rows:
        lower, upper = model.predict_bounds(row.features)
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
    model,
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
        lower, upper = model.predict_bounds(row.features)
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
    "conformal_tail_adjustment",
    "conformal_widening",
    "conformity_scores",
    "fit_residual_quantile_model",
    "residual_rows",
    "tail_conformity_scores",
]
