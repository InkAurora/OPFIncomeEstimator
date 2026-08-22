"""Deterministic gradient boosting trainer for portable decision stumps."""

from __future__ import annotations

import math

from income_estimator.models.transaction_classifier import (
    MODEL_FEATURE_NAMES,
    DecisionStump,
    TransactionClassifierArtifact,
)
from training.datasets import DATASET_VERSION, SPLIT_VERSION, LabeledTransaction

MODEL_VERSION = "transaction-gbdt-stumps-0.3.0"


def _sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-max(-40.0, min(40.0, value))))


def _candidate_thresholds(values: list[float], limit: int = 32) -> tuple[float, ...]:
    unique = sorted(set(values))
    if len(unique) < 2:
        return ()
    boundary_indexes = range(1, len(unique))
    if len(unique) - 1 > limit:
        boundary_indexes = sorted(
            {
                max(1, min(len(unique) - 1, round(index * len(unique) / limit)))
                for index in range(1, limit)
            }
        )
    return tuple(
        (unique[index - 1] + unique[index]) / 2 for index in boundary_indexes
    )


def _predict_scores(
    records: tuple[LabeledTransaction, ...],
    base_score: float,
    learning_rate: float,
    trees: list[DecisionStump],
) -> list[float]:
    scores: list[float] = []
    for record in records:
        score = base_score
        for tree in trees:
            value = record.model_features[tree.feature_name]
            leaf = tree.left_value if value <= tree.threshold else tree.right_value
            score += learning_rate * leaf
        scores.append(score)
    return scores


def _f1(labels: list[int], predictions: list[bool]) -> tuple[float, float, float]:
    true_positive = sum(prediction and label for label, prediction in zip(labels, predictions))
    false_positive = sum(
        prediction and not label for label, prediction in zip(labels, predictions)
    )
    false_negative = sum(
        not prediction and label for label, prediction in zip(labels, predictions)
    )
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 0.0
    )
    score = (
        2 * precision * recall / (precision + recall) if precision + recall else 0.0
    )
    return precision, recall, score


def _select_threshold(
    validation: tuple[LabeledTransaction, ...],
    scores: list[float],
) -> int:
    labels = [record.label for record in validation]
    baseline_precision, _, _ = _f1(
        labels,
        [record.baseline_is_income for record in validation],
    )
    best_key: tuple[float, float, float, int] | None = None
    best_threshold = 5_000
    for threshold in range(500, 9_951, 50):
        predictions = [
            False
            if record.hard_excluded
            else round(_sigmoid(score) * 10_000) >= threshold
            for record, score in zip(validation, scores)
        ]
        precision, recall, f1 = _f1(labels, predictions)
        if precision + 1e-12 < baseline_precision:
            continue
        key = (f1, precision, recall, threshold)
        if best_key is None or key > best_key:
            best_key = key
            best_threshold = threshold
    return best_threshold


def fit_gradient_boosted_stumps(
    train: tuple[LabeledTransaction, ...],
    validation: tuple[LabeledTransaction, ...],
    *,
    rounds: int = 32,
    learning_rate: float = 0.1,
    l2_regularization: float = 1.0,
    minimum_leaf_size: int = 8,
) -> TransactionClassifierArtifact:
    if not train or not validation:
        raise ValueError("train and validation partitions must not be empty")
    positive_rate = min(
        1 - 1e-6,
        max(1e-6, sum(record.label for record in train) / len(train)),
    )
    base_score = math.log(positive_rate / (1 - positive_rate))
    scores = [base_score] * len(train)
    trees: list[DecisionStump] = []

    feature_thresholds = {
        name: _candidate_thresholds(
            [record.model_features[name] for record in train]
        )
        for name in MODEL_FEATURE_NAMES
    }
    for _ in range(rounds):
        probabilities = [_sigmoid(score) for score in scores]
        gradients = [
            record.label - probability
            for record, probability in zip(train, probabilities)
        ]
        hessians = [probability * (1 - probability) for probability in probabilities]
        best: tuple[float, str, float, float, float] | None = None
        for feature_name in MODEL_FEATURE_NAMES:
            values = [record.model_features[feature_name] for record in train]
            for threshold in feature_thresholds[feature_name]:
                left_indexes = [
                    index for index, value in enumerate(values) if value <= threshold
                ]
                right_count = len(train) - len(left_indexes)
                if (
                    len(left_indexes) < minimum_leaf_size
                    or right_count < minimum_leaf_size
                ):
                    continue
                left_set = set(left_indexes)
                left_gradient = sum(gradients[index] for index in left_indexes)
                left_hessian = sum(hessians[index] for index in left_indexes)
                right_gradient = sum(
                    gradient
                    for index, gradient in enumerate(gradients)
                    if index not in left_set
                )
                right_hessian = sum(
                    hessian
                    for index, hessian in enumerate(hessians)
                    if index not in left_set
                )
                gain = (
                    left_gradient**2 / (left_hessian + l2_regularization)
                    + right_gradient**2 / (right_hessian + l2_regularization)
                )
                left_value = left_gradient / (left_hessian + l2_regularization)
                right_value = right_gradient / (right_hessian + l2_regularization)
                candidate = (
                    gain,
                    feature_name,
                    threshold,
                    left_value,
                    right_value,
                )
                if best is None or candidate > best:
                    best = candidate
        if best is None or best[0] <= 0:
            break
        _, feature_name, threshold, left_value, right_value = best
        tree = DecisionStump(
            feature_name=feature_name,
            threshold=round(threshold, 12),
            left_value=round(left_value, 12),
            right_value=round(right_value, 12),
        )
        trees.append(tree)
        for index, record in enumerate(train):
            leaf = (
                tree.left_value
                if record.model_features[feature_name] <= threshold
                else tree.right_value
            )
            scores[index] += learning_rate * leaf

    validation_scores = _predict_scores(
        validation,
        base_score,
        learning_rate,
        trees,
    )
    threshold = _select_threshold(validation, validation_scores)
    return TransactionClassifierArtifact(
        model_version=MODEL_VERSION,
        feature_names=MODEL_FEATURE_NAMES,
        base_score=round(base_score, 12),
        learning_rate=learning_rate,
        decision_threshold_basis_points=threshold,
        trees=tuple(trees),
        dataset_version=DATASET_VERSION,
        split_version=SPLIT_VERSION,
        simulator_version="0.7.0",
        source_contract_versions=("1.3", "1.4", "1.5"),
        training_rounds_requested=rounds,
        l2_regularization=l2_regularization,
        minimum_leaf_size=minimum_leaf_size,
        training_customer_count=len({record.customer_id for record in train}),
        validation_customer_count=len(
            {record.customer_id for record in validation}
        ),
    )


__all__ = ["MODEL_VERSION", "fit_gradient_boosted_stumps"]
