"""Private income-target projection tests for ADR 0002."""

from __future__ import annotations

from pathlib import Path

import pytest

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.ground_truth import (
    INCOME_TARGET_CONTRACT_VERSION,
    IncomeTargetProjectionError,
    project_income_targets,
)
from finances_simulator.ground_truth.income_targets import _is_horizon_limited


@pytest.fixture(scope="module")
def scenario_root() -> Path:
    return Path(__file__).resolve().parents[1] / "configs" / "scenarios"


@pytest.fixture(scope="module")
def life_events(scenario_root: Path) -> GeneratedScenario:
    config = load_scenario_config(scenario_root / "life_events.yaml")
    return generate_scenario(config, seed=42, months=24)


@pytest.fixture(scope="module")
def incomplete_observation(scenario_root: Path) -> GeneratedScenario:
    config = load_scenario_config(scenario_root / "incomplete_observation.yaml")
    return generate_scenario(config, seed=42, months=12)


def _by_month(generated: GeneratedScenario) -> dict[str, object]:
    return {item.month: item for item in project_income_targets(generated.simulation)}


def test_projection_is_deterministic_and_versioned(life_events: GeneratedScenario) -> None:
    first = project_income_targets(life_events.simulation)
    second = project_income_targets(life_events.simulation)

    assert first == second
    assert len(first) == 24
    assert {item.schema_version for item in first} == {INCOME_TARGET_CONTRACT_VERSION}
    assert [item.month for item in first] == sorted(item.month for item in first)


def test_realized_target_matches_customer_month_truth(
    life_events: GeneratedScenario,
    incomplete_observation: GeneratedScenario,
) -> None:
    """The two realized definitions must never diverge."""

    for generated in (life_events, incomplete_observation):
        truth = {
            item.month: item.true_income_minor
            for item in generated.ground_truth.customer_months
        }
        for target in project_income_targets(generated.simulation):
            assert target.realized_income_month_minor == truth[target.month]


def test_expected_income_equals_realized_when_the_scenario_is_deterministic(
    life_events: GeneratedScenario,
) -> None:
    targets = project_income_targets(life_events.simulation)

    assert sum(item.expected_income_month_minor for item in targets) == sum(
        item.realized_income_month_minor for item in targets
    )


def test_expected_income_tracks_realized_income_across_a_population(
    scenario_root: Path,
) -> None:
    """Volatility and payment probability are zero-mean, so totals must converge."""

    config = load_scenario_config(scenario_root / "income_diverse.yaml")
    population = generate_population(config, population_size=40, seed=9_000, months=12, workers=2)

    realized = 0
    expected = 0
    for member in population.members:
        for target in project_income_targets(member.simulation):
            realized += target.realized_income_month_minor
            expected += target.expected_income_month_minor

    assert expected > 0
    assert abs(realized / expected - 1) < 0.05


def test_life_events_move_sustainable_income(life_events: GeneratedScenario) -> None:
    targets = _by_month(life_events)

    assert targets["2024-01"].sustainable_monthly_income_minor == 509_167
    assert targets["2024-04"].sustainable_monthly_income_minor == 560_083
    assert targets["2024-06"].sustainable_monthly_income_minor == 616_092
    assert targets["2024-10"].sustainable_monthly_income_minor == 712_833


def test_job_loss_zeroes_capacity_and_job_change_restores_it(
    life_events: GeneratedScenario,
) -> None:
    targets = _by_month(life_events)

    for month in ("2024-08", "2024-09"):
        assert targets[month].realized_income_month_minor == 0
        assert targets[month].expected_income_month_minor == 0
        assert targets[month].sustainable_monthly_income_minor == 0
        assert targets[month].expected_income_next_12m_minor == 0
        assert targets[month].active_source_count == 0

    assert targets["2024-10"].active_source_count == 1
    assert targets["2024-10"].recurring_source_count == 1
    assert targets["2024-10"].expected_income_next_12m_minor == 8_554_000


