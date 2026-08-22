"""Held-out transaction classification metrics and safety error rates."""

from __future__ import annotations

from collections import defaultdict

from income_estimator.models.transaction_classifier import (
    GradientBoostedTransactionClassifier,
    TransactionClassifierArtifact,
)
from training.datasets import LabeledTransaction

CRITICAL_FALSE_POSITIVE_TYPES = (
    "INVESTMENT_REDEMPTION",
    "LOAN_DISBURSEMENT",
    "OWN_TRANSFER",
    "REFUND",
)


def _roc_auc(labels: list[int], scores: list[int]) -> float | None:
    positive_count = sum(labels)
    negative_count = len(labels) - positive_count
    if not positive_count or not negative_count:
        return None
    ordered = sorted(zip(scores, labels), key=lambda item: item[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = (index + 1 + end) / 2
        rank_sum += average_rank * sum(label for _, label in ordered[index:end])
        index = end
    return (
        rank_sum - positive_count * (positive_count + 1) / 2
    ) / (positive_count * negative_count)


def _average_precision(labels: list[int], scores: list[int]) -> float | None:
    positive_count = sum(labels)
    if not positive_count:
        return None
    ordered = sorted(
        enumerate(zip(scores, labels)),
        key=lambda item: (-item[1][0], item[0]),
    )
    true_positive = 0
    precision_sum = 0.0
    for rank, (_, (_, label)) in enumerate(ordered, start=1):
        if label:
            true_positive += 1
            precision_sum += true_positive / rank
    return precision_sum / positive_count


def classification_metrics(
    records: tuple[LabeledTransaction, ...],
    *,
    artifact: TransactionClassifierArtifact | None = None,
) -> dict[str, object]:
    if artifact is None:
        scores = [10_000 if record.baseline_is_income else 0 for record in records]
        predictions = [record.baseline_is_income for record in records]
        version = "rule-based-0.1.0"
    else:
        model = GradientBoostedTransactionClassifier(artifact)
        scores = [
            0
            if record.hard_excluded
            else model.predict_values_basis_points(record.model_features)
            for record in records
        ]
        predictions = [
            score >= artifact.decision_threshold_basis_points
            for score in scores
        ]
        version = artifact.model_version

    labels = [record.label for record in records]
    true_positive = sum(
        prediction and label for prediction, label in zip(predictions, labels)
    )
    false_positive = sum(
        prediction and not label for prediction, label in zip(predictions, labels)
    )
    false_negative = sum(
        not prediction and label for prediction, label in zip(predictions, labels)
    )
    true_negative = len(records) - true_positive - false_positive - false_negative
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
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    totals: dict[str, int] = defaultdict(int)
    critical_false_positives: dict[str, int] = defaultdict(int)
    hard_exclusion_false_negative_reasons: dict[str, int] = defaultdict(int)
    for record, prediction in zip(records, predictions):
        if record.economic_type in CRITICAL_FALSE_POSITIVE_TYPES:
            totals[record.economic_type] += 1
            if prediction and not record.label:
                critical_false_positives[record.economic_type] += 1
        if record.label and not prediction and record.hard_excluded:
            for reason in record.baseline_reason_codes:
                hard_exclusion_false_negative_reasons[reason] += 1

    return {
        "classifier_version": version,
        "record_count": len(records),
        "positive_count": sum(labels),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "f1": round(f1, 8),
        "pr_auc": (
            round(value, 8) if (value := _average_precision(labels, scores)) is not None else None
        ),
        "roc_auc": (
            round(value, 8) if (value := _roc_auc(labels, scores)) is not None else None
        ),
        "critical_false_positive_rates": {
            economic_type: (
                round(critical_false_positives[economic_type] / totals[economic_type], 8)
                if totals[economic_type]
                else 0.0
            )
            for economic_type in CRITICAL_FALSE_POSITIVE_TYPES
        },
        "hard_exclusion_false_negative_reasons": dict(
            sorted(hard_exclusion_false_negative_reasons.items())
        ),
    }


__all__ = ["CRITICAL_FALSE_POSITIVE_TYPES", "classification_metrics"]
