from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config
from finances_simulator.integration import (
    build_estimator_input_v1_1,
    build_estimator_input_v1_2,
    evaluate_population,
)

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


def test_simulator_builds_product_aware_input_1_2() -> None:
    simulator_root = Path(__file__).parents[2] / "finances_simulator"
    config = load_scenario_config(
        simulator_root / "configs/scenarios/salaried_loans_investments.yaml"
    )
    population = generate_population(
        config,
        population_size=1,
        seed=42,
        months=12,
        workers=1,
    )

    request = build_estimator_input_v1_2(population.members[0])

    assert request.schema_version == "1.2"
    assert request.credit_cards and request.credit_limits and request.card_transactions
    assert request.loan_payments and request.loan_balances
    assert request.investments and request.investment_balances
    assert all(item.customer_id == request.customer_id for item in request.credit_limits)

    table = build_customer_month_features(request)
    opening = table.rows[0].to_mapping()
    closing = table.rows[-1].to_mapping()

    assert table.input_contract_version == "1.2"
    assert closing["credit_utilization_ratio"] is not None
    assert closing["investment_balance_minor"] > 0
    assert closing["outstanding_debt_minor"] > 0
    assert closing["observed_domain_count"] == 5
    assert closing["data_completeness_score_basis_points"] == 10_000
    assert closing["observed_domain_count"] > opening["observed_domain_count"]

    payload = table.model_dump_json()
    assert all(field not in payload for field in PRIVATE_TRUTH_FIELDS)


def test_stress_suites_are_reported_separately_with_training_provenance() -> None:
    from evaluation.stress_report import SUITES, evaluate_suite
    from income_estimator.pipeline import EnsembleIncomeEstimator

    project_root = Path(__file__).parents[2]
    artifacts = project_root / "estimator" / "training" / "artifacts"
    estimator = EnsembleIncomeEstimator(
        artifacts / "capacity-estimator-0.5.0.json",
        calibration_path=artifacts / "quantile-calibration-0.7.0.json",
    )
    held_out = [suite for suite in SUITES if not suite.in_training_distribution]
    assert {suite.name for suite in held_out} >= {"noisy", "high_volatility"}

    noisy = next(suite for suite in SUITES if suite.name == "noisy")
    result = evaluate_suite(
        noisy,
        project_root=project_root,
        estimator=estimator,
        population_size=3,
        months=12,
        workers=1,
    )

    assert result["in_training_distribution"] is False
    assert result["realized_income"]["count"] == 36
    assert result["sustainable_income"]["count"] > 0
    assert result["false_income_month_count"] == 0
    assert result["mean_confidence_basis_points"] is not None


def test_a_scenario_below_contract_1_3_reports_targets_as_unavailable() -> None:
    from evaluation.stress_report import SUITES, evaluate_suite
    from income_estimator.pipeline import EnsembleIncomeEstimator

    project_root = Path(__file__).parents[2]
    clean = next(suite for suite in SUITES if suite.name == "clean")

    result = evaluate_suite(
        clean,
        project_root=project_root,
        estimator=EnsembleIncomeEstimator(),
        population_size=2,
        months=6,
        workers=1,
    )

    assert result["sustainable_income"]["unavailable_reason"] == "CONTRACT_BELOW_1_3"
    assert result["realized_income"]["count"] == 12
