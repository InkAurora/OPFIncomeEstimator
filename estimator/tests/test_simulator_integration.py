from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config
from finances_simulator.integration import build_estimator_input_v1_1, evaluate_population

from income_estimator.features import build_customer_month_features
from income_estimator.pipeline import RecurringIncomeEstimator, RuleBasedIncomeEstimator

PRIVATE_TRUTH_FIELDS = (
    "economic_type",
    "is_income",
    "income_source_id",
    "is_self_transfer",
    "truth_transaction_id",
    "life_event_id",
)


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


def test_customer_month_features_discover_products_only_after_they_are_observed() -> None:
    simulator_root = Path(__file__).parents[2] / "finances_simulator"
    config = load_scenario_config(
        simulator_root / "configs/scenarios/salaried_loans_investments.yaml"
    )
    population = generate_population(
        config,
        population_size=1,
        seed=70_000,
        months=12,
        workers=1,
    )
    request = build_estimator_input_v1_1(population.members[0])

    first = build_customer_month_features(request)
    second = build_customer_month_features(request)

    assert first == second
    assert first.input_contract_version == "1.1"
    assert len(first.rows) == 12

    opening = first.rows[0].to_mapping()
    following = first.rows[1].to_mapping()
    assert opening["window_months"] == 1
    assert opening["observed_loan_count"] is None
    assert following["observed_loan_count"] == 1
    assert following["observed_domain_count"] > opening["observed_domain_count"]

    trailing_income = [row.to_mapping()["income_12m_minor"] for row in first.rows]
    assert trailing_income == sorted(trailing_income)

    payload = first.model_dump_json()
    assert all(field not in payload for field in PRIVATE_TRUTH_FIELDS)
