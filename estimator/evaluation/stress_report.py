"""Stress evaluation across named suites, reported separately rather than pooled.

The plan requires each suite to be reported on its own, because a pooled average hides exactly the
regime where an estimator fails. Every suite here also declares whether the promoted models were
trained on its conditions.

That distinction matters more than it looks. The capacity model was trained on `income_diverse`,
`life_events`, and `incomplete_observation`, so results on those suites measure generalization to
new customers. `noisy_observation` and `high_volatility` were never in training, so they measure
generalization to new conditions, which is the harder claim and the one a stress suite exists to
test.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config
from finances_simulator.ground_truth import project_income_targets
from finances_simulator.integration import build_estimator_input_v1_2

from income_estimator.pipeline import EnsembleIncomeEstimator

STRESS_REPORT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class StressSuite:
    name: str
    scenario: str
    seed: int
    in_training_distribution: bool
    description: str


SUITES: tuple[StressSuite, ...] = (
    StressSuite(
        name="clean",
        scenario="salaried_basic.yaml",
        seed=610_000,
        in_training_distribution=False,
        description="single salaried account, complete observation, no products",
    ),
    StressSuite(
        name="normal",
        scenario="income_diverse.yaml",
        seed=620_000,
        in_training_distribution=True,
        description="mixed income profiles with complete observation",
    ),
    StressSuite(
        name="partial_consent",
        scenario="incomplete_observation.yaml",
        seed=630_000,
        in_training_distribution=True,
        description="stable salary behind partial consent and a degraded feed",
    ),
    StressSuite(
        name="life_events",
        scenario="life_events.yaml",
        seed=640_000,
        in_training_distribution=True,
        description="raises, promotions, job loss, job change, bonus, inheritance",
    ),
    StressSuite(
        name="noisy",
        scenario="noisy_observation.yaml",
        seed=650_000,
        in_training_distribution=False,
        description="income-shaped non-income credits with a late, duplicated, reversed feed",
    ),
    StressSuite(
        name="high_volatility",
        scenario="high_volatility.yaml",
        seed=660_000,
        in_training_distribution=False,
        description="irregular self-employed and business income on a clean feed",
    ),
)


def _metrics(errors: list[int], truths: list[int]) -> dict[str, object]:
    if not errors:
        return {"count": 0}
    truth_total = sum(truths)
    return {
        "count": len(errors),
        "mean_absolute_error_minor": round(fmean(errors), 4),
        "wape": round(sum(errors) / truth_total, 8) if truth_total else None,
        "mean_truth_minor": round(fmean(truths), 4),
    }


def evaluate_suite(
    suite: StressSuite,
    *,
    project_root: Path,
    estimator: EnsembleIncomeEstimator,
    population_size: int,
    months: int,
    workers: int,
) -> dict[str, object]:
    """Run one suite end to end and report realized and sustainable error separately."""

    scenario_root = project_root / "finances_simulator/configs/scenarios"
    population = generate_population(
        load_scenario_config(scenario_root / suite.scenario),
        population_size=population_size,
        seed=suite.seed,
        months=months,
        workers=workers,
    )

    realized_errors: list[int] = []
    realized_truths: list[int] = []
    sustainable_errors: list[int] = []
    sustainable_truths: list[int] = []
    covered = 0
    interval_count = 0
    confidence_scores: list[int] = []
    false_income_months = 0
    targets_available = True

    for generated in population.members:
        request = build_estimator_input_v1_2(generated)
        estimate = estimator.estimate_v1_1(request)
        realized_truth = {
            item.month: item.true_income_minor
            for item in generated.ground_truth.customer_months
        }
        try:
            sustainable_truth = {
                item.month: item.sustainable_monthly_income_minor
                for item in project_income_targets(generated.simulation)
            }
        except ValueError:
            targets_available = False
            sustainable_truth = {}

        for month in estimate.monthly_estimates:
            truth = realized_truth.get(month.month)
            if truth is not None:
                realized_errors.append(abs(month.realized_income_estimate_minor - truth))
                realized_truths.append(truth)
                if truth == 0 and month.realized_income_estimate_minor > 0:
                    false_income_months += 1
            if month.confidence_score_basis_points is not None:
                confidence_scores.append(month.confidence_score_basis_points)

            expected = sustainable_truth.get(month.month)
            if expected is None or month.sustainable_income_p50_minor is None:
                continue
            sustainable_errors.append(abs(month.sustainable_income_p50_minor - expected))
            sustainable_truths.append(expected)
            if (
                month.sustainable_income_p10_minor is not None
                and month.sustainable_income_p90_minor is not None
            ):
                interval_count += 1
                covered += int(
                    month.sustainable_income_p10_minor
                    <= expected
                    <= month.sustainable_income_p90_minor
                )

    return {
        "suite": suite.name,
        "scenario": suite.scenario,
        "seed": suite.seed,
        "in_training_distribution": suite.in_training_distribution,
        "description": suite.description,
        "customer_count": len(population.members),
        "realized_income": _metrics(realized_errors, realized_truths),
        "sustainable_income": (
            _metrics(sustainable_errors, sustainable_truths)
            if targets_available
            else {"count": 0, "unavailable_reason": "CONTRACT_BELOW_1_3"}
        ),
        "interval_coverage": (
            round(covered / interval_count, 6) if interval_count else None
        ),
        "interval_count": interval_count,
        "mean_confidence_basis_points": (
            round(fmean(confidence_scores), 4) if confidence_scores else None
        ),
        "false_income_month_count": false_income_months,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument(
        "--capacity-model",
        type=Path,
        default=Path(__file__).parents[1] / "training/artifacts/capacity-estimator-0.6.0.json",
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=Path(__file__).parents[1] / "training/artifacts/quantile-calibration-0.8.0.json",
    )
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "baselines")
    parser.add_argument("--population-size", type=int, default=20)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)

    estimator = EnsembleIncomeEstimator(
        args.capacity_model.resolve(),
        calibration_path=args.calibration.resolve(),
    )
    project_root = args.project_root.resolve()
    suites = [
        evaluate_suite(
            suite,
            project_root=project_root,
            estimator=estimator,
            population_size=args.population_size,
            months=args.months,
            workers=args.workers,
        )
        for suite in SUITES
    ]

    report = {
        "schema_version": STRESS_REPORT_SCHEMA_VERSION,
        "estimator_version": estimator.estimator_version,
        "model_versions": list(estimator.model_versions),
        "population_size": args.population_size,
        "months": args.months,
        "suites": suites,
        "held_out_conditions": [
            item["suite"] for item in suites if not item["in_training_distribution"]
        ],
    }
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / "stress-0.8.0-report.json"
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Report: {path}")
    for item in suites:
        realized = item["realized_income"].get("wape")
        sustainable = item["sustainable_income"].get("wape")
        print(
            f"  {item['suite']:16} realized_wape={realized} "
            f"sustainable_wape={sustainable} coverage={item['interval_coverage']} "
            f"false_income_months={item['false_income_month_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
