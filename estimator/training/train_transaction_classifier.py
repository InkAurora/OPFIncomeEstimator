"""Train and evaluate experimental estimator 0.3 transaction classifier."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config

from training.datasets import (
    DATASET_VERSION,
    SPLIT_VERSION,
    build_labeled_dataset,
    split_by_customer,
)
from training.gradient_boosting import fit_gradient_boosted_stumps
from training.metrics import classification_metrics


def _populations(
    project_root: Path,
    population_size: int,
    workers: int,
):
    scenario_root = project_root / "finances_simulator/configs/scenarios"
    specifications = (
        ("income_diverse.yaml", 80_000),
        ("life_events.yaml", 90_000),
        ("incomplete_observation.yaml", 100_000),
    )
    return tuple(
        generate_population(
            load_scenario_config(scenario_root / scenario),
            population_size=population_size,
            seed=seed,
            months=12,
            workers=workers,
        )
        for scenario, seed in specifications
    )


def _promotion(
    baseline: dict[str, object],
    candidate: dict[str, object],
) -> tuple[str, tuple[str, ...]]:
    failures: list[str] = []
    if float(candidate["f1"]) <= float(baseline["f1"]):
        failures.append("test F1 must strictly improve over rule baseline")
    baseline_rates = baseline["critical_false_positive_rates"]
    candidate_rates = candidate["critical_false_positive_rates"]
    for economic_type, baseline_rate in baseline_rates.items():
        if float(candidate_rates[economic_type]) > float(baseline_rate):
            failures.append(f"{economic_type} false-positive rate increased")
    return ("PROMOTED" if not failures else "NOT_PROMOTED", tuple(failures))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "artifacts")
    parser.add_argument("--population-size-per-suite", type=int, default=120)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)

    populations = _populations(
        args.project_root.resolve(),
        args.population_size_per_suite,
        args.workers,
    )
    records = build_labeled_dataset(populations)
    partitions = split_by_customer(records)
    artifact = fit_gradient_boosted_stumps(
        partitions["train"],
        partitions["validation"],
    )
    metrics = {
        partition: {
            "baseline": classification_metrics(items),
            "candidate": classification_metrics(items, artifact=artifact),
        }
        for partition, items in partitions.items()
    }
    status, failures = _promotion(
        metrics["test"]["baseline"],
        metrics["test"]["candidate"],
    )

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact_path = output / "transaction-classifier-0.3.0.json"
    artifact_bytes = (artifact.model_dump_json(indent=2) + "\n").encode()
    artifact_path.write_bytes(artifact_bytes)
    report = {
        "schema_version": "1.0",
        "dataset_version": DATASET_VERSION,
        "split_version": SPLIT_VERSION,
        "model_version": artifact.model_version,
        "feature_version": artifact.feature_version,
        "simulator_version": "0.7.0",
        "source_contract_versions": ["1.3", "1.4", "1.5"],
        "population_size_per_suite": args.population_size_per_suite,
        "customer_counts": {
            name: len({record.customer_id for record in items})
            for name, items in partitions.items()
        },
        "record_counts": {name: len(items) for name, items in partitions.items()},
        "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "metrics": metrics,
        "promotion": {"status": status, "failures": failures},
    }
    report_path = output / "transaction-classifier-0.3.0-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Artifact: {artifact_path}")
    print(f"Report: {report_path}")
    print(f"Promotion: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
