"""Deterministic held-out comparison for estimator versions 0.1 and 0.2."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean

from finances_simulator.batch import GeneratedPopulation, generate_population
from finances_simulator.config import load_scenario_config
from finances_simulator.integration import PopulationEvaluation, evaluate_population

from income_estimator import RecurringIncomeEstimator, RuleBasedIncomeEstimator
from income_estimator.pipeline import FEATURE_VERSION, RECURRING_FEATURE_VERSION

BENCHMARK_SCHEMA_VERSION = "1.0"
DATASET_VERSION = "synthetic-heldout-1.0.0"


@dataclass(frozen=True, slots=True)
class BenchmarkSuite:
    name: str
    scenario_path: Path
    first_seed: int
    population_size: int = 100
    months: int = 12


def default_suites(project_root: Path, population_size: int = 100) -> tuple[BenchmarkSuite, ...]:
    scenario_root = project_root / "finances_simulator/configs/scenarios"
    return (
        BenchmarkSuite(
            name="complete_income_diverse",
            scenario_path=scenario_root / "income_diverse.yaml",
            first_seed=20_000,
            population_size=population_size,
        ),
        BenchmarkSuite(
            name="incomplete_observation",
            scenario_path=scenario_root / "incomplete_observation.yaml",
            first_seed=30_000,
            population_size=population_size,
        ),
        BenchmarkSuite(
            name="life_events",
            scenario_path=scenario_root / "life_events.yaml",
            first_seed=40_000,
            population_size=population_size,
        ),
    )


def _points(
    population: GeneratedPopulation,
    evaluation: PopulationEvaluation,
) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    for generated, estimate in zip(population.members, evaluation.estimates, strict=True):
        predictions = {
            item.month: item.estimated_income_minor for item in estimate.monthly_estimates
        }
        result.extend(
            (truth.true_income_minor, predictions[truth.month])
            for truth in generated.ground_truth.customer_months
        )
    return tuple(result)


def _extended_metrics(
    evaluation: PopulationEvaluation,
    points: tuple[tuple[int, int], ...],
) -> dict[str, object]:
    errors = [abs(predicted - truth) for truth, predicted in points]
    squared_errors = [(predicted - truth) ** 2 for truth, predicted in points]
    true_total = sum(abs(truth) for truth, _ in points)
    smape_terms = [
        0.0
        if abs(truth) + abs(predicted) == 0
        else 2 * abs(predicted - truth) / (abs(truth) + abs(predicted))
        for truth, predicted in points
    ]
    intervals = [
        monthly.confidence_upper_minor - monthly.confidence_lower_minor
        for estimate in evaluation.estimates
        for monthly in estimate.monthly_estimates
    ]
    return {
        "customer_month_count": len(points),
        "mean_absolute_error_minor": evaluation.report.overall.mean_absolute_error_minor,
        "median_absolute_error_minor": evaluation.report.overall.median_absolute_error_minor,
        "root_mean_squared_error_minor": round(math.sqrt(fmean(squared_errors)), 6),
        "weighted_absolute_percentage_error": (
            round(sum(errors) / true_total, 8) if true_total else 0.0
        ),
        "symmetric_mean_absolute_percentage_error": round(fmean(smape_terms), 8),
        "mean_interval_width_minor": round(fmean(intervals), 6),
        "confidence_interval_coverage": (
            evaluation.report.confidence_interval_coverage.coverage_rate
        ),
        "false_income_classification_rate": (
            evaluation.report.false_income_classification.false_classification_rate
        ),
        "by_income_type": [
            item.model_dump(mode="json") for item in evaluation.report.by_income_type
        ],
        "by_consent_coverage": [
            item.model_dump(mode="json")
            for item in evaluation.report.by_consent_coverage
        ],
    }


def _comparison(
    baseline: dict[str, object],
    candidate: dict[str, object],
) -> dict[str, object]:
    baseline_mae = float(baseline["mean_absolute_error_minor"] or 0)
    candidate_mae = float(candidate["mean_absolute_error_minor"] or 0)
    improvement = baseline_mae - candidate_mae
    return {
        "mae_improvement_minor": round(improvement, 6),
        "mae_improvement_percent": (
            round(improvement / baseline_mae, 8) if baseline_mae else 0.0
        ),
        "candidate_does_not_regress": candidate_mae <= baseline_mae,
        "candidate_false_positive_rate_not_higher": (
            float(candidate["false_income_classification_rate"])
            <= float(baseline["false_income_classification_rate"])
        ),
    }


def run_benchmark(
    suites: tuple[BenchmarkSuite, ...],
    *,
    workers: int = 1,
) -> tuple[dict[str, object], dict[str, dict[str, tuple[tuple[int, int], ...]]]]:
    """Run fixed observed inference first, then aggregate against private truth."""

    suite_reports: list[dict[str, object]] = []
    chart_points: dict[str, dict[str, tuple[tuple[int, int], ...]]] = {}
    promotion_checks: list[bool] = []

    for suite in suites:
        config = load_scenario_config(suite.scenario_path)
        population = generate_population(
            config,
            population_size=suite.population_size,
            seed=suite.first_seed,
            months=suite.months,
            workers=workers,
        )
        baseline_evaluation = evaluate_population(population, RuleBasedIncomeEstimator())
        candidate_evaluation = evaluate_population(population, RecurringIncomeEstimator())
        baseline_points = _points(population, baseline_evaluation)
        candidate_points = _points(population, candidate_evaluation)
        baseline_metrics = _extended_metrics(baseline_evaluation, baseline_points)
        candidate_metrics = _extended_metrics(candidate_evaluation, candidate_points)
        comparison = _comparison(baseline_metrics, candidate_metrics)
        improves_required_suite = (
            suite.name != "incomplete_observation"
            or float(comparison["mae_improvement_minor"]) > 0
        )
        promotion_checks.extend(
            (
                bool(comparison["candidate_does_not_regress"]),
                bool(comparison["candidate_false_positive_rate_not_higher"]),
                improves_required_suite,
            )
        )
        source_profile = population.members[0].simulation.profile
        suite_reports.append(
            {
                "suite": suite.name,
                "scenario": suite.scenario_path.name,
                "batch_id": population.batch_id,
                "config_sha256": population.config_sha256,
                "first_seed": suite.first_seed,
                "last_seed": suite.first_seed + suite.population_size - 1,
                "population_size": suite.population_size,
                "months": suite.months,
                "source_simulator_version": source_profile.simulator_version,
                "source_contract_schema_version": (
                    source_profile.contract_schema_version
                ),
                "rng_algorithm_version": source_profile.rng_algorithm,
                "baseline": {
                    "estimator_version": baseline_evaluation.report.estimator_version,
                    **baseline_metrics,
                },
                "candidate": {
                    "estimator_version": candidate_evaluation.report.estimator_version,
                    **candidate_metrics,
                },
                "comparison": comparison,
            }
        )
        chart_points[suite.name] = {
            "rule-based-0.1.0": baseline_points,
            "recurring-streams-0.2.0": candidate_points,
        }

    report = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "trust_boundary": "aggregate_private_truth_evaluation",
        "simulator_version": "0.7.0",
        "estimator_contract_version": "1.0",
        "feature_versions": {
            "rule-based-0.1.0": FEATURE_VERSION,
            "recurring-streams-0.2.0": RECURRING_FEATURE_VERSION,
        },
        "model_versions": [],
        "suites": suite_reports,
        "promotion": {
            "status": "PASS" if all(promotion_checks) else "FAIL",
            "required_checks": (
                "candidate_does_not_regress",
                "candidate_false_positive_rate_not_higher",
                "candidate_improves_incomplete_observation",
            ),
        },
    }
    return report, chart_points


def write_report(report: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def render_true_vs_estimated_svg(
    points_by_estimator: dict[str, tuple[tuple[int, int], ...]],
    path: Path,
    *,
    suite_name: str,
) -> None:
    """Render dependency-free aggregate scatter plot without customer identifiers."""

    panel_width = 450
    panel_height = 350
    lefts = (80, 610)
    top = 80
    all_values = [
        value
        for points in points_by_estimator.values()
        for pair in points
        for value in pair
    ]
    maximum = max(all_values, default=1)
    axis_max = max(1, math.ceil(maximum / 100_000) * 100_000)
    colors = ("#64748b", "#0f766e")

    lines = [
        (
            '<svg xmlns="http://www.w3.org/2000/svg" width="1080" '
            'height="520" viewBox="0 0 1080 520">'
        ),
        '<rect width="1080" height="520" fill="#f8fafc"/>',
        (
            '<text x="540" y="32" text-anchor="middle" font-family="sans-serif" '
            'font-size="20" font-weight="700" fill="#0f172a">'
            "True vs estimated monthly income</text>"
        ),
        (
            '<text x="540" y="55" text-anchor="middle" font-family="sans-serif" '
            f'font-size="13" fill="#475569">{suite_name} · aggregate held-out '
            "customer-months · minor units</text>"
        ),
    ]

    for panel_index, (estimator_version, points) in enumerate(points_by_estimator.items()):
        left = lefts[panel_index]
        bottom = top + panel_height
        color = colors[panel_index]
        lines.extend(
            (
                (
                    f'<rect x="{left}" y="{top}" width="{panel_width}" '
                    f'height="{panel_height}" fill="#ffffff" stroke="#cbd5e1"/>'
                ),
                (
                    f'<line x1="{left}" y1="{bottom}" x2="{left + panel_width}" '
                    f'y2="{top}" stroke="#94a3b8" stroke-dasharray="5 5"/>'
                ),
                (
                    f'<text x="{left + panel_width / 2:.1f}" y="{top - 12}" '
                    'text-anchor="middle" font-family="sans-serif" font-size="14" '
                    f'font-weight="600" fill="#0f172a">{estimator_version}</text>'
                ),
                (
                    f'<text x="{left + panel_width / 2:.1f}" y="{bottom + 42}" '
                    'text-anchor="middle" font-family="sans-serif" font-size="12" '
                    'fill="#334155">true income</text>'
                ),
                (
                    f'<text x="{left - 48}" y="{top + panel_height / 2:.1f}" '
                    f'transform="rotate(-90 {left - 48} '
                    f'{top + panel_height / 2:.1f})" text-anchor="middle" '
                    'font-family="sans-serif" font-size="12" fill="#334155">'
                    "estimated income</text>"
                ),
                (
                    f'<text x="{left}" y="{bottom + 18}" text-anchor="middle" '
                    'font-family="sans-serif" font-size="10" fill="#64748b">0</text>'
                ),
                (
                    f'<text x="{left + panel_width}" y="{bottom + 18}" '
                    'text-anchor="middle" font-family="sans-serif" font-size="10" '
                    f'fill="#64748b">{axis_max}</text>'
                ),
                (
                    f'<text x="{left - 8}" y="{top + 4}" text-anchor="end" '
                    'font-family="sans-serif" font-size="10" '
                    f'fill="#64748b">{axis_max}</text>'
                ),
            )
        )
        counts = Counter(points)
        for (truth, predicted), count in sorted(counts.items()):
            x = left + panel_width * truth / axis_max
            y = bottom - panel_height * predicted / axis_max
            radius = min(11.0, 2.5 + math.log2(count + 1))
            lines.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" '
                f'fill="{color}" fill-opacity="0.68"><title>{count} '
                f"customer-months; true={truth}; estimated={predicted}"
                "</title></circle>"
            )

    lines.extend(
        (
            (
                '<text x="540" y="500" text-anchor="middle" '
                'font-family="sans-serif" font-size="11" fill="#64748b">'
                "Circle size represents repeated customer-month count. Dashed line "
                "marks perfect estimates.</text>"
            ),
            "</svg>",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "DATASET_VERSION",
    "BenchmarkSuite",
    "default_suites",
    "render_true_vs_estimated_svg",
    "run_benchmark",
    "write_report",
]
