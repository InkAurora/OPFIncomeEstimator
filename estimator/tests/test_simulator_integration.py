from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config
from finances_simulator.integration import build_estimator_input_v1_1, evaluate_population

from income_estimator.pipeline import RecurringIncomeEstimator, RuleBasedIncomeEstimator


def test_estimator_runs_through_simulator_boundary() -> None:
    simulator_root = Path(__file__).parents[2] / "finances_simulator"
    config = load_scenario_config(simulator_root / "configs/scenarios/salaried_basic.yaml")
    population = generate_population(
        config,
        population_size=1,
        seed=42,
        months=3,
        workers=1,
    )

    evaluation = evaluate_population(population, RuleBasedIncomeEstimator())

    assert evaluation.report.estimator_version == "rule-based-0.1.0"
    assert evaluation.report.overall.count == 3
    assert evaluation.report.overall.mean_absolute_error_minor == 0
    assert evaluation.report.false_income_classification.false_classification_count == 0


def test_recurring_reconstruction_improves_incomplete_observation() -> None:
    simulator_root = Path(__file__).parents[2] / "finances_simulator"
    config = load_scenario_config(
        simulator_root / "configs/scenarios/incomplete_observation.yaml"
    )
    population = generate_population(
        config,
        population_size=20,
        seed=10_000,
        months=12,
        workers=2,
    )

    baseline = evaluate_population(population, RuleBasedIncomeEstimator()).report
    recurring = evaluate_population(population, RecurringIncomeEstimator()).report

    assert recurring.estimator_version == "recurring-streams-0.2.0"
    assert recurring.overall.mean_absolute_error_minor < baseline.overall.mean_absolute_error_minor
    assert recurring.false_income_classification.false_classification_count == 0


def test_simulator_builds_allow_listed_input_1_1() -> None:
    simulator_root = Path(__file__).parents[2] / "finances_simulator"
    config = load_scenario_config(
        simulator_root / "configs/scenarios/incomplete_observation.yaml"
    )
    population = generate_population(
        config,
        population_size=1,
        seed=60_000,
        months=3,
        workers=1,
    )

    request = build_estimator_input_v1_1(population.members[0])
    estimate = RecurringIncomeEstimator().estimate(request)

    assert request.schema_version == "1.1"
    assert request.balances
    assert all(item.balance_after_minor is not None for item in request.transactions)
    assert all(item.counterparty_name is None for item in request.transactions)
    assert estimate.customer_id == request.customer_id
