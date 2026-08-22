"""Acceptance tests for the most degraded shipped scenario.

`noisy_observation.yaml` carries the repository's highest degradation rates and, until ADR 0004,
had only a reproducibility assertion covering it. That is where the contract 1.5 reversal defect
lived, so the feed with the most artifacts now gets a suite of its own.
"""

from dataclasses import fields
from pathlib import Path

import pytest

from finances_simulator.config import load_scenario_config
from finances_simulator.config_v6 import ScenarioConfigV6
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.simulation.primitives import V6_PROFILE


@pytest.fixture(scope="module")
def noisy_config(project_root: Path) -> ScenarioConfigV6:
    loaded = load_scenario_config(
        project_root / "configs" / "scenarios" / "noisy_observation.yaml"
    )
    assert isinstance(loaded, ScenarioConfigV6)
    return loaded


@pytest.fixture(scope="module")
def noisy_run(noisy_config: ScenarioConfigV6) -> GeneratedScenario:
    return generate_scenario(noisy_config, seed=42, months=12)


def test_noisy_scenario_uses_the_current_observation_contract(
    noisy_run: GeneratedScenario,
) -> None:
    assert noisy_run.simulation.profile == V6_PROFILE


def test_noisy_scenario_exercises_every_artifact_kind(
    noisy_config: ScenarioConfigV6,
    noisy_run: GeneratedScenario,
) -> None:
    """The suite is worth having only while it still degrades the feed."""

    settings = noisy_config.observation_degradation
    assert settings.late_record_basis_points > 0
    assert settings.duplicate_record_basis_points > 0
    assert settings.reversal_record_basis_points > 0

    metrics = noisy_run.observations.observation_coverage
    assert sum(item.late_record_count for item in metrics) > 0
    assert sum(item.duplicate_record_count for item in metrics) > 0
    assert sum(item.reversal_record_count for item in metrics) > 0
    assert sum(item.repost_record_count for item in metrics) > 0


def test_every_artifact_resolves_to_an_emitted_original(
    noisy_run: GeneratedScenario,
) -> None:
    records = noisy_run.observations.transactions
    emitted = {item.transaction_id for item in records}
    originals = {
        item.transaction_id
        for item in records
        if item.duplicate_of_transaction_id is None
        and item.reversal_of_transaction_id is None
        and item.repost_of_transaction_id is None
    }
    for item in records:
        for link in (
            item.duplicate_of_transaction_id,
            item.reversal_of_transaction_id,
            item.repost_of_transaction_id,
        ):
            if link is not None:
                assert link in originals
        assert item.transaction_id in emitted
    assert len(emitted) == len(records)


def test_reversal_carries_the_institution_reversal_prefix_and_the_repost_does_not(
    noisy_run: GeneratedScenario,
) -> None:
    """A re-post that still reads as a reversal would be excluded by a description rule."""

    records = noisy_run.observations.transactions
    originals = {item.transaction_id: item for item in records}
    reversals = [item for item in records if item.reversal_of_transaction_id is not None]
    reposts = [item for item in records if item.repost_of_transaction_id is not None]
    assert reversals
    assert reposts

    for item in reversals:
        assert item.description.startswith("NFB | ESTORNO | ")
    for item in reposts:
        assert "ESTORNO" not in item.description
        assert item.description == originals[item.repost_of_transaction_id].description


def test_arrival_never_precedes_posting(noisy_run: GeneratedScenario) -> None:
    for item in noisy_run.observations.transactions:
        assert item.observed_at >= item.posted_at


def test_effective_coverage_counts_originals_only(noisy_run: GeneratedScenario) -> None:
    """Duplicates, reversals, and re-posts must never inflate measured coverage."""

    for metric in noisy_run.observations.observation_coverage:
        assert metric.observed_original_record_count == (
            metric.consented_record_count - metric.missing_record_count
        )
        assert metric.effective_coverage_basis_points == (
            metric.observed_original_record_count * 10_000
            + metric.eligible_record_count // 2
        ) // metric.eligible_record_count
        assert metric.effective_coverage_basis_points <= 10_000


def test_noisy_observations_do_not_leak_private_labels(
    noisy_run: GeneratedScenario,
) -> None:
    forbidden = {
        "economic_type",
        "income_source_id",
        "life_event_id",
        "life_event_ref",
        "life_event_type",
        "anomaly_id",
        "anomaly_ref",
        "anomaly_type",
        "employment_status",
        "active_income_source_ids",
    }
    for bundle_field in fields(noisy_run.observations):
        for record in getattr(noisy_run.observations, bundle_field.name):
            assert forbidden.isdisjoint(record.model_dump())


def test_noisy_generation_is_reproducible(noisy_config: ScenarioConfigV6) -> None:
    assert generate_scenario(noisy_config, seed=42, months=12) == generate_scenario(
        noisy_config, seed=42, months=12
    )
