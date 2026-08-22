"""Observed-versus-truth reconciliation invariants R1 through R5 from ADR 0004.

The repository validated private ledgers against themselves and never checked the observed feed
against the truth it is derived from. That gap let contract 1.5 ship a feed whose amounts could not
reconcile with truth under any estimator, which reached estimator 0.8 undetected. These tests close
it.
"""

from pathlib import Path

import pytest

from finances_simulator.config import load_scenario_config
from finances_simulator.generation import GeneratedScenario, generate_scenario

RECONCILING_SCENARIOS = ("noisy_observation.yaml", "incomplete_observation.yaml")


def _generate(project_root: Path, scenario: str) -> GeneratedScenario:
    config = load_scenario_config(project_root / "configs" / "scenarios" / scenario)
    return generate_scenario(config, seed=42, months=12)


@pytest.fixture(scope="module", params=RECONCILING_SCENARIOS)
def degraded_run(request: pytest.FixtureRequest, project_root: Path) -> GeneratedScenario:
    return _generate(project_root, request.param)


def _originals(run: GeneratedScenario) -> dict[str, object]:
    return {
        item.transaction_id: item
        for item in run.observations.transactions
        if item.duplicate_of_transaction_id is None
        and item.reversal_of_transaction_id is None
        and item.repost_of_transaction_id is None
    }


def _signed(amount_minor: int, direction: object) -> int:
    return amount_minor if getattr(direction, "value", direction) == "CREDIT" else -amount_minor


def test_r1_observed_originals_carry_the_truth_balance(
    degraded_run: GeneratedScenario,
) -> None:
    """An observed balance is a carried provider report, never a re-derived total."""

    truth_by_id = {item.entry_id: item for item in degraded_run.ground_truth.transactions}
    originals = _originals(degraded_run)
    assert originals
    for transaction_id, observed in originals.items():
        truth = truth_by_id[transaction_id]
        assert observed.balance_after_minor == truth.balance_after_minor
        assert observed.amount_minor == truth.amount_minor
        assert observed.direction == truth.direction
        assert observed.posted_at == truth.occurred_at


def test_r2_reversal_records_restore_the_pre_original_balance(
    degraded_run: GeneratedScenario,
) -> None:
    truth_by_id = {item.entry_id: item for item in degraded_run.ground_truth.transactions}
    reversals = [
        item
        for item in degraded_run.observations.transactions
        if item.reversal_of_transaction_id is not None
    ]
    assert reversals
    for observed in reversals:
        truth = truth_by_id[observed.reversal_of_transaction_id]
        expected = truth.balance_after_minor - _signed(truth.amount_minor, truth.direction)
        assert observed.balance_after_minor == expected
        assert observed.amount_minor == truth.amount_minor
        assert observed.direction != truth.direction
        assert observed.account_id == truth.account_id


def test_r3_duplicate_records_match_their_source(degraded_run: GeneratedScenario) -> None:
    originals = _originals(degraded_run)
    duplicates = [
        item
        for item in degraded_run.observations.transactions
        if item.duplicate_of_transaction_id is not None
    ]
    assert duplicates
    for observed in duplicates:
        source = originals[observed.duplicate_of_transaction_id]
        assert observed.account_id == source.account_id
        assert observed.amount_minor == source.amount_minor
        assert observed.direction == source.direction
        assert observed.currency == source.currency
        assert observed.posted_at == source.posted_at
        assert observed.balance_after_minor == source.balance_after_minor


def test_r4_reposts_restore_the_original_and_follow_their_reversal(
    degraded_run: GeneratedScenario,
) -> None:
    originals = _originals(degraded_run)
    reversal_by_original = {
        item.reversal_of_transaction_id: item
        for item in degraded_run.observations.transactions
        if item.reversal_of_transaction_id is not None
    }
    reposts = [
        item
        for item in degraded_run.observations.transactions
        if item.repost_of_transaction_id is not None
    ]
    assert reposts
    for observed in reposts:
        source = originals[observed.repost_of_transaction_id]
        reversal = reversal_by_original[observed.repost_of_transaction_id]
        assert observed.account_id == source.account_id
        assert observed.amount_minor == source.amount_minor
        assert observed.direction == source.direction
        assert observed.currency == source.currency
        assert observed.balance_after_minor == source.balance_after_minor
        assert observed.observed_at >= reversal.observed_at
        assert observed.observed_at >= observed.posted_at


def test_r5_full_consent_feeds_fold_back_to_the_truth_balance(project_root: Path) -> None:
    """Excluding duplicates, a complete observed feed folds to the truth movement.

    This is the statement contract 1.5 could not make. Each reversal and its correction cancel, so
    a reversed amount is delayed rather than lost. Only `noisy_observation` qualifies: it is the
    shipped scenario at full consent with no missing records.
    """

    run = _generate(project_root, "noisy_observation.yaml")
    settings = load_scenario_config(
        project_root / "configs" / "scenarios" / "noisy_observation.yaml"
    ).observation_degradation
    assert settings.missing_record_basis_points == 0
    assert settings.consent.default_coverage_percent == 100
    assert settings.reversal_record_basis_points > 0

    observed_by_account: dict[str, int] = {}
    for item in run.observations.transactions:
        if item.duplicate_of_transaction_id is not None:
            continue
        observed_by_account[item.account_id] = observed_by_account.get(
            item.account_id, 0
        ) + _signed(item.amount_minor, item.direction)

    truth_by_account: dict[str, int] = {}
    for item in run.ground_truth.transactions:
        truth_by_account[item.account_id] = truth_by_account.get(
            item.account_id, 0
        ) + _signed(item.amount_minor, item.direction)

    assert observed_by_account
    assert observed_by_account == truth_by_account


def test_r5_fails_without_the_correction(project_root: Path) -> None:
    """Guard the guard: dropping re-posts must break R5, or R5 proves nothing."""

    run = _generate(project_root, "noisy_observation.yaml")
    without_reposts: dict[str, int] = {}
    for item in run.observations.transactions:
        if item.duplicate_of_transaction_id is not None:
            continue
        if item.repost_of_transaction_id is not None:
            continue
        without_reposts[item.account_id] = without_reposts.get(
            item.account_id, 0
        ) + _signed(item.amount_minor, item.direction)

    truth_by_account: dict[str, int] = {}
    for item in run.ground_truth.transactions:
        truth_by_account[item.account_id] = truth_by_account.get(
            item.account_id, 0
        ) + _signed(item.amount_minor, item.direction)

    assert without_reposts != truth_by_account
