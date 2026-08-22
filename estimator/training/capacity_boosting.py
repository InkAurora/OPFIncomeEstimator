"""Deterministic squared-error gradient boosting for the capacity estimator.

Splits are searched over pre-computed quantile bins rather than over raw values, so each boosting
round costs one pass per feature instead of one pass per candidate threshold. The result is
identical to an exhaustive scan restricted to the same candidate set, and it keeps a 98-feature
customer-month table trainable in pure Python.

Missingness is a routing decision, not an imputation. Every round evaluates sending absent values
left and right and keeps whichever scores better, so the model can express "no observed cards"
without inventing a value for it.
"""

from __future__ import annotations

import math
from statistics import fmean

from income_estimator.models.capacity import (
    CapacityEstimatorArtifact,
    CapacityStump,
    GradientBoostedCapacityModel,
)
from training.capacity_datasets import (
    CAPACITY_DATASET_VERSION,
    FEATURE_NAMES,
    FEATURE_SCHEMA_FINGERPRINT,
    FEATURE_SET_VERSION,
    INCOME_TARGET_VERSION,
    SPLIT_VERSION,
    CapacityRow,
)
from training.datasets import DATASET_VERSION as TRANSACTION_DATASET_VERSION

CAPACITY_MODEL_VERSION = "capacity-gbdt-stumps-0.5.0"
MISSING_BIN = -1
ANCHOR_FEATURE_NAME = "income_mean_3m_minor"


