from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config
from finances_simulator.integration import evaluate_population

from income_estimator.pipeline import RuleBasedIncomeEstimator


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
