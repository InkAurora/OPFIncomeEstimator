"""Acceptance tests for deterministic V0 salaried simulations."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from finances_simulator.cli import main
from finances_simulator.config import ScenarioConfig, load_scenario_config
from finances_simulator.domain.accounts import Direction
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.observations.contracts import (
    ObservedAccount,
    ObservedBalance,
    ObservedTransaction,
)
from finances_simulator.outputs import OutputDirectoryNotEmptyError, write_run
from finances_simulator.simulation.primitives import month_start, scheduled_date
from finances_simulator.validation import validate_reconciliation

FORBIDDEN_OBSERVED_FIELDS = {
    "event_id",
    "economic_type",
    "income_source_id",
    "caused_by_event_id",
    "metadata",
    "source_entity",
    "destination_entity",
}


def _events_with_expense_kind(
    generated: GeneratedScenario, expense_kind: str
) -> tuple[FinancialEvent, ...]:
    return tuple(
        event
        for event in generated.simulation.events
        if event.metadata.get("expense_kind") == expense_kind
    )


def _all_mapping_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for nested in value.values():
            keys.update(_all_mapping_keys(nested))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for nested in value:
            keys.update(_all_mapping_keys(nested))
        return keys
    return set()


def _assert_no_truth_fields(field_names: set[str]) -> None:
    assert field_names.isdisjoint(FORBIDDEN_OBSERVED_FIELDS)
    assert not any(name.startswith("true_") for name in field_names)


def _output_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _output_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for relative_path, payload in _output_tree(root).items():
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def test_example_configuration_loads(example_config_path: Path) -> None:
    config = load_scenario_config(example_config_path)

    assert config.scenario.name == "salaried_basic"
    assert config.scenario.default_months == 24
    assert config.customer.currency == "BRL"
    assert len(config.fixed_expenses) == 5


def test_seed_42_generates_complete_24_month_run(
    generated_seed_42: GeneratedScenario,
) -> None:
    run = generated_seed_42.simulation

    assert run.seed == 42
    assert run.months == 24
    assert run.start_date.isoformat() == "2024-01-01"
    assert run.end_date.isoformat() == "2025-12-31"
    assert len(generated_seed_42.ground_truth.customer_months) == 24
    assert len(generated_seed_42.observations.balances) == 24


def test_salary_occurs_once_on_each_scheduled_day(
    scenario_config: ScenarioConfig,
    generated_seed_42: GeneratedScenario,
) -> None:
    salary_events = tuple(
        event
        for event in generated_seed_42.simulation.events
        if event.economic_type is EconomicType.INCOME
    )
    expected_dates = tuple(
        scheduled_date(
            month_start(scenario_config.scenario.start_date, month_index),
            scenario_config.salary.day_of_month,
        )
        for month_index in range(24)
    )

    assert len(salary_events) == 24
    assert tuple(event.occurred_at for event in salary_events) == expected_dates
    assert all(event.amount_minor == scenario_config.salary.amount_minor for event in salary_events)
    assert all(event.metadata.get("income_kind") == "SALARY" for event in salary_events)


def test_true_monthly_income_contains_salary_only(
    scenario_config: ScenarioConfig,
    generated_seed_42: GeneratedScenario,
) -> None:
    monthly_truth = generated_seed_42.ground_truth.customer_months
    transaction_truth = generated_seed_42.ground_truth.transactions
    income_transactions = tuple(
        record for record in transaction_truth if record.economic_type is EconomicType.INCOME
    )

    assert len(monthly_truth) == 24
    assert all(
        record.true_income_minor == scenario_config.salary.amount_minor for record in monthly_truth
    )
    assert all(record.income_event_count == 1 for record in monthly_truth)
    assert len(income_transactions) == 24
    assert all(
        record.amount_minor == scenario_config.salary.amount_minor
        and record.income_source_id == generated_seed_42.simulation.customer_twin.income_source_id
        for record in income_transactions
    )


def test_fixed_and_variable_expenses_follow_monthly_rules(
    scenario_config: ScenarioConfig,
    generated_seed_42: GeneratedScenario,
) -> None:
    fixed_events = _events_with_expense_kind(generated_seed_42, "FIXED")
    variable_events = _events_with_expense_kind(generated_seed_42, "VARIABLE")
    fixed_by_month: dict[str, list[FinancialEvent]] = defaultdict(list)
    variable_by_month: dict[str, list[FinancialEvent]] = defaultdict(list)
    for event in fixed_events:
        fixed_by_month[event.occurred_at.strftime("%Y-%m")].append(event)
    for event in variable_events:
        variable_by_month[event.occurred_at.strftime("%Y-%m")].append(event)

    fixed_rules = {rule.rule_id: rule for rule in scenario_config.fixed_expenses}
    merchant_pairs = {
        (merchant.entity, merchant.description)
        for merchant in scenario_config.variable_expenses.merchants
    }
    variable_rule = scenario_config.variable_expenses

    assert len(fixed_events) == 24 * 5
    for month_index in range(24):
        current_month = month_start(scenario_config.scenario.start_date, month_index)
        month_key = current_month.strftime("%Y-%m")
        month_fixed = fixed_by_month[month_key]
        month_variable = variable_by_month[month_key]

        assert len(month_fixed) == 5
        assert {event.metadata["rule_id"] for event in month_fixed} == set(fixed_rules)
        for event in month_fixed:
            rule = fixed_rules[str(event.metadata["rule_id"])]
            assert event.economic_type is EconomicType.EXPENSE
            assert event.amount_minor == rule.amount_minor
            assert event.occurred_at == scheduled_date(current_month, rule.day_of_month)

        assert variable_rule.count_min <= len(month_variable) <= variable_rule.count_max
        for event in month_variable:
            assert event.economic_type is EconomicType.EXPENSE
            assert variable_rule.amount_min_minor <= event.amount_minor
            assert event.amount_minor <= variable_rule.amount_max_minor
            assert variable_rule.day_min <= event.occurred_at.day <= variable_rule.day_max
            assert (event.destination_entity, event.description) in merchant_pairs


@pytest.mark.parametrize("seed", range(20))
def test_ledger_and_monthly_balances_reconcile_for_many_seeds(
    seed: int,
    scenario_config: ScenarioConfig,
) -> None:
    generated = generate_scenario(scenario_config, seed=seed, months=24)
    run = generated.simulation
    account = run.customer_twin.primary_account
    closing_balance = validate_reconciliation(account, run.ledger_entries)
    credits = sum(
        entry.amount_minor for entry in run.ledger_entries if entry.direction is Direction.CREDIT
    )
    debits = sum(
        entry.amount_minor for entry in run.ledger_entries if entry.direction is Direction.DEBIT
    )

    assert closing_balance == account.opening_balance_minor + credits - debits
    assert generated.ground_truth.customer_months[0].opening_balance_minor == (
        account.opening_balance_minor
    )
    for previous, current in zip(
        generated.ground_truth.customer_months,
        generated.ground_truth.customer_months[1:],
        strict=False,
    ):
        assert current.opening_balance_minor == previous.closing_balance_minor
    assert generated.ground_truth.customer_months[-1].closing_balance_minor == closing_balance
    assert generated.observations.balances[-1].balance_minor == closing_balance


@pytest.mark.parametrize(
    "model_type",
    (ObservedAccount, ObservedBalance, ObservedTransaction),
)
def test_observation_models_exclude_ground_truth_fields(
    model_type: type[BaseModel],
) -> None:
    _assert_no_truth_fields(set(model_type.model_fields))


def test_serialized_observations_exclude_ground_truth_fields(
    tmp_path: Path,
    generated_seed_42: GeneratedScenario,
) -> None:
    output_directory = tmp_path / "run"
    write_run(generated_seed_42, output_directory)

    for path in (output_directory / "observed").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            _assert_no_truth_fields(_all_mapping_keys(json.loads(line)))


def test_private_and_observed_outputs_are_physically_separated(
    tmp_path: Path,
    generated_seed_42: GeneratedScenario,
) -> None:
    output_directory = tmp_path / "run"
    manifest_path = write_run(generated_seed_42, output_directory)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert {path.name for path in (output_directory / "private").iterdir()} == {
        "customer_ground_truth.jsonl",
        "customer_month_ground_truth.jsonl",
        "transaction_ground_truth.jsonl",
    }
    assert {path.name for path in (output_directory / "observed").iterdir()} == {
        "accounts.jsonl",
        "balances.jsonl",
        "transactions.jsonl",
    }
    assert set(manifest["datasets"]) == {"private", "observed"}
    assert all(
        metadata["path"].startswith("private/")
        for metadata in manifest["datasets"]["private"].values()
    )
    assert all(
        metadata["path"].startswith("observed/")
        for metadata in manifest["datasets"]["observed"].values()
    )

    private_transaction = json.loads(
        (output_directory / "private" / "transaction_ground_truth.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    observed_transaction = json.loads(
        (output_directory / "observed" / "transactions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert {"event_id", "economic_type", "metadata"} <= set(private_transaction)
    _assert_no_truth_fields(set(observed_transaction))


def test_output_is_byte_for_byte_reproducible_in_different_directories(
    tmp_path: Path,
    scenario_config: ScenarioConfig,
) -> None:
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    write_run(generate_scenario(scenario_config, seed=42, months=24), first_output)
    write_run(generate_scenario(scenario_config, seed=42, months=24), second_output)

    first_tree = _output_tree(first_output)
    second_tree = _output_tree(second_output)
    assert "run_manifest.json" in first_tree
    assert first_tree == second_tree


def test_seed_42_output_matches_versioned_golden_fingerprint(
    tmp_path: Path,
    generated_seed_42: GeneratedScenario,
) -> None:
    output_directory = tmp_path / "golden"
    write_run(generated_seed_42, output_directory)

    assert generated_seed_42.simulation.run_id == "run_9e93a533dbe45c3eb8475801a1ad7783"
    assert len(generated_seed_42.simulation.events) == 554
    assert generated_seed_42.ground_truth.customer_months[-1].closing_balance_minor == 4_139_148
    assert _output_digest(output_directory) == (
        "2fe5c4815c6eab6287d550558dad1bf6016a10daa8ca5edc9674fb3d3d469d37"
    )


def test_different_seed_changes_variable_expense_output(
    scenario_config: ScenarioConfig,
    generated_seed_42: GeneratedScenario,
) -> None:
    generated_seed_43 = generate_scenario(scenario_config, seed=43, months=24)

    def variable_signature(generated: GeneratedScenario) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                event.occurred_at,
                event.amount_minor,
                event.destination_entity,
                event.description,
            )
            for event in _events_with_expense_kind(generated, "VARIABLE")
        )

    assert variable_signature(generated_seed_42) != variable_signature(generated_seed_43)


def test_writer_refuses_to_overwrite_nonempty_directory(
    tmp_path: Path,
    generated_seed_42: GeneratedScenario,
) -> None:
    output_directory = tmp_path / "existing"
    output_directory.mkdir()
    sentinel = output_directory / "keep.txt"
    sentinel.write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(OutputDirectoryNotEmptyError, match="not empty"):
        write_run(generated_seed_42, output_directory)

    assert sentinel.read_text(encoding="utf-8") == "do not overwrite"
    assert {path.name for path in output_directory.iterdir()} == {"keep.txt"}


def test_cli_generates_run_successfully(
    tmp_path: Path,
    example_config_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_directory = tmp_path / "cli-output"
    exit_code = main(
        [
            "generate",
            "--config",
            str(example_config_path),
            "--seed",
            "42",
            "--months",
            "24",
            "--output",
            str(output_directory),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Generated run run_" in captured.out
    assert "Manifest:" in captured.out
    assert captured.err == ""
    assert (output_directory / "run_manifest.json").is_file()
    assert (output_directory / "private").is_dir()
    assert (output_directory / "observed").is_dir()


def test_cli_reports_actionable_invalid_configuration(
    tmp_path: Path,
    example_config_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    invalid_config_path = tmp_path / "invalid.yaml"
    config_text = example_config_path.read_text(encoding="utf-8")
    invalid_config_path.write_text(
        config_text.replace("amount_minor: 650000", "amount_minor: 0", 1),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "generate",
            "--config",
            str(invalid_config_path),
            "--seed",
            "42",
            "--output",
            str(tmp_path / "invalid-output"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert "error: Invalid scenario configuration" in captured.err
    assert "salary.amount_minor" in captured.err
    assert "greater than 0" in captured.err
    assert "Traceback" not in captured.err