def test_forward_targets_ignore_life_events_after_the_cutoff(
    life_events: GeneratedScenario,
) -> None:
    """July cannot know about the August job loss."""

    targets = _by_month(life_events)

    assert targets["2024-07"].sustainable_monthly_income_minor == 616_092
    assert targets["2024-07"].expected_income_next_12m_minor == 7_393_100


def test_bonus_counts_as_realized_and_expected_but_never_as_sustainable(
    life_events: GeneratedScenario,
) -> None:
    targets = _by_month(life_events)
    november = targets["2024-11"]

    assert november.bonus_income_month_minor == 350_000
    assert november.realized_income_month_minor == 1_050_000
    assert november.expected_income_month_minor == 1_050_000
    assert november.sustainable_monthly_income_minor == 712_833
    assert (
        november.sustainable_monthly_income_minor
        == targets["2024-10"].sustainable_monthly_income_minor
    )


def test_inheritance_is_not_income_under_any_target(life_events: GeneratedScenario) -> None:
    """The December inheritance is a GIFT and must not reach a target."""

    december = _by_month(life_events)["2024-12"]

    assert december.realized_income_month_minor == 924_000
    assert december.bonus_income_month_minor == 0


def test_trailing_twelve_months_is_unavailable_before_a_complete_window(
    life_events: GeneratedScenario,
) -> None:
    targets = project_income_targets(life_events.simulation)

    assert all(item.realized_income_trailing_12m_minor is None for item in targets[:11])
    assert targets[11].realized_income_trailing_12m_minor == 6_423_500
    assert targets[11].realized_income_trailing_12m_minor == sum(
        item.realized_income_month_minor for item in targets[:12]
    )


def test_seasonality_moves_realized_income_without_moving_capacity(
    life_events: GeneratedScenario,
) -> None:
    targets = _by_month(life_events)

    assert targets["2025-07"].realized_income_month_minor == 630_000
    assert targets["2025-12"].realized_income_month_minor == 924_000
    assert (
        targets["2025-07"].sustainable_monthly_income_minor
        == targets["2025-12"].sustainable_monthly_income_minor
        == 712_833
    )


def test_a_schedule_that_fills_the_window_is_not_an_ending_source(
    incomplete_observation: GeneratedScenario,
) -> None:
    """Scenarios size occurrences to the window; that must not read as income ending."""

    run = incomplete_observation.simulation
    assert all(_is_horizon_limited(source, run.months) for source in run.income_sources)

    targets = project_income_targets(run)
    assert {item.sustainable_monthly_income_minor for item in targets} == {600_000}
    assert {item.active_source_count for item in targets} == {1}


def test_a_source_ending_inside_the_window_stops_contributing(
    incomplete_observation: GeneratedScenario,
) -> None:
    """Shorten the schedule so it ends while the window still runs."""

    from dataclasses import replace

    run = incomplete_observation.simulation
    shortened = tuple(
        source.model_copy(update={"occurrences": 6}) for source in run.income_sources
    )
    ending_run = replace(run, income_sources=shortened)

    assert not any(_is_horizon_limited(source, ending_run.months) for source in shortened)

    targets = project_income_targets(ending_run)
    by_month = {item.month: item for item in targets}

    assert by_month["2024-01"].sustainable_monthly_income_minor == 250_000
    assert by_month["2024-04"].recurring_source_count == 1
    assert by_month["2024-05"].recurring_source_count == 0
    assert by_month["2024-06"].active_source_count == 0
    assert by_month["2024-06"].sustainable_monthly_income_minor == 0
    assert by_month["2024-06"].expected_income_next_12m_minor == 0


def test_contracts_without_private_income_sources_are_rejected(scenario_root: Path) -> None:
    config = load_scenario_config(scenario_root / "salaried_loans_investments.yaml")
    generated = generate_scenario(config, seed=42, months=12)

    with pytest.raises(IncomeTargetProjectionError, match="1.3"):
        project_income_targets(generated.simulation)
