"""Fit conformal intervals from out-of-fold residuals and measure their coverage.

The gate for `0.7` is empirical: an interval sold as 80% must contain the truth about 80% of the
time on customers the model never trained on. This script fits the offsets on out-of-fold residuals
from train and validation customers, then measures coverage, width, and confidence monotonicity on
the untouched test partition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Sequence
from pathlib import Path
from statistics import fmean, median

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config

from income_estimator.models.capacity import GradientBoostedCapacityModel
from income_estimator.models.ensemble import combine_month
from income_estimator.models.quantiles import (
    CALIBRATION_METHOD,
    ConformalCalibrationArtifact,
    ConformalIntervalModel,
    empirical_quantile,
)
from training.capacity_datasets import (
    CAPACITY_DATASET_VERSION,
    build_capacity_dataset,
    split_capacity_rows,
)
from training.out_of_fold import (
    OUT_OF_FOLD_VERSION,
    build_out_of_fold_predictions,
    customer_fold,
)

CALIBRATION_VERSION = "conformal-intervals-0.7.0"
DEFAULT_LOWER_QUANTILE = 0.1
DEFAULT_UPPER_QUANTILE = 0.9
ZERO_GATE_CERTAIN_BASIS_POINTS = 1_000
COVERAGE_TOLERANCE = 0.05

SUITES = (
    ("income_diverse.yaml", 410_000),
    ("life_events.yaml", 420_000),
    ("incomplete_observation.yaml", 430_000),
)


def _populations(project_root: Path, population_size: int, months: int, workers: int):
    scenario_root = project_root / "finances_simulator/configs/scenarios"
    return tuple(
        generate_population(
            load_scenario_config(scenario_root / scenario),
            population_size=population_size,
            seed=seed,
            months=months,
            workers=workers,
        )
        for scenario, seed in SUITES
    )


def _confidence_score(row, capacity: GradientBoostedCapacityModel) -> int:
    """Read the score estimator 0.6 actually publishes, not a proxy for it."""

    realized = int(row.features.get("income_1m_minor") or 0)
    return combine_month(
        realized,
        row.features,
        capacity,
        realized_components={"recurring_streams_0_2": realized},
        realized_selected="recurring_streams_0_2",
    ).confidence_score_basis_points


def _confidence_band(row, capacity: GradientBoostedCapacityModel) -> str:
    score = _confidence_score(row, capacity)
    if score >= 7_000:
        return "high"
    if score >= 5_000:
        return "medium"
    return "low"


def _coverage_metrics(rows, model, intervals) -> dict[str, object]:
    if not rows:
        return {"count": 0}
    covered = 0
    widths: list[int] = []
    errors: list[int] = []
    truths: list[int] = []
    for row in rows:
        point = model.predict_minor(row.features)
        lower, upper = intervals.interval_minor(
            point,
            positive_basis_points=model.predict_positive_basis_points(row.features),
        )
        truth = row.sustainable_monthly_income_minor
        covered += int(lower <= truth <= upper)
        widths.append(upper - lower)
        errors.append(abs(point - truth))
        truths.append(truth)
    truth_total = sum(truths)
    return {
        "count": len(rows),
        "empirical_coverage": round(covered / len(rows), 6),
        "mean_interval_width_minor": round(fmean(widths), 4),
        "median_interval_width_minor": round(float(median(widths)), 4),
        "mean_absolute_error_minor": round(fmean(errors), 4),
        "wape": round(sum(errors) / truth_total, 8) if truth_total else None,
    }


def _segmented(rows, model, intervals, classifier) -> dict[str, dict[str, object]]:
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(classifier(row), []).append(row)
    return {
        band: _coverage_metrics(items, model, intervals)
        for band, items in sorted(grouped.items())
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument(
        "--capacity-model",
        type=Path,
        default=Path(__file__).parent / "artifacts/capacity-estimator-0.5.0.json",
    )
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "artifacts")
    parser.add_argument("--population-size-per-suite", type=int, default=80)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=400)
    args = parser.parse_args(argv)

    populations = _populations(
        args.project_root.resolve(),
        args.population_size_per_suite,
        args.months,
        args.workers,
    )
    partitions = split_capacity_rows(build_capacity_dataset(populations))
    calibration_rows = (*partitions["train"], *partitions["validation"])
    out_of_fold = build_out_of_fold_predictions(
        calibration_rows,
        fold_count=args.folds,
        rounds=args.rounds,
    )
    residuals = out_of_fold.positive_log_residuals
    if not residuals:
        raise ValueError("no positive out-of-fold residuals to calibrate on")

    capacity = GradientBoostedCapacityModel.from_path(args.capacity_model.resolve())
    artifact = ConformalCalibrationArtifact(
        calibration_version=CALIBRATION_VERSION,
        method=CALIBRATION_METHOD,
        capacity_model_version=capacity.artifact.model_version,
        out_of_fold_version=OUT_OF_FOLD_VERSION,
        fold_count=args.folds,
        nominal_lower_quantile=DEFAULT_LOWER_QUANTILE,
        nominal_upper_quantile=DEFAULT_UPPER_QUANTILE,
        lower_log_offset=round(
            min(0.0, empirical_quantile(residuals, DEFAULT_LOWER_QUANTILE)), 12
        ),
        upper_log_offset=round(
            max(0.0, empirical_quantile(residuals, DEFAULT_UPPER_QUANTILE)), 12
        ),
        zero_gate_certain_basis_points=ZERO_GATE_CERTAIN_BASIS_POINTS,
        calibration_row_count=len(calibration_rows),
        calibration_customer_count=len({row.customer_id for row in calibration_rows}),
    )
    intervals = ConformalIntervalModel(artifact)
    test_rows = partitions["test"]

    overall = _coverage_metrics(test_rows, capacity, intervals)
    nominal = artifact.nominal_coverage
    empirical = float(overall["empirical_coverage"])
    standard_error = math.sqrt(max(1e-12, nominal * (1 - nominal) / max(1, len(test_rows))))
    failures: list[str] = []
    if abs(empirical - nominal) > COVERAGE_TOLERANCE:
        failures.append(
            f"empirical coverage {empirical:.4f} is more than {COVERAGE_TOLERANCE} from "
            f"nominal {nominal:.2f}"
        )

    confidence_bands = _segmented(
        test_rows,
        capacity,
        intervals,
        lambda row: _confidence_band(row, capacity),
    )
    ordered_bands = [
        band for band in ("high", "medium", "low") if confidence_bands.get(band, {}).get("count")
    ]
    band_errors = [
        confidence_bands[band]["wape"]
        for band in ordered_bands
        if confidence_bands[band]["wape"] is not None
    ]
    if band_errors != sorted(band_errors):
        failures.append(
            "relative error is not monotonic across confidence bands: "
            + ", ".join(
                f"{band}={error:.4f}"
                for band, error in zip(ordered_bands, band_errors)
            )
        )

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact_path = output / "quantile-calibration-0.7.0.json"
    artifact_bytes = (artifact.model_dump_json(indent=2) + "\n").encode()
    artifact_path.write_bytes(artifact_bytes)

    report = {
        "schema_version": "1.0",
        "calibration_version": CALIBRATION_VERSION,
        "method": CALIBRATION_METHOD,
        "dataset_version": CAPACITY_DATASET_VERSION,
        "out_of_fold_version": OUT_OF_FOLD_VERSION,
        "capacity_model_version": capacity.artifact.model_version,
        "fold_count": args.folds,
        "population_size_per_suite": args.population_size_per_suite,
        "months": args.months,
        "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "calibration": {
            "row_count": len(calibration_rows),
            "positive_residual_count": len(residuals),
            "lower_log_offset": artifact.lower_log_offset,
            "upper_log_offset": artifact.upper_log_offset,
            "fold_row_counts": {
                str(fold): sum(1 for item in out_of_fold.predictions if item.fold == fold)
                for fold in range(args.folds)
            },
        },
        "test": {
            "nominal_coverage": nominal,
            "coverage_standard_error": round(standard_error, 6),
            "overall": overall,
            "by_confidence_band": confidence_bands,
            "by_consent_coverage": _segmented(
                test_rows,
                capacity,
                intervals,
                lambda row: (
                    "complete"
                    if (row.features.get("effective_consent_coverage_basis_points") or 0) >= 10_000
                    else "partial_or_undeclared"
                ),
            ),
            "by_income_range": _segmented(
                test_rows,
                capacity,
                intervals,
                lambda row: (
                    "zero" if row.sustainable_monthly_income_minor == 0 else "positive"
                ),
            ),
        },
        "promotion": {
            "status": "PROMOTED" if not failures else "NOT_PROMOTED",
            "failures": failures,
            "coverage_tolerance": COVERAGE_TOLERANCE,
        },
    }
    report_path = output / "quantile-calibration-0.7.0-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Artifact: {artifact_path}")
    print(f"Report: {report_path}")
    print(f"Coverage: {empirical:.4f} nominal {nominal:.2f} +/- {standard_error:.4f}")
    print(f"Promotion: {report['promotion']['status']}")
    for failure in failures:
        print(f"  - {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CALIBRATION_VERSION",
    "COVERAGE_TOLERANCE",
    "ZERO_GATE_CERTAIN_BASIS_POINTS",
    "customer_fold",
    "main",
]
