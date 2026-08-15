"""Phase-2 acceptance tests for multiple accounts, transfers, and credit cards."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from finances_simulator.config import (
    ConfigurationError,
    ScenarioConfig,
    ScenarioConfigV0,
    load_scenario_config,
)
from finances_simulator.config_v1 import ScenarioConfigV1
from finances_simulator.domain.accounts import Direction
from finances_simulator.domain.cards import CardInstallment, CardInvoice, InvoiceStatus
from finances_simulator.domain.events import EconomicType
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.observations.contracts_v1 import (
    AccountV1,
    BalanceV1,
    CardInvoiceItemV1,
    CardInvoiceV1,
    CardTransactionV1,
    CreditCardV1,
    CreditLimitV1,
    TransactionV1,
)
from finances_simulator.outputs import write_run
from finances_simulator.simulation.cards import (
    due_date_after_close,
    split_installments,
    statement_close_for_purchase,
)
from finances_simulator.simulation.primitives import V0_PROFILE, V1_PROFILE, month_end, month_start

FORBIDDEN_OBSERVED_FIELDS = {
    "caused_by_event_id",
    "economic_type",
    "event_id",
    "expense_kind",
    "income_source_id",
    "installment_ids",
    "is_income",
    "is_own_transfer",
    "metadata",
    "occurrence_index",
    "payment_event_id",
    "purchase_id",
    "rule_id",
    "source_entity",
    "destination_entity",
    "transfer_group_id",
    "used_limit_after_purchase_minor",
}

OBSERVED_MODELS: tuple[type[BaseModel], ...] = (
    AccountV1,
    BalanceV1,
    TransactionV1,
    CreditCardV1,
    CreditLimitV1,
    CardTransactionV1,
    CardInvoiceV1,
    CardInvoiceItemV1,
)


@pytest.fixture(scope="session")
def v1_config_path(project_root: Path) -> Path:
    return project_root / "configs" / "scenarios" / "salaried_multi_account_card.yaml"


@pytest.fixture(scope="session")
def v1_config(v1_config_path: Path) -> ScenarioConfigV1:
    config = load_scenario_config(v1_config_path)
    assert isinstance(config, ScenarioConfigV1)
    return config


@pytest.fixture(scope="session")
def generated_v1_seed_42(v1_config: ScenarioConfigV1) -> GeneratedScenario:
    return generate_scenario(v1_config, seed=42, months=24)


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
    assert not any(field_name.startswith("true_") for field_name in field_names)


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


def _signed_amount(direction: Direction, amount_minor: int) -> int:
    return amount_minor if direction is Direction.CREDIT else -amount_minor


def _repeated_card_purchase_rules(
    v1_config: ScenarioConfigV1,
    *,
    rule_count: int,
    occurrences: int,
    installment_count: int,
) -> list[dict[str, Any]]:
    template = v1_config.card_purchase_rules[0].model_dump(mode="json")
    return [
        {
            **template,
            "rule_id": f"aggregate_rule_{rule_index:03d}",
            "amount_minor": max(template["amount_minor"], installment_count),
            "occurrences": occurrences,
            "installment_count": installment_count,
        }
        for rule_index in range(rule_count)
    ]


def test_configuration_loader_dispatches_frozen_v0_and_v1_schemas(
    example_config_path: Path,
    v1_config_path: Path,
) -> None:
    v0_config = load_scenario_config(example_config_path)
    v1_config = load_scenario_config(v1_config_path)

    assert isinstance(v0_config, ScenarioConfigV0)
    assert v0_config.schema_version == "1.0"
    assert isinstance(v1_config, ScenarioConfigV1)
    assert v1_config.schema_version == "1.1"

    v0_run = generate_scenario(v0_config, seed=42, months=24).simulation
    v1_run = generate_scenario(v1_config, seed=42, months=24).simulation
    assert v0_run.profile == V0_PROFILE
    assert v1_run.profile == V1_PROFILE
    assert v0_run.run_id == "run_9e93a533dbe45c3eb8475801a1ad7783"


def test_scenario_config_remains_runtime_v0_model_class_with_model_validate(
    example_config_path: Path,
) -> None:
    payload = yaml.safe_load(example_config_path.read_text(encoding="utf-8"))

    assert ScenarioConfig is ScenarioConfigV0
    assert isinstance(ScenarioConfig, type)
    validated = ScenarioConfig.model_validate(payload)
    assert isinstance(validated, ScenarioConfigV0)
    assert validated.schema_version == "1.0"


def test_configuration_loader_rejects_unsupported_schema(
    tmp_path: Path,
    v1_config_path: Path,
) -> None:
    payload = yaml.safe_load(v1_config_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "9.9"
    config_path = tmp_path / "unsupported.yaml"
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="unsupported schema_version"):
        load_scenario_config(config_path)


@pytest.mark.parametrize(
    ("case", "error_match"),
    (
        ("duplicate_account_ref", "accounts.account_ref values must be unique"),
        ("primary_is_savings", "primary_account_ref must reference a CHECKING account"),
        ("unknown_salary_account", "salary.destination_account_ref must reference an account"),
        ("same_transfer_endpoints", "source_account_ref and destination_account_ref must differ"),
        ("installments_exceed_amount", "amount_minor must be greater than or equal"),
        ("utilization_above_full_limit", "less than or equal to 10000"),
        ("composite_key_delimiter", "String should match pattern"),
    ),
)
def test_v1_configuration_rejects_invalid_relationships_and_ranges(
    case: str,
    error_match: str,
    v1_config_path: Path,
) -> None:
    payload = yaml.safe_load(v1_config_path.read_text(encoding="utf-8"))
    if case == "duplicate_account_ref":
        payload["accounts"][1]["account_ref"] = payload["accounts"][0]["account_ref"]
    elif case == "primary_is_savings":
        payload["customer"]["primary_account_ref"] = "reserve_savings"
    elif case == "unknown_salary_account":
        payload["salary"]["destination_account_ref"] = "missing_account"
    elif case == "same_transfer_endpoints":
        payload["own_transfers"][0]["destination_account_ref"] = "primary_checking"
    elif case == "installments_exceed_amount":
        payload["card_purchase_rules"][0]["amount_minor"] = 2
        payload["card_purchase_rules"][0]["installment_count"] = 3
    elif case == "utilization_above_full_limit":
        payload["credit_cards"][0]["utilization_policy"]["maximum_basis_points"] = 10_001
    elif case == "composite_key_delimiter":
        payload["card_purchase_rules"][0]["rule_id"] = "ambiguous:rule"
    else:  # pragma: no cover - guards parametrization maintenance
        raise AssertionError(f"Unknown invalid-config case: {case}")

    with pytest.raises(ValidationError, match=error_match):
        ScenarioConfigV1.model_validate(payload)


@pytest.mark.parametrize(
    ("case", "error_match"),
    (
        ("variable_count_min", "less than or equal to 500"),
        ("variable_count_max", "less than or equal to 500"),
        ("purchase_occurrences", "less than or equal to 1200"),
        ("purchase_installments", "less than or equal to 120"),
    ),
)
def test_v1_configuration_rejects_generation_counts_above_caps(
    case: str,
    error_match: str,
    v1_config: ScenarioConfigV1,
) -> None:
    payload = v1_config.model_dump(mode="json")
    if case == "variable_count_min":
        payload["variable_expenses"]["count_min"] = 501
        payload["variable_expenses"]["count_max"] = 501
    elif case == "variable_count_max":
        payload["variable_expenses"]["count_max"] = 501
    elif case == "purchase_occurrences":
        payload["card_purchase_rules"][0]["occurrences"] = 1_201
    elif case == "purchase_installments":
        payload["card_purchase_rules"][0]["installment_count"] = 121
    else:  # pragma: no cover - guards parametrization maintenance
        raise AssertionError(f"Unknown cap case: {case}")

    with pytest.raises(ValidationError, match=error_match):
        ScenarioConfigV1.model_validate(payload)


def test_v1_configuration_accepts_generation_count_cap_boundaries(
    v1_config: ScenarioConfigV1,
) -> None:
    payload = v1_config.model_dump(mode="json")
    payload["variable_expenses"]["count_min"] = 500
    payload["variable_expenses"]["count_max"] = 500
    payload["card_purchase_rules"][0]["occurrences"] = 1_200
    payload["card_purchase_rules"][0]["installment_count"] = 120

    validated = ScenarioConfigV1.model_validate(payload)
    assert validated.variable_expenses.count_min == 500
    assert validated.variable_expenses.count_max == 500
    assert validated.card_purchase_rules[0].occurrences == 1_200
    assert validated.card_purchase_rules[0].installment_count == 120


@pytest.mark.parametrize(
    ("rule_count", "occurrences", "installment_count", "error_match"),
    (
        (257, 1, 1, "at most 256"),
        (9, 1_200, 1, "at most 10000 purchase attempts"),
        (25, 100, 101, "at most 250000 installment items"),
    ),
)
def test_v1_configuration_rejects_aggregate_card_work_above_caps(
    rule_count: int,
    occurrences: int,
    installment_count: int,
    error_match: str,
    v1_config: ScenarioConfigV1,
) -> None:
    payload = v1_config.model_dump(mode="json")
    payload["card_purchase_rules"] = _repeated_card_purchase_rules(
        v1_config,
        rule_count=rule_count,
        occurrences=occurrences,
        installment_count=installment_count,
    )

    with pytest.raises(ValidationError, match=error_match):
        ScenarioConfigV1.model_validate(payload)


@pytest.mark.parametrize(
    ("rule_count", "occurrences", "installment_count"),
    (
        (256, 1, 1),
        (10, 1_000, 1),
        (25, 100, 100),
    ),
)
def test_v1_configuration_accepts_exact_aggregate_card_cap_boundaries(
    rule_count: int,
    occurrences: int,
    installment_count: int,
    v1_config: ScenarioConfigV1,
) -> None:
    payload = v1_config.model_dump(mode="json")
    payload["card_purchase_rules"] = _repeated_card_purchase_rules(
        v1_config,
        rule_count=rule_count,
        occurrences=occurrences,
        installment_count=installment_count,
    )

    validated = ScenarioConfigV1.model_validate(payload)
    assert len(validated.card_purchase_rules) == rule_count
    assert sum(rule.occurrences for rule in validated.card_purchase_rules) <= 10_000
    assert (
        sum(rule.occurrences * rule.installment_count for rule in validated.card_purchase_rules)
        <= 250_000
    )


def test_large_occurrence_schedule_materializes_only_attempts_inside_window(
    v1_config: ScenarioConfigV1,
) -> None:
    payload = v1_config.model_dump(mode="json")
    rule = payload["card_purchase_rules"][0]
    rule["occurrences"] = 1_200
    rule["start_month_index"] = 0
    rule["interval_months"] = 1
    payload["card_purchase_rules"] = [rule]
    config = ScenarioConfigV1.model_validate(payload)

    generated = generate_scenario(config, seed=42, months=1)
    assert len(generated.simulation.card_purchases) == 1
    assert len(generated.simulation.card_installments) == rule["installment_count"]

    rule["start_month_index"] = 10_000
    out_of_window_config = ScenarioConfigV1.model_validate(payload)
    out_of_window = generate_scenario(out_of_window_config, seed=42, months=1)
    assert out_of_window.simulation.card_purchases == ()
    assert out_of_window.simulation.card_installments == ()


@pytest.mark.parametrize("seed", (0, 1, 2, 7, 42, 99))
def test_every_account_and_month_end_balance_reconciles_for_many_seeds(
    seed: int,
    v1_config: ScenarioConfigV1,
) -> None:
    generated = generate_scenario(v1_config, seed=seed, months=12)
    run = generated.simulation
    accounts = run.customer_twin.accounts

    assert len(accounts) == len(v1_config.accounts) == 2
    assert len({account.account_id for account in accounts}) == len(accounts)
    assert len({account.institution_id for account in accounts}) == 2
    assert len(generated.observations.accounts) == len(accounts)
    assert len(generated.observations.balances) == 12 * len(accounts)

    observed_balance_by_key = {
        (record.account_id, record.reference_date): record.balance_minor
        for record in generated.observations.balances
    }
    final_balances: dict[str, int] = {}
    for account in accounts:
        running_balance = account.opening_balance_minor
        account_entries = [
            entry for entry in run.ledger_entries if entry.account_id == account.account_id
        ]
        entry_index = 0
        for entry in account_entries:
            running_balance += _signed_amount(entry.direction, entry.amount_minor)
            assert entry.balance_after_minor == running_balance
        final_balances[account.account_id] = running_balance

        running_balance = account.opening_balance_minor
        for month_index in range(run.months):
            reference_date = month_end(month_start(run.start_date, month_index))
            while (
                entry_index < len(account_entries)
                and account_entries[entry_index].posted_at <= reference_date
            ):
                running_balance = account_entries[entry_index].balance_after_minor
                entry_index += 1
            assert (
                observed_balance_by_key[(account.account_id, reference_date.isoformat())]
                == running_balance
            )

    assert generated.ground_truth.customer_months[-1].total_deposit_closing_balance_minor == sum(
        final_balances.values()
    )


def test_salary_routes_only_to_primary_account(
    v1_config: ScenarioConfigV1,
    generated_v1_seed_42: GeneratedScenario,
) -> None:
    run = generated_v1_seed_42.simulation
    primary_account = run.customer_twin.primary_account
    salary_events = [event for event in run.events if event.economic_type is EconomicType.INCOME]
    entries_by_event: dict[str, list[Any]] = defaultdict(list)
    for entry in run.ledger_entries:
        entries_by_event[entry.event_id].append(entry)

    assert len(salary_events) == run.months
    for event in salary_events:
        assert event.destination_entity == primary_account.account_id
        assert event.amount_minor == v1_config.salary.amount_minor
        assert event.income_source_id == run.customer_twin.income_source_id
        assert len(entries_by_event[event.event_id]) == 1
        entry = entries_by_event[event.event_id][0]
        assert entry.account_id == primary_account.account_id
        assert entry.direction is Direction.CREDIT
        assert entry.amount_minor == event.amount_minor


def test_own_transfers_have_exact_cash_conserving_ledger_pairs(
    v1_config: ScenarioConfigV1,
    generated_v1_seed_42: GeneratedScenario,
) -> None:
    run = generated_v1_seed_42.simulation
    transfer_events = [
        event for event in run.events if event.economic_type is EconomicType.OWN_TRANSFER
    ]
    entries_by_event: dict[str, list[Any]] = defaultdict(list)
    for entry in run.ledger_entries:
        entries_by_event[entry.event_id].append(entry)

    assert len(transfer_events) == run.months * len(v1_config.own_transfers)
    for event in transfer_events:
        pair = entries_by_event[event.event_id]
        assert len(pair) == 2
        debit = next(entry for entry in pair if entry.direction is Direction.DEBIT)
        credit = next(entry for entry in pair if entry.direction is Direction.CREDIT)
        assert debit.account_id == event.source_entity
        assert credit.account_id == event.destination_entity
        assert debit.account_id != credit.account_id
        assert debit.posted_at == credit.posted_at == event.occurred_at
        assert debit.amount_minor == credit.amount_minor == event.amount_minor
        assert debit.transfer_group_id == credit.transfer_group_id
        assert debit.transfer_group_id is not None
        assert sum(_signed_amount(entry.direction, entry.amount_minor) for entry in pair) == 0

        truth_rows = [
            record
            for record in generated_v1_seed_42.ground_truth.transactions
            if record.event_id == event.event_id
        ]
        assert len(truth_rows) == 2
        assert {record.transfer_group_id for record in truth_rows} == {debit.transfer_group_id}


def test_transfers_conserve_aggregate_deposit_cash_and_never_become_income(
    generated_v1_seed_42: GeneratedScenario,
) -> None:
    run = generated_v1_seed_42.simulation
    opening_total = sum(account.opening_balance_minor for account in run.customer_twin.accounts)
    signed_all_entries = sum(
        _signed_amount(entry.direction, entry.amount_minor) for entry in run.ledger_entries
    )
    transfer_event_ids = {
        event.event_id for event in run.events if event.economic_type is EconomicType.OWN_TRANSFER
    }
    signed_transfers = sum(
        _signed_amount(entry.direction, entry.amount_minor)
        for entry in run.ledger_entries
        if entry.event_id in transfer_event_ids
    )
    final_observed_total = sum(
        record.balance_minor
        for record in generated_v1_seed_42.observations.balances
        if record.reference_date == run.end_date.isoformat()
    )

    assert signed_transfers == 0
    assert final_observed_total == opening_total + signed_all_entries
    assert all(
        month.true_income_minor
        == sum(
            event.amount_minor
            for event in run.events
            if event.occurred_at.strftime("%Y-%m") == month.month
            and event.economic_type is EconomicType.INCOME
        )
        for month in generated_v1_seed_42.ground_truth.customer_months
    )


def test_card_purchase_is_expense_once_and_payment_is_only_cash_settlement(
    generated_v1_seed_42: GeneratedScenario,
) -> None:
    run = generated_v1_seed_42.simulation
    event_by_id = {event.event_id: event for event in run.events}
    ledger_event_ids = {entry.event_id for entry in run.ledger_entries}
    purchase_event_ids = {purchase.event_id for purchase in run.card_purchases}
    payment_events = [
        event for event in run.events if event.economic_type is EconomicType.CARD_PAYMENT
    ]

    assert purchase_event_ids.isdisjoint(ledger_event_ids)
    assert payment_events
    assert all(event.event_id in ledger_event_ids for event in payment_events)
    assert all(
        event_by_id[purchase.event_id].economic_type is EconomicType.EXPENSE
        for purchase in run.card_purchases
    )
    assert all(
        record.economic_type is EconomicType.EXPENSE
        for record in generated_v1_seed_42.ground_truth.credit_card_transactions
    )

    for month_truth in generated_v1_seed_42.ground_truth.customer_months:
        month_events = [
            event
            for event in run.events
            if event.occurred_at.strftime("%Y-%m") == month_truth.month
        ]
        economic_expenses = [
            event for event in month_events if event.economic_type is EconomicType.EXPENSE
        ]
        assert month_truth.true_expenses_minor == sum(
            event.amount_minor for event in economic_expenses
        )
        assert month_truth.expense_event_count == len(economic_expenses)
        assert all(
            event.economic_type is not EconomicType.CARD_PAYMENT for event in economic_expenses
        )


@pytest.mark.parametrize(
    ("amount_minor", "installment_count", "expected"),
    (
        (10_001, 3, (3_334, 3_334, 3_333)),
        (3, 3, (1, 1, 1)),
        (10, 1, (10,)),
        (10, 4, (3, 3, 2, 2)),
    ),
)
def test_installment_split_is_exact_and_remainder_goes_first(
    amount_minor: int,
    installment_count: int,
    expected: tuple[int, ...],
) -> None:
    installments = split_installments(amount_minor, installment_count)

    assert installments == expected
    assert len(installments) == installment_count
    assert sum(installments) == amount_minor
    assert all(amount > 0 for amount in installments)


@pytest.mark.parametrize(
    ("amount_minor", "installment_count", "error_match"),
    ((0, 1, "positive"), (1, 0, "positive"), (2, 3, "at least")),
)
def test_installment_split_rejects_invalid_inputs(
    amount_minor: int,
    installment_count: int,
    error_match: str,
) -> None:
    with pytest.raises(ValueError, match=error_match):
        split_installments(amount_minor, installment_count)


@pytest.mark.parametrize(
    ("case", "error_match"),
    (
        ("number_exceeds_count", "installment_number must not exceed"),
        ("due_on_close", "due_date must be after"),
        ("due_before_close", "due_date must be after"),
    ),
)
def test_card_installment_rejects_impossible_schedule(
    case: str,
    error_match: str,
) -> None:
    payload: dict[str, Any] = {
        "invoice_item_id": "item-1",
        "purchase_id": "purchase-1",
        "card_id": "card-1",
        "invoice_id": "invoice-1",
        "statement_close_date": date(2024, 1, 20),
        "due_date": date(2024, 2, 5),
        "installment_number": 1,
        "installment_count": 3,
        "amount_minor": 3_334,
        "description": "INSTALLMENT PURCHASE",
    }
    if case == "number_exceeds_count":
        payload["installment_number"] = 4
    elif case == "due_on_close":
        payload["due_date"] = date(2024, 1, 20)
    elif case == "due_before_close":
        payload["due_date"] = date(2024, 1, 19)
    else:  # pragma: no cover - guards parametrization maintenance
        raise AssertionError(f"Unknown installment case: {case}")

    with pytest.raises(ValidationError, match=error_match):
        CardInstallment.model_validate(payload)


@pytest.mark.parametrize(
    ("case", "error_match"),
    (
        ("zero_amount_due", "greater than 0"),
        ("empty_items", "at least 1"),
        ("due_on_close", "due_date must be after"),
        ("duplicate_items", "installment_ids must be unique"),
        ("paid_amount_mismatch", "PAID invoice amount must reconcile"),
        ("paid_wrong_date", "PAID invoice requires due-date payment"),
        ("paid_missing_event", "PAID invoice requires due-date payment"),
        ("closed_paid_amount", "CLOSED invoice cannot contain payment details"),
        ("closed_payment_reference", "CLOSED invoice cannot contain payment details"),
    ),
)
def test_card_invoice_rejects_impossible_payment_state(
    case: str,
    error_match: str,
) -> None:
    payload: dict[str, Any] = {
        "invoice_id": "invoice-1",
        "customer_id": "customer-1",
        "card_id": "card-1",
        "statement_close_date": date(2024, 1, 20),
        "due_date": date(2024, 2, 5),
        "amount_due_minor": 10_000,
        "paid_amount_minor": 10_000,
        "status": "PAID",
        "paid_at": date(2024, 2, 5),
        "payment_event_id": "payment-1",
        "installment_ids": ("item-1",),
    }
    if case == "zero_amount_due":
        payload["amount_due_minor"] = 0
        payload["paid_amount_minor"] = 0
    elif case == "empty_items":
        payload["installment_ids"] = ()
    elif case == "due_on_close":
        payload["due_date"] = date(2024, 1, 20)
        payload["paid_at"] = date(2024, 1, 20)
    elif case == "duplicate_items":
        payload["installment_ids"] = ("item-1", "item-1")
    elif case == "paid_amount_mismatch":
        payload["paid_amount_minor"] = 9_999
    elif case == "paid_wrong_date":
        payload["paid_at"] = date(2024, 2, 4)
    elif case == "paid_missing_event":
        payload["payment_event_id"] = None
    elif case == "closed_paid_amount":
        payload["status"] = "CLOSED"
        payload["paid_at"] = None
        payload["payment_event_id"] = None
    elif case == "closed_payment_reference":
        payload["status"] = "CLOSED"
        payload["paid_amount_minor"] = 0
    else:  # pragma: no cover - guards parametrization maintenance
        raise AssertionError(f"Unknown invoice case: {case}")

    with pytest.raises(ValidationError, match=error_match):
        CardInvoice.model_validate(payload)


def test_generated_remainder_purchase_has_expected_installments_and_cycles(
    generated_v1_seed_42: GeneratedScenario,
) -> None:
    run = generated_v1_seed_42.simulation
    purchase = next(
        item for item in run.card_purchases if item.rule_id == "close_day_remainder_purchase"
    )
    installments = sorted(
        (item for item in run.card_installments if item.purchase_id == purchase.purchase_id),
        key=lambda item: item.installment_number,
    )

    assert purchase.purchased_at == date(2024, 3, 20)
    assert [item.installment_number for item in installments] == [1, 2, 3]
    assert [item.amount_minor for item in installments] == [3_334, 3_334, 3_333]
    assert [item.statement_close_date for item in installments] == [
        date(2024, 3, 20),
        date(2024, 4, 20),
        date(2024, 5, 20),
    ]
    assert [item.due_date for item in installments] == [
        date(2024, 4, 5),
        date(2024, 5, 5),
        date(2024, 6, 5),
    ]


@pytest.mark.parametrize(
    ("purchased_at", "close_day", "expected"),
    (
        (date(2024, 1, 20), 20, date(2024, 1, 20)),
        (date(2024, 1, 21), 20, date(2024, 2, 20)),
        (date(2024, 2, 29), 31, date(2024, 2, 29)),
        (date(2024, 2, 29), 28, date(2024, 3, 28)),
        (date(2025, 2, 28), 31, date(2025, 2, 28)),
        (date(2024, 12, 31), 31, date(2024, 12, 31)),
    ),
)
def test_statement_cycle_boundary_and_month_end_clamping(
    purchased_at: date,
    close_day: int,
    expected: date,
) -> None:
    assert statement_close_for_purchase(purchased_at, close_day) == expected


@pytest.mark.parametrize(
    ("statement_close", "due_day", "expected"),
    (
        (date(2024, 1, 20), 25, date(2024, 1, 25)),
        (date(2024, 1, 20), 5, date(2024, 2, 5)),
        (date(2024, 2, 29), 31, date(2024, 3, 31)),
        (date(2024, 12, 31), 5, date(2025, 1, 5)),
    ),
)
def test_due_date_is_first_configured_date_strictly_after_close(
    statement_close: date,
    due_day: int,
    expected: date,
) -> None:
    due_date = due_date_after_close(statement_close, due_day)

    assert due_date == expected
    assert due_date > statement_close


def test_invoices_items_and_full_autopay_reconcile(
    generated_v1_seed_42: GeneratedScenario,
) -> None:
    run = generated_v1_seed_42.simulation
    event_by_id = {event.event_id: event for event in run.events}
    entries_by_event: dict[str, list[Any]] = defaultdict(list)
    for entry in run.ledger_entries:
        entries_by_event[entry.event_id].append(entry)
    hidden_items_by_invoice: dict[str, list[Any]] = defaultdict(list)
    for item in run.card_installments:
        if item.statement_close_date <= run.end_date:
            hidden_items_by_invoice[item.invoice_id].append(item)
    observed_invoice_by_id = {
        invoice.invoice_id: invoice
        for invoice in generated_v1_seed_42.observations.credit_card_invoices
    }
    observed_items_by_invoice: dict[str, list[Any]] = defaultdict(list)
    for item in generated_v1_seed_42.observations.credit_card_invoice_items:
        observed_items_by_invoice[item.invoice_id].append(item)

    assert set(observed_invoice_by_id) == {invoice.invoice_id for invoice in run.card_invoices}
    assert len(run.card_invoices) == 24
    for invoice in run.card_invoices:
        hidden_items = hidden_items_by_invoice[invoice.invoice_id]
        observed_items = observed_items_by_invoice[invoice.invoice_id]
        observed_invoice = observed_invoice_by_id[invoice.invoice_id]
        assert invoice.due_date > invoice.statement_close_date
        assert invoice.amount_due_minor == sum(item.amount_minor for item in hidden_items)
        assert set(invoice.installment_ids) == {item.invoice_item_id for item in hidden_items}
        assert {item.invoice_item_id for item in observed_items} == set(invoice.installment_ids)
        assert observed_invoice.amount_due_minor == invoice.amount_due_minor
        assert all(item.installment_number <= item.installment_count for item in hidden_items)

        if invoice.due_date <= run.end_date:
            assert invoice.status is InvoiceStatus.PAID
            assert invoice.paid_amount_minor == invoice.amount_due_minor
            assert invoice.paid_at == invoice.due_date
            assert invoice.payment_event_id is not None
            payment_event = event_by_id[invoice.payment_event_id]
            payment_entries = entries_by_event[invoice.payment_event_id]
            assert payment_event.economic_type is EconomicType.CARD_PAYMENT
            assert payment_event.amount_minor == invoice.amount_due_minor
            assert len(payment_entries) == 1
            assert payment_entries[0].direction is Direction.DEBIT
            assert payment_entries[0].amount_minor == invoice.amount_due_minor
            assert observed_invoice.status == "PAID"
            assert observed_invoice.paid_amount_minor == invoice.amount_due_minor
            assert observed_invoice.payment_transaction_id == payment_entries[0].entry_id
        else:
            assert invoice.status is InvoiceStatus.CLOSED
            assert invoice.paid_amount_minor == 0
            assert invoice.paid_at is None
            assert invoice.payment_event_id is None
            assert observed_invoice.status == "CLOSED"
            assert observed_invoice.paid_at is None
            assert observed_invoice.payment_transaction_id is None

    installments_by_purchase: dict[str, list[Any]] = defaultdict(list)
    for item in run.card_installments:
        installments_by_purchase[item.purchase_id].append(item)
    for purchase in run.card_purchases:
        purchase_installments = installments_by_purchase[purchase.purchase_id]
        assert len(purchase_installments) == purchase.installment_count
        assert sum(item.amount_minor for item in purchase_installments) == purchase.amount_minor


def test_credit_limit_snapshots_follow_full_commitment_and_payment_release(
    v1_config: ScenarioConfigV1,
    generated_v1_seed_42: GeneratedScenario,
) -> None:
    run = generated_v1_seed_42.simulation
    card_config_by_id = {
        card.card_id: next(
            config for config in v1_config.credit_cards if config.card_label == card.card_label
        )
        for card in run.cards
    }
    purchases_by_card: dict[str, list[Any]] = defaultdict(list)
    installments_by_card: dict[str, list[Any]] = defaultdict(list)
    for purchase in run.card_purchases:
        purchases_by_card[purchase.card_id].append(purchase)
    for installment in run.card_installments:
        installments_by_card[installment.card_id].append(installment)

    assert len(run.credit_limit_snapshots) == run.months * len(run.cards)
    for snapshot in run.credit_limit_snapshots:
        purchased = sum(
            purchase.amount_minor
            for purchase in purchases_by_card[snapshot.card_id]
            if purchase.purchased_at <= snapshot.reference_date
        )
        paid_principal = sum(
            installment.amount_minor
            for installment in installments_by_card[snapshot.card_id]
            if installment.due_date <= snapshot.reference_date
        )
        expected_used = purchased - paid_principal
        card_config = card_config_by_id[snapshot.card_id]
        maximum_used = (
            snapshot.total_limit_minor
            * card_config.utilization_policy.maximum_basis_points
            // 10_000
        )

        assert snapshot.used_limit_minor == expected_used
        assert snapshot.available_limit_minor == (
            snapshot.total_limit_minor - snapshot.used_limit_minor
        )
        assert snapshot.used_limit_minor <= maximum_used
        assert snapshot.total_limit_minor == (
            snapshot.used_limit_minor + snapshot.available_limit_minor
        )

    maximum_by_card = {
        card.card_id: (card.credit_limit_minor * card.maximum_utilization_basis_points // 10_000)
        for card in run.cards
    }
    assert all(
        purchase.used_limit_after_purchase_minor <= maximum_by_card[purchase.card_id]
        for purchase in run.card_purchases
    )


@pytest.mark.parametrize(
    ("purchase_amount", "is_accepted"),
    ((400_000, True), (400_001, False)),
)
def test_utilization_policy_accepts_exact_boundary_and_declines_one_over(
    purchase_amount: int,
    is_accepted: bool,
    v1_config: ScenarioConfigV1,
) -> None:
    payload = v1_config.model_dump(mode="json")
    payload["card_purchase_rules"] = [
        {
            "rule_id": "utilization_boundary",
            "card_ref": "main_card",
            "merchant": "Boundary Merchant",
            "description": "LIMIT BOUNDARY PURCHASE",
            "amount_minor": purchase_amount,
            "day_of_month": 1,
            "start_month_index": 0,
            "interval_months": 1,
            "occurrences": 1,
            "installment_count": 1,
        }
    ]
    config = ScenarioConfigV1.model_validate(payload)
    generated = generate_scenario(config, seed=42, months=1)

    assert bool(generated.simulation.card_purchases) is is_accepted
    snapshot = generated.simulation.credit_limit_snapshots[0]
    assert snapshot.used_limit_minor == (purchase_amount if is_accepted else 0)
    assert snapshot.available_limit_minor == 500_000 - snapshot.used_limit_minor


def test_same_day_authorization_truth_is_causal_but_observation_order_is_stable(
    v1_config: ScenarioConfigV1,
) -> None:
    payload = v1_config.model_dump(mode="json")
    payload["card_purchase_rules"] = [
        {
            "rule_id": "a_first",
            "card_ref": "main_card",
            "merchant": "A",
            "description": "FIRST",
            "amount_minor": 100_000,
            "day_of_month": 10,
            "start_month_index": 0,
            "interval_months": 1,
            "occurrences": 1,
            "installment_count": 1,
        },
        {
            "rule_id": "z_second",
            "card_ref": "main_card",
            "merchant": "Z",
            "description": "SECOND",
            "amount_minor": 200_000,
            "day_of_month": 10,
            "start_month_index": 0,
            "interval_months": 1,
            "occurrences": 1,
            "installment_count": 1,
        },
    ]
    config = ScenarioConfigV1.model_validate(payload)
    generated = generate_scenario(config, seed=42, months=1)
    repeated = generate_scenario(config, seed=42, months=1)
    purchases = list(generated.simulation.card_purchases)
    truth = list(generated.ground_truth.credit_card_transactions)
    observed = list(generated.observations.credit_card_transactions)

    assert [purchase.rule_id for purchase in purchases] == ["a_first", "z_second"]
    assert [purchase.used_limit_after_purchase_minor for purchase in purchases] == [
        100_000,
        300_000,
    ]
    assert [record.card_transaction_id for record in truth] == [
        purchase.purchase_id for purchase in purchases
    ]
    assert [record.outstanding_after_minor for record in truth] == [100_000, 300_000]
    assert all(
        earlier.outstanding_after_minor <= later.outstanding_after_minor
        for earlier, later in zip(truth, truth[1:], strict=False)
    )

    expected_observed = sorted(
        purchases,
        key=lambda purchase: (purchase.purchased_at, purchase.event_id),
    )
    assert [record.card_transaction_id for record in observed] == [
        purchase.purchase_id for purchase in expected_observed
    ]
    assert [record.card_transaction_id for record in observed] != [
        record.card_transaction_id for record in truth
    ]
    assert generated.observations.credit_card_transactions == (
        repeated.observations.credit_card_transactions
    )


@pytest.mark.parametrize("model_type", OBSERVED_MODELS)
def test_v1_observation_models_exclude_ground_truth_fields(
    model_type: type[BaseModel],
) -> None:
    _assert_no_truth_fields(set(model_type.model_fields))


def test_serialized_v1_observations_do_not_leak_private_truth(
    tmp_path: Path,
    generated_v1_seed_42: GeneratedScenario,
) -> None:
    output_directory = tmp_path / "v1-leakage"
    write_run(generated_v1_seed_42, output_directory)

    for path in (output_directory / "observed").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            _assert_no_truth_fields(_all_mapping_keys(json.loads(line)))

    private_card_truth = json.loads(
        (output_directory / "private" / "credit_card_transaction_ground_truth.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    observed_card_transaction = json.loads(
        (output_directory / "observed" / "credit_card_transactions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert {"event_id", "economic_type", "metadata", "source_entity"} <= set(private_card_truth)
    _assert_no_truth_fields(set(observed_card_transaction))


def test_v1_output_is_deterministic_versioned_and_manifest_reconciled(
    tmp_path: Path,
    project_root: Path,
    v1_config: ScenarioConfigV1,
) -> None:
    first_generated = generate_scenario(v1_config, seed=42, months=24)
    second_generated = generate_scenario(v1_config, seed=42, months=24)
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    write_run(first_generated, first_output)
    write_run(second_generated, second_output)

    assert first_generated == second_generated
    assert _output_tree(first_output) == _output_tree(second_output)
    committed_output = (
        project_root / "examples" / "generated" / "salaried_multi_account_card_seed_42"
    )
    assert _output_tree(first_output) == _output_tree(committed_output)
    manifest = json.loads((first_output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run_ebaac2f476ea54a1b7cb260739bc49f9"
    assert manifest["simulator_version"] == "0.2.0"
    assert manifest["contract_schema_version"] == "1.1"
    assert manifest["rng_algorithm"] == "sha256-counter-v1"
    assert set(manifest["datasets"]["observed"]) == {
        "accounts",
        "balances",
        "credit_card_invoice_items",
        "credit_card_invoices",
        "credit_card_transactions",
        "credit_cards",
        "credit_limits",
        "transactions",
    }
    assert set(manifest["datasets"]["private"]) == {
        "credit_card_transaction_ground_truth",
        "customer_ground_truth",
        "customer_month_ground_truth",
        "transaction_ground_truth",
    }

    for visibility, datasets in manifest["datasets"].items():
        for metadata in datasets.values():
            dataset_path = first_output / metadata["path"]
            payload = dataset_path.read_bytes()
            assert metadata["path"].startswith(f"{visibility}/")
            assert metadata["schema_version"] == "1.1"
            assert metadata["sha256"] == hashlib.sha256(payload).hexdigest()
            assert metadata["record_count"] == len(payload.splitlines())
            for line in payload.splitlines():
                assert json.loads(line)["schema_version"] == "1.1"

    assert {
        key: value["record_count"] for key, value in manifest["datasets"]["observed"].items()
    } == {
        "accounts": 2,
        "balances": 48,
        "credit_card_invoice_items": 33,
        "credit_card_invoices": 24,
        "credit_card_transactions": 26,
        "credit_cards": 1,
        "credit_limits": 24,
        "transactions": 619,
    }
    assert _output_digest(first_output) == (
        "8f4f93f5638c435732fb2767706ab80d14a0456a11cda875e582be64f09e6020"
    )
