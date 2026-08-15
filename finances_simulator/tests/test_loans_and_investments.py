"""Phase-3 acceptance tests for loans, investments, and net worth."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from finances_simulator.config import ScenarioConfigV0, load_scenario_config
from finances_simulator.config_v1 import ScenarioConfigV1
from finances_simulator.config_v2 import ScenarioConfigV2
from finances_simulator.contracts.observed_v2 import (
    AccountV2,
    BalanceV2,
    CardInvoiceItemV2,
    CardInvoiceV2,
    CardTransactionV2,
    CreditCardV2,
    CreditLimitV2,
    InvestmentBalanceV2,
    InvestmentTransactionV2,
    InvestmentV2,
    LoanBalanceV2,
    LoanPaymentV2,
    LoanV2,
    TransactionV2,
)
from finances_simulator.domain.accounts import Direction
from finances_simulator.domain.events import EconomicType
from finances_simulator.domain.investments import InvestmentTransactionType
from finances_simulator.domain.loans import LoanPaymentStatus
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.outputs import write_run
from finances_simulator.simulation.primitives import V0_PROFILE, V1_PROFILE, V2_PROFILE

FORBIDDEN_OBSERVED_FIELDS = {
    "caused_by_event_id",
    "destination_entity",
    "disbursement_event_id",
    "economic_type",
    "event_id",
    "income_source_id",
    "metadata",
    "occurrence_index",
    "payment_event_id",
    "rule_id",
    "source_entity",
    "transfer_group_id",
}

OBSERVED_MODELS: tuple[type[BaseModel], ...] = (
    AccountV2,
    BalanceV2,
    TransactionV2,
    CreditCardV2,
    CreditLimitV2,
    CardTransactionV2,
    CardInvoiceV2,
    CardInvoiceItemV2,
    LoanV2,
    LoanPaymentV2,
    LoanBalanceV2,
    InvestmentV2,
    InvestmentTransactionV2,
    InvestmentBalanceV2,
)


@pytest.fixture(scope="session")
def v2_config_path(project_root: Path) -> Path:
    return project_root / "configs" / "scenarios" / "salaried_loans_investments.yaml"


@pytest.fixture(scope="session")
def v2_config(v2_config_path: Path) -> ScenarioConfigV2:
    config = load_scenario_config(v2_config_path)
    assert isinstance(config, ScenarioConfigV2)
    return config


@pytest.fixture(scope="session")
def generated_v2_seed_42(v2_config: ScenarioConfigV2) -> GeneratedScenario:
    return generate_scenario(v2_config, seed=42, months=24)


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
    assert not any("net_worth" in field_name for field_name in field_names)


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


def _config_with_changes(
    config: ScenarioConfigV2,
    mutate: Any,
) -> ScenarioConfigV2:
    payload = config.model_dump(mode="json")
    mutate(payload)
    return ScenarioConfigV2.model_validate(payload)


def test_configuration_loader_dispatches_all_frozen_contracts(
    project_root: Path,
    v2_config_path: Path,
) -> None:
    v0 = load_scenario_config(project_root / "configs" / "scenarios" / "salaried_basic.yaml")
    v1 = load_scenario_config(
        project_root / "configs" / "scenarios" / "salaried_multi_account_card.yaml"
    )
    v2 = load_scenario_config(v2_config_path)

    assert isinstance(v0, ScenarioConfigV0)
    assert isinstance(v1, ScenarioConfigV1)
    assert not isinstance(v1, ScenarioConfigV2)
    assert isinstance(v2, ScenarioConfigV2)
    assert generate_scenario(v0, seed=42, months=1).simulation.profile == V0_PROFILE
    assert generate_scenario(v1, seed=42, months=1).simulation.profile == V1_PROFILE
    assert generate_scenario(v2, seed=42, months=1).simulation.profile == V2_PROFILE


@pytest.mark.parametrize(
    ("case", "error_match"),
    (
        ("duplicate_loan_ref", "loans.loan_ref values must be unique"),
        ("unknown_loan_institution", "references unknown institution"),
        ("unknown_disbursement_account", "unknown disbursement account"),
        ("unknown_payment_account", "unknown payment account"),
        ("principal_shorter_than_term", "greater than or equal to term_months"),
        ("duplicate_investment_ref", "investments.investment_ref values must be unique"),
        ("unknown_investment_institution", "references unknown institution"),
        ("unknown_flow_investment", "references unknown investment"),
        ("unknown_flow_account", "references unknown account"),
        ("duplicate_flow_rule", "investment flow rule_id values must be unique"),
        ("loan_schedule_cap", "loans may schedule at most 10000 installments"),
        ("investment_attempt_cap", "may schedule at most 10000 attempts"),
    ),
)
def test_v2_configuration_rejects_invalid_references_and_bounds(
    case: str,
    error_match: str,
    v2_config: ScenarioConfigV2,
) -> None:
    payload = v2_config.model_dump(mode="json")
    if case == "duplicate_loan_ref":
        payload["loans"].append(dict(payload["loans"][0]))
    elif case == "unknown_loan_institution":
        payload["loans"][0]["institution_ref"] = "missing_institution"
    elif case == "unknown_disbursement_account":
        payload["loans"][0]["disbursement_account_ref"] = "missing_account"
    elif case == "unknown_payment_account":
        payload["loans"][0]["payment_account_ref"] = "missing_account"
    elif case == "principal_shorter_than_term":
        payload["loans"][0]["principal_minor"] = 35
    elif case == "duplicate_investment_ref":
        payload["investments"].append(dict(payload["investments"][0]))
    elif case == "unknown_investment_institution":
        payload["investments"][0]["institution_ref"] = "missing_institution"
    elif case == "unknown_flow_investment":
        payload["investment_contribution_rules"][0]["investment_ref"] = "missing_investment"
    elif case == "unknown_flow_account":
        payload["investment_redemption_rules"][0]["account_ref"] = "missing_account"
    elif case == "duplicate_flow_rule":
        payload["investment_redemption_rules"][0]["rule_id"] = payload[
            "investment_contribution_rules"
        ][0]["rule_id"]
    elif case == "loan_schedule_cap":
        template = payload["loans"][0]
        payload["loans"] = [
            {**template, "loan_ref": f"loan_{index}", "term_months": 480} for index in range(21)
        ]
    elif case == "investment_attempt_cap":
        template = payload["investment_contribution_rules"][0]
        payload["investment_contribution_rules"] = [
            {**template, "rule_id": f"contribution_{index}", "occurrences": 1_200}
            for index in range(9)
        ]
    else:  # pragma: no cover - protects parametrization maintenance
        raise AssertionError(f"Unknown invalid configuration case: {case}")

    with pytest.raises(ValidationError, match=error_match):
        ScenarioConfigV2.model_validate(payload)


@pytest.mark.parametrize(
    "field_name",
    ("institutions", "accounts", "own_transfers", "credit_cards"),
)
def test_v2_caps_inherited_recurring_collections(
    field_name: str,
    v2_config: ScenarioConfigV2,
) -> None:
    payload = v2_config.model_dump(mode="json")
    payload[field_name] = [dict(payload[field_name][0]) for _ in range(33)]

    with pytest.raises(ValidationError, match="at most 32 items"):
        ScenarioConfigV2.model_validate(payload)


def test_constant_principal_schedule_uses_integer_half_up_math_and_clamped_dates(
    v2_config: ScenarioConfigV2,
) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["loans"][0].update(
            {
                "principal_minor": 10_001,
                "annual_interest_basis_points": 1_200,
                "term_months": 3,
                "disbursement_month_index": 0,
                "disbursement_day_of_month": 31,
                "payment_day_of_month": 31,
            }
        )

    generated = generate_scenario(_config_with_changes(v2_config, mutate), seed=42, months=4)
    payments = sorted(
        generated.simulation.loan_payments,
        key=lambda payment: payment.installment_number,
    )

    assert [payment.due_date for payment in payments] == [
        date(2024, 2, 29),
        date(2024, 3, 31),
        date(2024, 4, 30),
    ]
    assert [payment.opening_principal_minor for payment in payments] == [10_001, 6_667, 3_333]
    assert [payment.principal_minor for payment in payments] == [3_334, 3_334, 3_333]
    assert [payment.interest_minor for payment in payments] == [100, 67, 33]
    assert [payment.payment_minor for payment in payments] == [3_434, 3_401, 3_366]
    assert [payment.remaining_principal_minor for payment in payments] == [6_667, 3_333, 0]
    assert all(payment.status is LoanPaymentStatus.PAID for payment in payments)
    assert sum(payment.principal_minor for payment in payments) == 10_001


def test_investment_return_uses_post_flow_balance_and_integer_rounding(
    v2_config: ScenarioConfigV2,
) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["loans"][0]["disbursement_month_index"] = 1_199
        payload["investments"][0]["opening_balance_minor"] = 10_001
        payload["investments"][0]["monthly_return_basis_points"] = 100
        payload["investment_contribution_rules"][0].update(
            {"amount_minor": 10_000, "occurrences": 2}
        )
        payload["investment_redemption_rules"][0].update(
            {"amount_minor": 5_000, "start_month_index": 1}
        )

    generated = generate_scenario(_config_with_changes(v2_config, mutate), seed=42, months=2)
    transactions = generated.simulation.investment_transactions
    snapshots = generated.simulation.investment_balance_snapshots

    assert [transaction.transaction_type for transaction in transactions] == [
        InvestmentTransactionType.CONTRIBUTION,
        InvestmentTransactionType.RETURN,
        InvestmentTransactionType.CONTRIBUTION,
        InvestmentTransactionType.REDEMPTION,
        InvestmentTransactionType.RETURN,
    ]
    assert [transaction.amount_minor for transaction in transactions] == [
        10_000,
        200,
        10_000,
        5_000,
        252,
    ]
    assert [transaction.balance_after_minor for transaction in transactions] == [
        20_001,
        20_201,
        30_201,
        25_201,
        25_453,
    ]
    assert [snapshot.balance_minor for snapshot in snapshots] == [20_201, 25_453]


def test_half_minor_investment_return_rounds_up(v2_config: ScenarioConfigV2) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["loans"][0]["disbursement_month_index"] = 1_199
        payload["investments"][0]["opening_balance_minor"] = 0
        payload["investments"][0]["monthly_return_basis_points"] = 100
        payload["investment_contribution_rules"][0].update({"amount_minor": 50, "occurrences": 1})
        payload["investment_redemption_rules"][0]["start_month_index"] = 1

    generated = generate_scenario(_config_with_changes(v2_config, mutate), seed=42, months=1)
    returns = [
        transaction
        for transaction in generated.simulation.investment_transactions
        if transaction.transaction_type is InvestmentTransactionType.RETURN
    ]

    assert len(returns) == 1
    assert returns[0].amount_minor == 1
    assert generated.simulation.investment_balance_snapshots[0].balance_minor == 51


@pytest.mark.parametrize(("redemption_minor", "accepted"), ((51, True), (52, False)))
def test_same_day_contribution_funds_redemption_and_one_over_is_declined(
    redemption_minor: int,
    accepted: bool,
    v2_config: ScenarioConfigV2,
) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["loans"][0]["disbursement_month_index"] = 1_199
        payload["investments"][0]["opening_balance_minor"] = 50
        payload["investments"][0]["monthly_return_basis_points"] = 0
        payload["investment_contribution_rules"][0].update(
            {"amount_minor": 1, "day_of_month": 7, "occurrences": 1}
        )
        payload["investment_redemption_rules"][0].update(
            {
                "amount_minor": redemption_minor,
                "day_of_month": 7,
                "start_month_index": 0,
            }
        )

    generated = generate_scenario(_config_with_changes(v2_config, mutate), seed=42, months=1)
    redemptions = [
        transaction
        for transaction in generated.simulation.investment_transactions
        if transaction.transaction_type is InvestmentTransactionType.REDEMPTION
    ]

    assert bool(redemptions) is accepted
    assert generated.simulation.investment_balance_snapshots[0].balance_minor == (
        0 if accepted else 51
    )
    redemption_events = [
        event
        for event in generated.simulation.events
        if event.economic_type is EconomicType.INVESTMENT_REDEMPTION
    ]
    assert bool(redemption_events) is accepted


def test_loan_disbursement_and_paid_installments_have_exact_cash_effects(
    generated_v2_seed_42: GeneratedScenario,
) -> None:
    run = generated_v2_seed_42.simulation
    assert len(run.loans) == 1
    loan = run.loans[0]
    event_by_id = {event.event_id: event for event in run.events}
    entries_by_event: dict[str, list[Any]] = defaultdict(list)
    for entry in run.ledger_entries:
        entries_by_event[entry.event_id].append(entry)

    disbursement = event_by_id[loan.disbursement_event_id]
    disbursement_entries = entries_by_event[loan.disbursement_event_id]
    assert disbursement.economic_type is EconomicType.LOAN_DISBURSEMENT
    assert disbursement.amount_minor == loan.principal_minor == 360_001
    assert disbursement.income_source_id is None
    assert len(disbursement_entries) == 1
    assert disbursement_entries[0].direction is Direction.CREDIT
    assert disbursement_entries[0].account_id == loan.disbursement_account_id
    assert disbursement_entries[0].amount_minor == loan.principal_minor

    paid = [payment for payment in run.loan_payments if payment.status is LoanPaymentStatus.PAID]
    scheduled = [
        payment for payment in run.loan_payments if payment.status is LoanPaymentStatus.SCHEDULED
    ]
    assert len(run.loan_payments) == 36
    assert len(paid) == 22
    assert len(scheduled) == 14
    assert paid[0].due_date == date(2024, 3, 12)
    assert paid[-1].due_date == date(2025, 12, 12)
    assert sum(payment.principal_minor for payment in run.loan_payments) == loan.principal_minor
    assert sum(payment.interest_minor for payment in run.loan_payments) == 66_600
    assert sum(payment.principal_minor for payment in paid) == 220_001
    assert sum(payment.interest_minor for payment in paid) == 56_100

    for payment in paid:
        assert payment.payment_event_id is not None
        event = event_by_id[payment.payment_event_id]
        entries = entries_by_event[payment.payment_event_id]
        assert event.economic_type is EconomicType.LOAN_PAYMENT
        assert event.amount_minor == payment.payment_minor
        assert event.income_source_id is None
        assert len(entries) == 1
        assert entries[0].direction is Direction.DEBIT
        assert entries[0].account_id == loan.payment_account_id
        assert entries[0].posted_at == payment.due_date
        assert entries[0].amount_minor == payment.payment_minor

    assert all(payment.payment_event_id is None for payment in scheduled)
    assert all(payment.paid_at is None for payment in scheduled)


def test_loan_balances_follow_schedule_and_preserve_future_liability(
    generated_v2_seed_42: GeneratedScenario,
) -> None:
    run = generated_v2_seed_42.simulation
    loan = run.loans[0]
    snapshots = sorted(
        run.loan_balance_snapshots,
        key=lambda snapshot: snapshot.reference_date,
    )

    assert len(snapshots) == 23
    assert snapshots[0].reference_date == date(2024, 2, 29)
    assert snapshots[0].remaining_principal_minor == 360_001
    assert snapshots[-1].reference_date == date(2025, 12, 31)
    assert snapshots[-1].remaining_principal_minor == 140_000
    for snapshot in snapshots:
        principal_paid = sum(
            payment.principal_minor
            for payment in run.loan_payments
            if payment.due_date <= snapshot.reference_date
        )
        assert snapshot.remaining_principal_minor == loan.principal_minor - principal_paid

    assert len(generated_v2_seed_42.observations.loan_balances) == 23
    assert len(generated_v2_seed_42.observations.loan_payments) == 22
    assert len(generated_v2_seed_42.ground_truth.loan_payments) == 22


def test_investment_flows_returns_and_deposit_effects_reconcile(
    generated_v2_seed_42: GeneratedScenario,
) -> None:
    run = generated_v2_seed_42.simulation
    investment = run.investments[0]
    transactions = sorted(
        run.investment_transactions,
        key=lambda transaction: (transaction.occurred_at, transaction.event_id),
    )
    entries_by_event: dict[str, list[Any]] = defaultdict(list)
    event_by_id = {event.event_id: event for event in run.events}
    for entry in run.ledger_entries:
        entries_by_event[entry.event_id].append(entry)

    by_type: dict[InvestmentTransactionType, list[Any]] = defaultdict(list)
    balance = investment.opening_balance_minor
    for transaction in transactions:
        by_type[transaction.transaction_type].append(transaction)
        event = event_by_id[transaction.event_id]
        if transaction.transaction_type is InvestmentTransactionType.CONTRIBUTION:
            balance += transaction.amount_minor
            assert event.economic_type is EconomicType.INVESTMENT_CONTRIBUTION
            assert len(entries_by_event[transaction.event_id]) == 1
            assert entries_by_event[transaction.event_id][0].direction is Direction.DEBIT
        elif transaction.transaction_type is InvestmentTransactionType.REDEMPTION:
            balance -= transaction.amount_minor
            assert event.economic_type is EconomicType.INVESTMENT_REDEMPTION
            assert len(entries_by_event[transaction.event_id]) == 1
            assert entries_by_event[transaction.event_id][0].direction is Direction.CREDIT
        else:
            balance += transaction.amount_minor
            assert event.economic_type is EconomicType.INVESTMENT_RETURN
            assert not entries_by_event[transaction.event_id]
        assert event.income_source_id is None
        assert transaction.balance_after_minor == balance

    assert len(by_type[InvestmentTransactionType.CONTRIBUTION]) == 24
    assert len(by_type[InvestmentTransactionType.REDEMPTION]) == 1
    assert len(by_type[InvestmentTransactionType.RETURN]) == 24
    assert sum(item.amount_minor for item in by_type[InvestmentTransactionType.RETURN]) == 70_111
    assert len(run.investment_balance_snapshots) == 24
    assert run.investment_balance_snapshots[-1].balance_minor == balance == 720_111
    assert len(generated_v2_seed_42.observations.investment_transactions) == 49
    assert len(generated_v2_seed_42.ground_truth.investment_transactions) == 49


def test_monthly_balance_sheets_and_economic_change_reconcile(
    generated_v2_seed_42: GeneratedScenario,
) -> None:
    truth = generated_v2_seed_42.ground_truth
    sheets = truth.balance_sheets

    assert len(sheets) == len(truth.customer_months) == 24
    assert sheets[0].opening_total_investment_balance_minor == 50_000
    assert sheets[0].opening_total_loan_principal_minor == 0
    assert sheets[0].total_loan_principal_minor == 0
    assert sheets[1].total_loan_principal_minor == 360_001
    assert sheets[-1].total_investment_balance_minor == 720_111
    assert sheets[-1].total_loan_principal_minor == 140_000
    assert any(sheet.total_card_outstanding_minor > 0 for sheet in sheets)

    for index, (sheet, month) in enumerate(zip(sheets, truth.customer_months, strict=True)):
        assert sheet.opening_total_assets_minor == (
            sheet.opening_total_deposit_balance_minor + sheet.opening_total_investment_balance_minor
        )
        assert sheet.opening_total_liabilities_minor == (
            sheet.opening_total_card_outstanding_minor + sheet.opening_total_loan_principal_minor
        )
        assert sheet.opening_net_worth_minor == (
            sheet.opening_total_assets_minor - sheet.opening_total_liabilities_minor
        )
        assert sheet.total_assets_minor == (
            sheet.total_deposit_balance_minor + sheet.total_investment_balance_minor
        )
        assert sheet.total_liabilities_minor == (
            sheet.total_card_outstanding_minor + sheet.total_loan_principal_minor
        )
        assert sheet.net_worth_minor == sheet.total_assets_minor - sheet.total_liabilities_minor
        assert sheet.net_worth_minor - sheet.opening_net_worth_minor == (
            month.true_income_minor
            - month.true_expenses_minor
            - month.loan_interest_paid_minor
            + month.investment_return_minor
        )
        if index:
            previous = sheets[index - 1]
            assert sheet.opening_total_deposit_balance_minor == (
                previous.total_deposit_balance_minor
            )
            assert sheet.opening_total_investment_balance_minor == (
                previous.total_investment_balance_minor
            )
            assert sheet.opening_total_card_outstanding_minor == (
                previous.total_card_outstanding_minor
            )
            assert sheet.opening_total_loan_principal_minor == (previous.total_loan_principal_minor)
            assert sheet.opening_net_worth_minor == previous.net_worth_minor


def test_disbursement_redemption_and_returns_never_become_income(
    v2_config: ScenarioConfigV2,
    generated_v2_seed_42: GeneratedScenario,
) -> None:
    run = generated_v2_seed_42.simulation
    truth = generated_v2_seed_42.ground_truth
    non_income_types = {
        EconomicType.LOAN_DISBURSEMENT,
        EconomicType.INVESTMENT_CONTRIBUTION,
        EconomicType.INVESTMENT_REDEMPTION,
        EconomicType.INVESTMENT_RETURN,
        EconomicType.LOAN_PAYMENT,
    }
    relevant_events = [event for event in run.events if event.economic_type in non_income_types]

    assert relevant_events
    assert all(event.income_source_id is None for event in relevant_events)
    assert all(
        month.true_income_minor == v2_config.salary.amount_minor for month in truth.customer_months
    )
    assert all(month.income_event_count == 1 for month in truth.customer_months)
    assert {
        transaction.economic_type
        for transaction in truth.transactions
        if transaction.direction is Direction.CREDIT
    } >= {
        EconomicType.INCOME,
        EconomicType.LOAN_DISBURSEMENT,
        EconomicType.INVESTMENT_REDEMPTION,
        EconomicType.OWN_TRANSFER,
    }


@pytest.mark.parametrize("model_type", OBSERVED_MODELS)
def test_v2_observation_models_exclude_private_truth_fields(
    model_type: type[BaseModel],
) -> None:
    _assert_no_truth_fields(set(model_type.model_fields))


def test_serialized_v2_observations_exclude_private_truth(
    tmp_path: Path,
    generated_v2_seed_42: GeneratedScenario,
) -> None:
    output = tmp_path / "v2-leakage"
    write_run(generated_v2_seed_42, output)

    for path in (output / "observed").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            _assert_no_truth_fields(_all_mapping_keys(json.loads(line)))

    private_loan = json.loads(
        (output / "private" / "loan_payment_ground_truth.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    private_investment = json.loads(
        (output / "private" / "investment_transaction_ground_truth.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert {"event_id", "economic_type", "metadata", "source_entity"} <= set(private_loan)
    assert {"event_id", "economic_type", "metadata", "source_entity"} <= set(private_investment)


def test_phase_three_does_not_perturb_phase_two_random_or_card_semantics(
    project_root: Path,
    generated_v2_seed_42: GeneratedScenario,
) -> None:
    v1_config = load_scenario_config(
        project_root / "configs" / "scenarios" / "salaried_multi_account_card.yaml"
    )
    v1 = generate_scenario(v1_config, seed=42, months=24)

    def variable_signature(generated: GeneratedScenario) -> tuple[tuple[object, ...], ...]:
        return tuple(
            sorted(
                (
                    event.occurred_at,
                    event.amount_minor,
                    event.destination_entity,
                    event.description,
                )
                for event in generated.simulation.events
                if event.metadata.get("expense_kind") == "VARIABLE"
            )
        )

    def card_signature(generated: GeneratedScenario) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                purchase.purchased_at,
                purchase.amount_minor,
                purchase.installment_count,
                purchase.description,
            )
            for purchase in generated.simulation.card_purchases
        )

    assert variable_signature(generated_v2_seed_42) == variable_signature(v1)
    assert card_signature(generated_v2_seed_42) == card_signature(v1)


@pytest.mark.parametrize(
    ("scenario", "run_id", "digest"),
    (
        (
            "salaried_basic",
            "run_9e93a533dbe45c3eb8475801a1ad7783",
            "2fe5c4815c6eab6287d550558dad1bf6016a10daa8ca5edc9674fb3d3d469d37",
        ),
        (
            "salaried_multi_account_card",
            "run_ebaac2f476ea54a1b7cb260739bc49f9",
            "8f4f93f5638c435732fb2767706ab80d14a0456a11cda875e582be64f09e6020",
        ),
    ),
)
def test_frozen_v0_and_v1_outputs_remain_byte_identical(
    scenario: str,
    run_id: str,
    digest: str,
    project_root: Path,
    tmp_path: Path,
) -> None:
    config = load_scenario_config(project_root / "configs" / "scenarios" / f"{scenario}.yaml")
    generated = generate_scenario(config, seed=42, months=24)
    output = tmp_path / scenario
    write_run(generated, output)

    assert generated.simulation.run_id == run_id
    assert _output_digest(output) == digest
    committed = project_root / "examples" / "generated" / f"{scenario}_seed_42"
    assert _output_tree(output) == _output_tree(committed)


def test_v2_output_is_deterministic_versioned_and_matches_committed_golden(
    tmp_path: Path,
    project_root: Path,
    v2_config: ScenarioConfigV2,
) -> None:
    first = generate_scenario(v2_config, seed=42, months=24)
    second = generate_scenario(v2_config, seed=42, months=24)
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    write_run(first, first_output)
    write_run(second, second_output)

    assert first == second
    assert _output_tree(first_output) == _output_tree(second_output)
    committed = project_root / "examples" / "generated" / "salaried_loans_investments_seed_42"
    assert _output_tree(first_output) == _output_tree(committed)

    manifest = json.loads((first_output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["config_sha256"] == (
        "0e4dd4083936fe1b21ac03525440bd325d3ea963c16f2f1e7144000c4d5a3936"
    )
    assert manifest["run_id"] == "run_a3f411ac77a159ffb0fa9246113c3686"
    assert manifest["simulator_version"] == "0.3.0"
    assert manifest["contract_schema_version"] == "1.2"
    assert set(manifest["datasets"]["observed"]) == {
        "accounts",
        "balances",
        "credit_card_invoice_items",
        "credit_card_invoices",
        "credit_card_transactions",
        "credit_cards",
        "credit_limits",
        "investment_balances",
        "investment_transactions",
        "investments",
        "loan_balances",
        "loan_payments",
        "loans",
        "transactions",
    }
    assert set(manifest["datasets"]["private"]) == {
        "balance_sheet_ground_truth",
        "credit_card_transaction_ground_truth",
        "customer_ground_truth",
        "customer_month_ground_truth",
        "investment_transaction_ground_truth",
        "loan_payment_ground_truth",
        "transaction_ground_truth",
    }
    assert {
        key: metadata["record_count"] for key, metadata in manifest["datasets"]["observed"].items()
    } == {
        "accounts": 2,
        "balances": 48,
        "credit_card_invoice_items": 33,
        "credit_card_invoices": 24,
        "credit_card_transactions": 26,
        "credit_cards": 1,
        "credit_limits": 24,
        "investment_balances": 24,
        "investment_transactions": 49,
        "investments": 1,
        "loan_balances": 23,
        "loan_payments": 22,
        "loans": 1,
        "transactions": 667,
    }
    assert {
        key: metadata["record_count"] for key, metadata in manifest["datasets"]["private"].items()
    } == {
        "balance_sheet_ground_truth": 24,
        "credit_card_transaction_ground_truth": 26,
        "customer_ground_truth": 1,
        "customer_month_ground_truth": 24,
        "investment_transaction_ground_truth": 49,
        "loan_payment_ground_truth": 22,
        "transaction_ground_truth": 667,
    }
    for visibility, datasets in manifest["datasets"].items():
        for metadata in datasets.values():
            payload = (first_output / metadata["path"]).read_bytes()
            assert metadata["path"].startswith(f"{visibility}/")
            assert metadata["schema_version"] == "1.2"
            assert metadata["sha256"] == hashlib.sha256(payload).hexdigest()
            assert metadata["record_count"] == len(payload.splitlines())
            assert all(json.loads(line)["schema_version"] == "1.2" for line in payload.splitlines())

    assert len(first.simulation.events) == 693
    assert len(first.simulation.ledger_entries) == 667
    assert first.ground_truth.balance_sheets[-1].total_deposit_balance_minor == 2_419_530
    assert first.ground_truth.balance_sheets[-1].net_worth_minor == 2_949_641
    assert _output_digest(first_output) == (
        "e508c4a6b1de93f93734df394469478cc8ab4b8d92692f4d5c4af8e5ea47fccd"
    )
