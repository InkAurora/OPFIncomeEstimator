"""Held-out capacity metrics, deterministic baselines, and segmented reporting.

MAPE is deliberately absent: sustainable income can be zero for an unemployed customer, so a
percentage error is undefined exactly where the estimate matters most. WAPE and SMAPE carry the
relative view instead.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from statistics import fmean, median

from income_estimator.models.capacity import (
    CapacityEstimatorArtifact,
    GradientBoostedCapacityModel,
)
from training.capacity_datasets import CapacityRow

Predictor = Callable[[CapacityRow], int]


def _feature(row: CapacityRow, name: str) -> float | int | None:
    return row.features.get(name)


def historical_median_baseline(row: CapacityRow) -> int:
    """Median reconstructed monthly income over the trailing year."""

    value = _feature(row, "income_median_12m_minor")
    return int(value) if value is not None else 0


def cash_flow_baseline(row: CapacityRow) -> int:
    """Last reconstructed month, the simplest defensible carry-forward."""

    value = _feature(row, "income_1m_minor")
    return int(value) if value is not None else 0


def recurring_stream_baseline(row: CapacityRow) -> int:
    """Mean reconstructed income over the trailing quarter."""

    value = _feature(row, "income_mean_3m_minor")
    return int(value) if value is not None else 0


BASELINES: dict[str, Predictor] = {
    "historical_median_12m": historical_median_baseline,
    "cash_flow_last_month": cash_flow_baseline,
    "recurring_stream_mean_3m": recurring_stream_baseline,
}


def model_predictor(artifact: CapacityEstimatorArtifact) -> Predictor:
    model = GradientBoostedCapacityModel(artifact)
    return lambda row: model.predict_minor(row.features)


def regression_metrics(
    rows: Sequence[CapacityRow],
    predictor: Predictor,
) -> dict[str, object]:
    """Report absolute, squared, and relative error over one row set."""

    if not rows:
        return {"count": 0}
    truths = [row.sustainable_monthly_income_minor for row in rows]
    predictions = [predictor(row) for row in rows]
    errors = [abs(prediction - truth) for truth, prediction in zip(truths, predictions)]
    squared = [(prediction - truth) ** 2 for truth, prediction in zip(truths, predictions)]
    truth_total = sum(truths)
    smape_terms = [
        0.0 if truth == prediction else 2 * abs(prediction - truth) / (abs(truth) + abs(prediction))
        for truth, prediction in zip(truths, predictions)
    ]
    mean_truth = fmean(truths)
    return {
        "count": len(rows),
        "mean_absolute_error_minor": round(fmean(errors), 4),
        "median_absolute_error_minor": round(float(median(errors)), 4),
        "root_mean_squared_error_minor": round(math.sqrt(fmean(squared)), 4),
        "wape": round(sum(errors) / truth_total, 8) if truth_total else None,
        "smape": round(fmean(smape_terms), 8),
        "error_over_mean_income": round(fmean(errors) / mean_truth, 8) if mean_truth else None,
        "mean_truth_minor": round(mean_truth, 4),
        "mean_prediction_minor": round(fmean(predictions), 4),
    }


def _coverage_band(row: CapacityRow) -> str:
    value = _feature(row, "effective_consent_coverage_basis_points")
    if value is None:
        return "undeclared"
    if value >= 10_000:
        return "complete"
    if value >= 7_000:
        return "partial_high"
    return "partial_low"


def _volatility_band(row: CapacityRow) -> str:
    value = _feature(row, "income_cv_12m")
    if value is None:
        return "unknown"
    if value < 0.1:
        return "stable"
    if value < 0.4:
        return "moderate"
    return "volatile"


def _history_band(row: CapacityRow) -> str:
    value = _feature(row, "window_months") or 0
    if value >= 12:
        return "months_12_plus"
    if value >= 6:
        return "months_6_to_11"
    return "months_under_6"


def _income_band(row: CapacityRow) -> str:
    truth = row.sustainable_monthly_income_minor
    if truth == 0:
        return "zero"
    if truth < 300_000:
        return "low"
    if truth < 800_000:
        return "middle"
    return "high"


SEGMENTS: dict[str, Callable[[CapacityRow], str]] = {
    "consent_coverage": _coverage_band,
    "income_volatility": _volatility_band,
    "history_length": _history_band,
    "sustainable_income_range": _income_band,
}


def segmented_metrics(
    rows: Sequence[CapacityRow],
    predictor: Predictor,
) -> dict[str, dict[str, dict[str, object]]]:
    result: dict[str, dict[str, dict[str, object]]] = {}
    for segment_name, classifier in SEGMENTS.items():
        grouped: dict[str, list[CapacityRow]] = {}
        for row in rows:
            grouped.setdefault(classifier(row), []).append(row)
        result[segment_name] = {
            band: regression_metrics(items, predictor)
            for band, items in sorted(grouped.items())
        }
    return result


def evaluate_partition(
    rows: Sequence[CapacityRow],
    artifact: CapacityEstimatorArtifact,
) -> dict[str, object]:
    """Compare the candidate with every deterministic baseline on the same rows."""

    predictors: dict[str, Predictor] = {
        "candidate": model_predictor(artifact),
        **BASELINES,
    }
    return {
        name: {
            "overall": regression_metrics(rows, predictor),
            "segments": segmented_metrics(rows, predictor),
        }
        for name, predictor in predictors.items()
    }


def _mae(entry: dict[str, object]) -> float:
    value = entry.get("mean_absolute_error_minor")
    return float(value) if value is not None else math.inf


def best_baseline(evaluation: dict[str, object]) -> tuple[str, float]:
    scores = {
        name: _mae(entry["overall"])  # type: ignore[index]
        for name, entry in evaluation.items()
        if name in BASELINES
    }
    name = min(scores, key=lambda key: (scores[key], key))
    return name, scores[name]


FULL_COVERAGE_TOLERANCE = 1.05


def promotion_decision(evaluation: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    """Apply the frozen 0.5 gate from the implementation plan.

    Overall error must strictly improve, partial-consent error must improve, full-coverage error
    must not regress beyond a documented tolerance, and zero-income customers must not get worse.
    """

    failures: list[str] = []
    candidate = evaluation["candidate"]
    baseline_name, baseline_mae = best_baseline(evaluation)
    candidate_mae = _mae(candidate["overall"])  # type: ignore[index]
    if not candidate_mae < baseline_mae:
        failures.append(
            f"test MAE must improve on best baseline {baseline_name} "
            f"({candidate_mae:.4f} vs {baseline_mae:.4f})"
        )

    def _segment(entry: dict[str, object], segment: str, band: str) -> dict[str, object] | None:
        return entry["segments"].get(segment, {}).get(band)  # type: ignore[index,union-attr]

    for band in ("partial_low", "partial_high"):
        candidate_band = _segment(candidate, "consent_coverage", band)  # type: ignore[arg-type]
        if not candidate_band or not candidate_band.get("count"):
            continue
        baseline_band = _segment(
            evaluation[baseline_name],  # type: ignore[arg-type]
            "consent_coverage",
            band,
        )
        if baseline_band and _mae(candidate_band) >= _mae(baseline_band):
            failures.append(f"partial-consent segment {band} did not improve")

    complete_candidate = _segment(candidate, "consent_coverage", "complete")  # type: ignore[arg-type]
    complete_baseline = _segment(
        evaluation[baseline_name],  # type: ignore[arg-type]
        "consent_coverage",
        "complete",
    )
    if complete_candidate and complete_baseline and complete_candidate.get("count"):
        if _mae(complete_candidate) > _mae(complete_baseline) * FULL_COVERAGE_TOLERANCE:
            failures.append("full-coverage segment regressed beyond tolerance")

    zero_candidate = _segment(candidate, "sustainable_income_range", "zero")  # type: ignore[arg-type]
    zero_baseline = _segment(
        evaluation[baseline_name],  # type: ignore[arg-type]
        "sustainable_income_range",
        "zero",
    )
    if zero_candidate and zero_baseline and zero_candidate.get("count"):
        if _mae(zero_candidate) > _mae(zero_baseline):
            failures.append("zero-income customers regressed")

    return ("PROMOTED" if not failures else "NOT_PROMOTED", tuple(failures))


__all__ = [
    "BASELINES",
    "FULL_COVERAGE_TOLERANCE",
    "SEGMENTS",
    "best_baseline",
    "evaluate_partition",
    "model_predictor",
    "promotion_decision",
    "regression_metrics",
    "segmented_metrics",
]
