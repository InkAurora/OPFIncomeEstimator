"""Train and evaluate estimator 0.5 capacity model."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config

from training.capacity_boosting import fit_capacity_model
from training.capacity_datasets import (
    CAPACITY_DATASET_VERSION,
    FEATURE_SCHEMA_FINGERPRINT,
    FEATURE_SET_VERSION,
    INCOME_TARGET_VERSION,
    SPLIT_VERSION,
    build_capacity_dataset,
    split_capacity_rows,
)
from training.capacity_metrics import (
    FULL_COVERAGE_TOLERANCE,
    best_baseline,
    evaluate_partition,
    promotion_decision,
)

SUITES = (
    ("income_diverse.yaml", 110_000),
    ("life_events.yaml", 120_000),
    ("incomplete_observation.yaml", 130_000),
)


def _populations(project_root: Path, population_size: int, workers: int, months: int):
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "artifacts")
    parser.add_argument("--population-size-per-suite", type=int, default=80)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=600)
    args = parser.parse_args(argv)

    populations = _populations(
        args.project_root.resolve(),
        args.population_size_per_suite,
        args.workers,
        args.months,
    )
    rows = build_capacity_dataset(populations)
    partitions = split_capacity_rows(rows)
    artifact = fit_capacity_model(
        partitions["train"],
        partitions["validation"],
        rounds=args.rounds,
    )
    evaluation = {
        name: evaluate_partition(items, artifact) for name, items in partitions.items()
    }
    status, failures = promotion_decision(evaluation["test"])
    baseline_name, baseline_mae = best_baseline(evaluation["test"])

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact_path = output / "capacity-estimator-0.5.0.json"
    artifact_bytes = (artifact.model_dump_json(indent=2) + "\n").encode()
    artifact_path.write_bytes(artifact_bytes)

    report = {
        "schema_version": "1.0",
        "dataset_version": CAPACITY_DATASET_VERSION,
        "split_version": SPLIT_VERSION,
        "feature_version": FEATURE_SET_VERSION,
        "feature_schema_fingerprint": FEATURE_SCHEMA_FINGERPRINT,
        "income_target_version": INCOME_TARGET_VERSION,
        "model_version": artifact.model_version,
        "input_contract_version": artifact.input_contract_version,
        "simulator_version": artifact.simulator_version,
        "population_size_per_suite": args.population_size_per_suite,
        "months": args.months,
        "tree_count": len(artifact.trees),
        "customer_counts": {
            name: len({row.customer_id for row in items})
            for name, items in partitions.items()
        },
        "row_counts": {name: len(items) for name, items in partitions.items()},
        "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "evaluation": evaluation,
        "promotion": {
            "status": status,
            "failures": failures,
            "best_baseline": baseline_name,
            "best_baseline_mae_minor": baseline_mae,
            "full_coverage_tolerance": FULL_COVERAGE_TOLERANCE,
        },
    }
    report_path = output / "capacity-estimator-0.5.0-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Artifact: {artifact_path}")
    print(f"Report: {report_path}")
    print(f"Promotion: {status}")
    for failure in failures:
        print(f"  - {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