def _bin_edges(values: list[float], maximum_bins: int) -> tuple[float, ...]:
    """Quantile edges over observed values only; absent values get their own bin."""

    present = sorted(value for value in values if value is not None)
    if len(present) < 2:
        return ()
    unique = sorted(set(present))
    if len(unique) <= maximum_bins:
        return tuple(
            (unique[index - 1] + unique[index]) / 2 for index in range(1, len(unique))
        )
    return tuple(
        (
            present[max(0, index * len(present) // maximum_bins - 1)]
            + present[min(len(present) - 1, index * len(present) // maximum_bins)]
        )
        / 2
        for index in range(1, maximum_bins)
    )


def _binned(value: float | int | None, edges: tuple[float, ...]) -> int:
    if value is None:
        return MISSING_BIN
    low, high = 0, len(edges)
    while low < high:
        middle = (low + high) // 2
        if value <= edges[middle]:
            high = middle
        else:
            low = middle + 1
    return low


def _best_split(
    bins: list[int],
    gradients: list[float],
    hessians: list[float],
    edges: tuple[float, ...],
    *,
    l2_regularization: float,
    minimum_leaf_size: int,
) -> tuple[float, float, bool, float, float] | None:
    """Return gain, threshold, missing direction, and both leaf values for one feature.

    Squared error passes unit hessians; the logistic gate passes p(1-p). One routine therefore
    serves both parts of the hurdle without duplicating the histogram scan.
    """

    bin_count = len(edges) + 1
    gradient_sums = [0.0] * bin_count
    hessian_sums = [0.0] * bin_count
    counts = [0] * bin_count
    missing_gradient = 0.0
    missing_hessian = 0.0
    missing_count = 0
    for index, gradient, hessian in zip(bins, gradients, hessians):
        if index == MISSING_BIN:
            missing_gradient += gradient
            missing_hessian += hessian
            missing_count += 1
        else:
            gradient_sums[index] += gradient
            hessian_sums[index] += hessian
            counts[index] += 1

    total_gradient = sum(gradient_sums) + missing_gradient
    total_hessian = sum(hessian_sums) + missing_hessian
    total_count = len(gradients)
    best: tuple[float, float, bool, float, float] | None = None
    left_gradient = 0.0
    left_hessian = 0.0
    left_count = 0
    for index in range(bin_count - 1):
        left_gradient += gradient_sums[index]
        left_hessian += hessian_sums[index]
        left_count += counts[index]
        for missing_left in (True, False):
            branch_gradient = left_gradient + (missing_gradient if missing_left else 0.0)
            branch_hessian = left_hessian + (missing_hessian if missing_left else 0.0)
            branch_count = left_count + (missing_count if missing_left else 0)
            right_count = total_count - branch_count
            if branch_count < minimum_leaf_size or right_count < minimum_leaf_size:
                continue
            right_gradient = total_gradient - branch_gradient
            right_hessian = total_hessian - branch_hessian
            gain = branch_gradient**2 / (branch_hessian + l2_regularization) + (
                right_gradient**2 / (right_hessian + l2_regularization)
            )
            candidate = (
                gain,
                edges[index],
                missing_left,
                branch_gradient / (branch_hessian + l2_regularization),
                right_gradient / (right_hessian + l2_regularization),
            )
            if best is None or candidate > best:
                best = candidate
    return best


def _fit_stumps(
    rows: tuple[CapacityRow, ...],
    edges_by_feature: dict[str, tuple[float, ...]],
    bins_by_feature: dict[str, list[int]],
    *,
    initial_scores: list[float],
    gradient_hessian,
    rounds: int,
    learning_rate: float,
    l2_regularization: float,
    minimum_leaf_size: int,
) -> tuple[list[CapacityStump], list[float]]:
    """Boost additive stumps; ties break on feature name then threshold for determinism."""

    scores = list(initial_scores)
    trees: list[CapacityStump] = []
    for _ in range(rounds):
        gradients, hessians = gradient_hessian(scores)
        best: tuple[float, str, float, bool, float, float] | None = None
        for name in FEATURE_NAMES:
            edges = edges_by_feature[name]
            if not edges:
                continue
            split = _best_split(
                bins_by_feature[name],
                gradients,
                hessians,
                edges,
                l2_regularization=l2_regularization,
                minimum_leaf_size=minimum_leaf_size,
            )
            if split is None:
                continue
            gain, threshold, missing_left, left_value, right_value = split
            candidate = (gain, name, threshold, missing_left, left_value, right_value)
            if best is None or candidate > best:
                best = candidate
        if best is None or best[0] <= 0:
            break
        _, name, threshold, missing_left, left_value, right_value = best
        tree = CapacityStump(
            feature_name=name,
            threshold=round(threshold, 12),
            missing_left=missing_left,
            left_value=round(left_value, 12),
            right_value=round(right_value, 12),
        )
        trees.append(tree)
        for index, row in enumerate(rows):
            scores[index] += learning_rate * _leaf(tree, row.features[name])
    return trees, scores


def fit_capacity_model(
    train: tuple[CapacityRow, ...],
    validation: tuple[CapacityRow, ...],
    *,
    rounds: int = 400,
    learning_rate: float = 0.3,
    gate_rounds: int = 60,
    gate_learning_rate: float = 0.3,
    l2_regularization: float = 1.0,
    minimum_leaf_size: int = 20,
    maximum_bins: int = 32,
    anchor_feature_name: str = ANCHOR_FEATURE_NAME,
    input_contract_version: str = "1.2",
    simulator_version: str = "0.7.0",
    source_contract_versions: tuple[str, ...] = ("1.3", "1.4", "1.5"),
) -> CapacityEstimatorArtifact:
    """Fit the hurdle: a logistic zero gate, then an anchored regressor on positive rows.

    Both parts stop where held-out error is lowest. The gate threshold is chosen on validation by
    mean absolute error in minor units rather than by classification score, because a wrong zero
    costs the whole estimate while a wrong positive costs only its error.
    """

    if not train or not validation:
        raise ValueError("train and validation partitions must not be empty")

    edges_by_feature = {
        name: _bin_edges([row.features[name] for row in train], maximum_bins)
        for name in FEATURE_NAMES
    }

    def _bins(rows: tuple[CapacityRow, ...]) -> dict[str, list[int]]:
        return {
            name: [_binned(row.features[name], edges_by_feature[name]) for row in rows]
            for name in FEATURE_NAMES
        }

    train_bins = _bins(train)
    gate_labels = [
        1 if row.sustainable_monthly_income_minor > 0 else 0 for row in train
    ]
    positive_rate = min(1 - 1e-6, max(1e-6, fmean(gate_labels)))
    gate_base_score = math.log(positive_rate / (1 - positive_rate))

    def _logistic_gradient_hessian(scores: list[float]):
        probabilities = [_sigmoid(score) for score in scores]
        return (
            [label - probability for label, probability in zip(gate_labels, probabilities)],
            [probability * (1 - probability) for probability in probabilities],
        )

    gate_trees, _ = _fit_stumps(
        train,
        edges_by_feature,
        train_bins,
        initial_scores=[gate_base_score] * len(train),
        gradient_hessian=_logistic_gradient_hessian,
        rounds=gate_rounds,
        learning_rate=gate_learning_rate,
        l2_regularization=l2_regularization,
        minimum_leaf_size=minimum_leaf_size,
    )

    positive = tuple(row for row in train if row.sustainable_monthly_income_minor > 0)
    if not positive:
        raise ValueError("training partition contains no positive sustainable income")
    positive_bins = _bins(positive)
    anchors = [_anchor_log(row, anchor_feature_name) for row in positive]
    targets = [row.log_target - anchor for row, anchor in zip(positive, anchors)]
    base_score = fmean(targets)

    def _squared_gradient_hessian(scores: list[float]):
        return (
            [target - score for target, score in zip(targets, scores)],
            [1.0] * len(scores),
        )

    trees, _ = _fit_stumps(
        positive,
        edges_by_feature,
        positive_bins,
        initial_scores=[base_score] * len(positive),
        gradient_hessian=_squared_gradient_hessian,
        rounds=rounds,
        learning_rate=learning_rate,
        l2_regularization=l2_regularization,
        minimum_leaf_size=minimum_leaf_size,
    )

    def _artifact(
        selected_trees: tuple[CapacityStump, ...],
        threshold: int,
    ) -> CapacityEstimatorArtifact:
        return CapacityEstimatorArtifact(
            model_version=CAPACITY_MODEL_VERSION,
            feature_version=FEATURE_SET_VERSION,
            feature_schema_fingerprint=FEATURE_SCHEMA_FINGERPRINT,
            target="log1p_sustainable_monthly_income_minor",
            anchor_feature_name=anchor_feature_name,
            feature_names=FEATURE_NAMES,
            base_score=round(base_score, 12),
            learning_rate=learning_rate,
            trees=selected_trees,
            gate_base_score=round(gate_base_score, 12),
            gate_learning_rate=gate_learning_rate,
            gate_threshold_basis_points=threshold,
            gate_trees=tuple(gate_trees),
            dataset_version=CAPACITY_DATASET_VERSION,
            split_version=SPLIT_VERSION,
            simulator_version=simulator_version,
            income_target_version=INCOME_TARGET_VERSION,
            source_contract_versions=source_contract_versions,
            input_contract_version=input_contract_version,
            training_rounds_requested=rounds,
            l2_regularization=l2_regularization,
            minimum_leaf_size=minimum_leaf_size,
            maximum_bins=maximum_bins,
            training_customer_count=len({row.customer_id for row in train}),
            validation_customer_count=len({row.customer_id for row in validation}),
            training_row_count=len(train),
        )

    best_count = _select_tree_count(validation, trees, _artifact)
    selected = tuple(trees[:best_count])
    best_threshold = _select_gate_threshold(validation, selected, _artifact)
    return _artifact(selected, best_threshold)


def _validation_mae(
    validation: tuple[CapacityRow, ...],
    artifact: CapacityEstimatorArtifact,
) -> float:
    model = GradientBoostedCapacityModel(artifact)
    return fmean(
        abs(model.predict_minor(row.features) - row.sustainable_monthly_income_minor)
        for row in validation
    )


def _select_tree_count(
    validation: tuple[CapacityRow, ...],
    trees: list[CapacityStump],
    build,
) -> int:
    """Search tree counts on a coarse grid, then refine, keeping the search deterministic."""

    candidates = sorted(
        {0, len(trees), *range(0, len(trees) + 1, max(1, len(trees) // 20))}
    )
    best_count = 0
    best_error = None
    for count in candidates:
        error = _validation_mae(validation, build(tuple(trees[:count]), 5_000))
        if best_error is None or error < best_error:
            best_error = error
            best_count = count
    return best_count


def _select_gate_threshold(
    validation: tuple[CapacityRow, ...],
    selected_trees: tuple[CapacityStump, ...],
    build,
) -> int:
    """Pick the zero cutoff by held-out monetary error, not by classification score."""

    best_threshold = 5_000
    best_error = None
    for threshold in range(500, 9_501, 500):
        error = _validation_mae(validation, build(selected_trees, threshold))
        if best_error is None or error < best_error:
            best_error = error
            best_threshold = threshold
    return best_threshold


def _sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-max(-40.0, min(40.0, value))))


def _anchor_log(row: CapacityRow, anchor_feature_name: str) -> float:
    value = row.features.get(anchor_feature_name)
    return math.log1p(max(0.0, float(value))) if value is not None else 0.0


def _leaf(tree: CapacityStump, value: float | int | None) -> float:
    if value is None:
        return tree.left_value if tree.missing_left else tree.right_value
    return tree.left_value if value <= tree.threshold else tree.right_value



__all__ = [
    "ANCHOR_FEATURE_NAME",
    "CAPACITY_MODEL_VERSION",
    "TRANSACTION_DATASET_VERSION",
    "fit_capacity_model",
]
