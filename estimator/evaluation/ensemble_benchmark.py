"""Held-out comparison of estimator 0.6 routing against each individual component.

Routing must earn its place. The plan allows an ensemble that does not beat every component
everywhere, provided the segments where it deliberately selects one component are documented, so
this benchmark reports the routed estimate beside every component it could have chosen and records
which rule fired on each row.

Evaluation may read private truth because inference has already completed. Runtime never imports
this module.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from statistics import fmean

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config

from income_estimator.models.capacity import GradientBoostedCapacityModel
from income_estimator.models.ensemble import ENSEMBLE_VERSION, combine_month
from training.capacity_datasets import (
    CAPACITY_DATASET_VERSION,
    CapacityRow,
    build_capacity_dataset,
    split_capacity_rows,
)
from training.capacity_metrics import BASELINES, regression_metrics, segmented_metrics

ENSEMBLE_REPORT_SCHEMA_VERSION = "1.0"
SUITES = (
    ("income_diverse.yaml", 310_000),
    ("life_events.yaml", 320_000),
    ("incomplete_observation.yaml", 330_000),
)


def _routed(row: CapacityRow, capacity: GradientBoostedCapacityModel) -> int:
    realized = int(row.features.get("income_1m_minor") or 0)
    result = combine_month(
        realized,
        row.features,
        capacity,
        realized_components={"recurring_streams_0_2": realized},
        realized_selected="recurring_streams_0_2",
    )
    return result.sustainable_income_minor or 0


def _routing_reasons(
    rows: Sequence[CapacityRow],
    capacity: GradientBoostedCapacityModel,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        realized = int(row.features.get("income_1m_minor") or 0)
        result = combine_month(
            realized,
            row.features,
            capacity,
            realized_components={"recurring_streams_0_2": realized},
            realized_selected="recurring_streams_0_2",
        )
        for reason in result.routing_reason_codes:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def run_benchmark(
    project_root: Path,
    *,
    capacity_model_path: Path,
    population_size: int,
    months: int,
    workers: int,
) -> dict[str, object]:
    scenario_root = project_root / "finances_simulator/configs/scenarios"
    populations = tuple(
        generate_population(
            load_scenario_config(scenario_root / scenario),
            population_size=population_size,
            seed=seed,
            months=months,
            workers=workers,
        )
        for scenario, seed in SUITES
    )
    rows = split_capacity_rows(build_capacity_dataset(populations))["test"]
    capacity = GradientBoostedCapacityModel.from_path(capacity_model_path)

    predictors = {
        "routed_ensemble": lambda row: _routed(row, capacity),
        "capacity_model": lambda row: capacity.predict_minor(row.features),
        **BASELINES,
    }
    results = {
        name: {
            "overall": regression_metrics(rows, predictor),
            "segments": segmented_metrics(rows, predictor),
        }
        for name, predictor in predictors.items()
    }

    routed_mae = float(results["routed_ensemble"]["overall"]["mean_absolute_error_minor"])
    component_mae = {
        name: float(entry["overall"]["mean_absolute_error_minor"])
        for name, entry in results.items()
        if name != "routed_ensemble"
    }
    best_component = min(component_mae, key=lambda key: (component_mae[key], key))
    improved_segments = sorted(
        band
        for segment, bands in results["routed_ensemble"]["segments"].items()
        for band, metrics in bands.items()
        if metrics.get("count")
        and float(metrics["mean_absolute_error_minor"])
        < float(results[best_component]["segments"][segment][band]["mean_absolute_error_minor"])
    )
    failures: list[str] = []
    if routed_mae > component_mae[best_component]:
        failures.append(
            f"routed MAE {routed_mae:.4f} exceeds best component {best_component} "
            f"{component_mae[best_component]:.4f}"
        )
    if not improved_segments:
        failures.append("routing improves no segment over the best component")

    return {
        "schema_version": ENSEMBLE_REPORT_SCHEMA_VERSION,
        "ensemble_version": ENSEMBLE_VERSION,
        "dataset_version": CAPACITY_DATASET_VERSION,
        "capacity_model_version": capacity.artifact.model_version,
        "capacity_artifact_sha256": hashlib.sha256(
            capacity_model_path.read_bytes()
        ).hexdigest(),
        "population_size_per_suite": population_size,
        "months": months,
        "row_count": len(rows),
        "customer_count": len({row.customer_id for row in rows}),
        "mean_sustainable_truth_minor": round(
            fmean(row.sustainable_monthly_income_minor for row in rows), 4
        ),
        "routing_reason_counts": _routing_reasons(rows, capacity),
        "results": results,
        "promotion": {
            "status": "PROMOTED" if not failures else "NOT_PROMOTED",
            "failures": tuple(failures),
            "best_component": best_component,
            "best_component_mae_minor": component_mae[best_component],
            "routed_mae_minor": routed_mae,
            "improved_segments": improved_segments,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument(
        "--capacity-model",
        type=Path,
        default=Path(__file__).parents[1] / "training/artifacts/capacity-estimator-0.6.0.json",
    )
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "baselines")
    parser.add_argument("--population-size-per-suite", type=int, default=80)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)

    report = run_benchmark(
        args.project_root.resolve(),
        capacity_model_path=args.capacity_model.resolve(),
        population_size=args.population_size_per_suite,
        months=args.months,
        workers=args.workers,
    )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / "ensemble-0.6.0-report.json"
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=list) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Report: {path}")
    print(f"Promotion: {report['promotion']['status']}")
    for failure in report["promotion"]["failures"]:
        print(f"  - {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
