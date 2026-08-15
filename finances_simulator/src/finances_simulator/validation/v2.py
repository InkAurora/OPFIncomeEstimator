"""Cross-record invariants for schema-1.2 loans, investments, and net worth."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import TYPE_CHECKING

from finances_simulator.domain.accounts import Direction, LedgerEntry
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.domain.investments import (
    Investment,
    InvestmentBalanceSnapshot,
    InvestmentTransaction,
    InvestmentTransactionType,
)
from finances_simulator.domain.loans import (
    Loan,
    LoanBalanceSnapshot,
    LoanPayment,
    LoanPaymentStatus,
)
from finances_simulator.simulation.primitives import month_end, month_start
from finances_simulator.validation.invariants import InvariantViolation

if TYPE_CHECKING:
    from finances_simulator.contracts.ground_truth_v2 import (
        BalanceSheetGroundTruthV2,
        CustomerMonthGroundTruthV2,
    )


def _unique(items: tuple[object, ...], attribute: str, label: str) -> dict[str, object]:
    indexed = {getattr(item, attribute): item for item in items}
    if len(indexed) != len(items):
        raise InvariantViolation(f"{label} IDs must be unique.")
    return indexed


def validate_loan_simulation(
    *,
    loans: Iterable[Loan],
    payments: Iterable[LoanPayment],
    snapshots: Iterable[LoanBalanceSnapshot],
    events: Iterable[FinancialEvent],
    entries: Iterable[LedgerEntry],
    start_date: date,
    end_date: date,
    months: int,
) -> None:
    """Validate origination, SAC schedules, settlements, and remaining principal."""

    loan_items = tuple(loans)
    payment_items = tuple(payments)
    snapshot_items = tuple(snapshots)
    event_items = tuple(events)
    entry_items = tuple(entries)
    loan_by_id = _unique(loan_items, "loan_id", "Loan")
    _unique(payment_items, "payment_id", "Loan payment")
    _unique(snapshot_items, "snapshot_id", "Loan-balance snapshot")
    event_by_id = _unique(event_items, "event_id", "Financial event")
    entries_by_event: dict[str, list[LedgerEntry]] = {}
    for entry in entry_items:
        entries_by_event.setdefault(entry.event_id, []).append(entry)

    expected_loan_event_ids: set[str] = set()
    payments_by_loan: dict[str, list[LoanPayment]] = {loan_id: [] for loan_id in loan_by_id}
    for payment in payment_items:
        if payment.loan_id not in loan_by_id:
            raise InvariantViolation(
                f"Loan payment {payment.payment_id} references an unknown loan."
            )
        payments_by_loan[payment.loan_id].append(payment)

    for loan in loan_items:
        disbursement_event = event_by_id.get(loan.disbursement_event_id)
        disbursement_entries = entries_by_event.get(loan.disbursement_event_id, [])
        if not isinstance(disbursement_event, FinancialEvent) or (
            disbursement_event.economic_type is not EconomicType.LOAN_DISBURSEMENT
            or disbursement_event.occurred_at != loan.originated_at
            or disbursement_event.amount_minor != loan.principal_minor
            or disbursement_event.currency != loan.currency
            or disbursement_event.source_entity != loan.loan_id
            or disbursement_event.destination_entity != loan.disbursement_account_id
            or disbursement_event.income_source_id is not None
            or len(disbursement_entries) != 1
            or disbursement_entries[0].account_id != loan.disbursement_account_id
            or disbursement_entries[0].direction is not Direction.CREDIT
            or disbursement_entries[0].amount_minor != loan.principal_minor
            or disbursement_entries[0].posted_at != loan.originated_at
        ):
            raise InvariantViolation(f"Loan {loan.loan_id} has an invalid disbursement.")
        expected_loan_event_ids.add(loan.disbursement_event_id)

        scheduled = sorted(
            payments_by_loan[loan.loan_id],
            key=lambda item: item.installment_number,
        )
        if len(scheduled) != loan.term_months or {
            item.installment_number for item in scheduled
        } != set(range(1, loan.term_months + 1)):
            raise InvariantViolation(f"Loan {loan.loan_id} has an incomplete schedule.")
        base_principal, remainder = divmod(loan.principal_minor, loan.term_months)
        remaining = loan.principal_minor
        for payment in scheduled:
            expected_principal = base_principal + (
                1 if payment.installment_number <= remainder else 0
            )
            expected_interest = (remaining * loan.annual_interest_basis_points + 60_000) // 120_000
            expected_due_month = month_start(
                loan.originated_at.replace(day=1),
                payment.installment_number,
            )
            remaining -= expected_principal
            should_be_paid = payment.due_date <= end_date
            if (
                payment.installment_count != loan.term_months
                or payment.due_date.year != expected_due_month.year
                or payment.due_date.month != expected_due_month.month
                or payment.opening_principal_minor != remaining + expected_principal
                or payment.principal_minor != expected_principal
                or payment.interest_minor != expected_interest
                or payment.payment_minor != expected_principal + expected_interest
                or payment.remaining_principal_minor != remaining
                or (payment.status is LoanPaymentStatus.PAID) != should_be_paid
            ):
                raise InvariantViolation(
                    f"Loan payment {payment.payment_id} does not follow its SAC schedule."
                )
            if not should_be_paid:
                if payment.payment_event_id is not None or payment.paid_at is not None:
                    raise InvariantViolation(
                        f"Future loan payment {payment.payment_id} contains settlement data."
                    )
                continue
            if payment.payment_event_id is None:
                raise InvariantViolation(f"Paid loan payment {payment.payment_id} lacks an event.")
            event = event_by_id.get(payment.payment_event_id)
            payment_entries = entries_by_event.get(payment.payment_event_id, [])
            if not isinstance(event, FinancialEvent) or (
                payment.paid_at != payment.due_date
                or event.economic_type is not EconomicType.LOAN_PAYMENT
                or event.occurred_at != payment.due_date
                or event.amount_minor != payment.payment_minor
                or event.currency != loan.currency
                or event.source_entity != loan.payment_account_id
                or event.destination_entity != loan.loan_id
                or event.income_source_id is not None
                or len(payment_entries) != 1
                or payment_entries[0].account_id != loan.payment_account_id
                or payment_entries[0].direction is not Direction.DEBIT
                or payment_entries[0].amount_minor != payment.payment_minor
                or payment_entries[0].posted_at != payment.due_date
            ):
                raise InvariantViolation(
                    f"Loan payment {payment.payment_id} has an invalid settlement."
                )
            expected_loan_event_ids.add(payment.payment_event_id)
        if remaining != 0:
            raise InvariantViolation(f"Loan {loan.loan_id} does not amortize to zero.")

    actual_loan_event_ids = {
        event.event_id
        for event in event_items
        if event.economic_type in {EconomicType.LOAN_DISBURSEMENT, EconomicType.LOAN_PAYMENT}
    }
    if actual_loan_event_ids != expected_loan_event_ids:
        raise InvariantViolation("Loan events do not match originated loans and paid installments.")

    expected_snapshot_keys: set[tuple[str, date]] = set()
    expected_remaining: dict[tuple[str, date], int] = {}
    for month_index in range(months):
        reference_date = month_end(month_start(start_date, month_index))
        for loan in loan_items:
            if reference_date < loan.originated_at:
                continue
            key = (loan.loan_id, reference_date)
            expected_snapshot_keys.add(key)
            remaining = loan.principal_minor
            for payment in payments_by_loan[loan.loan_id]:
                if payment.due_date <= reference_date:
                    remaining = payment.remaining_principal_minor
            expected_remaining[key] = remaining
    actual_snapshot_keys = {
        (snapshot.loan_id, snapshot.reference_date) for snapshot in snapshot_items
    }
    if len(actual_snapshot_keys) != len(snapshot_items) or (
        actual_snapshot_keys != expected_snapshot_keys
    ):
        raise InvariantViolation("Loan-balance snapshots do not cover every active loan month.")
    for snapshot in snapshot_items:
        loan = loan_by_id.get(snapshot.loan_id)
        if not isinstance(loan, Loan) or (
            snapshot.customer_id != loan.customer_id
            or snapshot.currency != loan.currency
            or snapshot.remaining_principal_minor
            != expected_remaining[(snapshot.loan_id, snapshot.reference_date)]
        ):
            raise InvariantViolation(
                f"Loan-balance snapshot {snapshot.snapshot_id} does not reconcile."
            )


def validate_investment_simulation(
    *,
    investments: Iterable[Investment],
    transactions: Iterable[InvestmentTransaction],
    snapshots: Iterable[InvestmentBalanceSnapshot],
    events: Iterable[FinancialEvent],
    entries: Iterable[LedgerEntry],
    start_date: date,
    months: int,
) -> None:
    """Validate external flows, returns, and every month-end valuation."""

    investment_items = tuple(investments)
    transaction_items = tuple(transactions)
    snapshot_items = tuple(snapshots)
    event_items = tuple(events)
    entry_items = tuple(entries)
    investment_by_id = _unique(investment_items, "investment_id", "Investment")
    _unique(transaction_items, "transaction_id", "Investment transaction")
    _unique(snapshot_items, "snapshot_id", "Investment-balance snapshot")
    event_by_id = _unique(event_items, "event_id", "Financial event")
    entries_by_event: dict[str, list[LedgerEntry]] = {}
    for entry in entry_items:
        entries_by_event.setdefault(entry.event_id, []).append(entry)

    expected_event_ids: set[str] = set()
    transactions_by_product_month: dict[tuple[str, str], list[InvestmentTransaction]] = {}
    for transaction in transaction_items:
        if transaction.investment_id not in investment_by_id:
            raise InvariantViolation(
                f"Investment transaction {transaction.transaction_id} references an unknown "
                "investment."
            )
        transactions_by_product_month.setdefault(
            (transaction.investment_id, transaction.occurred_at.strftime("%Y-%m")),
            [],
        ).append(transaction)

    expected_snapshot_keys = {
        (investment.investment_id, month_end(month_start(start_date, month_index)))
        for month_index in range(months)
        for investment in investment_items
    }
    snapshot_by_key = {
        (snapshot.investment_id, snapshot.reference_date): snapshot for snapshot in snapshot_items
    }
    if len(snapshot_by_key) != len(snapshot_items) or (
        set(snapshot_by_key) != expected_snapshot_keys
    ):
        raise InvariantViolation("Investment-balance snapshots do not cover every product month.")

    balances = {
        investment.investment_id: investment.opening_balance_minor
        for investment in investment_items
    }
    for month_index in range(months):
        current_month = month_start(start_date, month_index)
        reference_date = month_end(current_month)
        month_key = current_month.strftime("%Y-%m")
        for investment in investment_items:
            balance = balances[investment.investment_id]
            movements = transactions_by_product_month.get((investment.investment_id, month_key), [])
            external = [
                item
                for item in movements
                if item.transaction_type is not InvestmentTransactionType.RETURN
            ]
            returns = [
                item
                for item in movements
                if item.transaction_type is InvestmentTransactionType.RETURN
            ]
            rank = {
                InvestmentTransactionType.CONTRIBUTION: 0,
                InvestmentTransactionType.REDEMPTION: 1,
            }
            if external != sorted(
                external,
                key=lambda item: (
                    item.occurred_at,
                    rank[item.transaction_type],
                    item.rule_id or "",
                    item.occurrence_index or 0,
                ),
            ):
                raise InvariantViolation("Investment flows are not in causal order.")
            for transaction in external:
                event = event_by_id.get(transaction.event_id)
                flow_entries = entries_by_event.get(transaction.event_id, [])
                is_contribution = (
                    transaction.transaction_type is InvestmentTransactionType.CONTRIBUTION
                )
                expected_type = (
                    EconomicType.INVESTMENT_CONTRIBUTION
                    if is_contribution
                    else EconomicType.INVESTMENT_REDEMPTION
                )
                expected_direction = Direction.DEBIT if is_contribution else Direction.CREDIT
                expected_source = (
                    transaction.account_id if is_contribution else transaction.investment_id
                )
                expected_destination = (
                    transaction.investment_id if is_contribution else transaction.account_id
                )
                balance = (
                    balance + transaction.amount_minor
                    if is_contribution
                    else balance - transaction.amount_minor
                )
                if not isinstance(event, FinancialEvent) or (
                    balance < 0
                    or transaction.balance_after_minor != balance
                    or event.economic_type is not expected_type
                    or event.occurred_at != transaction.occurred_at
                    or event.amount_minor != transaction.amount_minor
                    or event.currency != investment.currency
                    or event.source_entity != expected_source
                    or event.destination_entity != expected_destination
                    or event.income_source_id is not None
                    or len(flow_entries) != 1
                    or flow_entries[0].account_id != transaction.account_id
                    or flow_entries[0].direction is not expected_direction
                    or flow_entries[0].amount_minor != transaction.amount_minor
                    or flow_entries[0].posted_at != transaction.occurred_at
                ):
                    raise InvariantViolation(
                        f"Investment transaction {transaction.transaction_id} does not reconcile."
                    )
                expected_event_ids.add(transaction.event_id)

            expected_return = (balance * investment.monthly_return_basis_points + 5_000) // 10_000
            if len(returns) != (1 if expected_return > 0 else 0):
                raise InvariantViolation(
                    f"Investment {investment.investment_id} has an invalid monthly return."
                )
            if returns:
                transaction = returns[0]
                event = event_by_id.get(transaction.event_id)
                balance += expected_return
                if not isinstance(event, FinancialEvent) or (
                    transaction.occurred_at != reference_date
                    or transaction.amount_minor != expected_return
                    or transaction.balance_after_minor != balance
                    or transaction.account_id is not None
                    or event.economic_type is not EconomicType.INVESTMENT_RETURN
                    or event.amount_minor != expected_return
                    or event.source_entity != investment.institution_id
                    or event.destination_entity != investment.investment_id
                    or event.income_source_id is not None
                    or entries_by_event.get(transaction.event_id)
                ):
                    raise InvariantViolation(
                        f"Investment return {transaction.transaction_id} does not reconcile."
                    )
                expected_event_ids.add(transaction.event_id)
            snapshot = snapshot_by_key[(investment.investment_id, reference_date)]
            if (
                snapshot.customer_id != investment.customer_id
                or snapshot.currency != investment.currency
                or snapshot.balance_minor != balance
            ):
                raise InvariantViolation(
                    f"Investment-balance snapshot {snapshot.snapshot_id} does not reconcile."
                )
            balances[investment.investment_id] = balance

    actual_event_ids = {
        event.event_id
        for event in event_items
        if event.economic_type
        in {
            EconomicType.INVESTMENT_CONTRIBUTION,
            EconomicType.INVESTMENT_REDEMPTION,
            EconomicType.INVESTMENT_RETURN,
        }
    }
    if actual_event_ids != expected_event_ids:
        raise InvariantViolation("Investment events do not match accepted product movements.")


def validate_balance_sheet_truth(
    records: Iterable[BalanceSheetGroundTruthV2],
    customer_months: Iterable[CustomerMonthGroundTruthV2],
) -> None:
    """Validate monthly continuity and the supported net-worth bridge."""

    record_items = tuple(records)
    month_items = tuple(customer_months)
    if len(record_items) != len(month_items):
        raise InvariantViolation("Balance-sheet and customer-month coverage must match.")
    month_truth = {item.month: item for item in month_items}
    if len(month_truth) != len(month_items):
        raise InvariantViolation("Customer-month keys must be unique.")
    previous = None
    for record in record_items:
        month = month_truth.get(record.month)
        if month is None:
            raise InvariantViolation(
                f"Balance sheet {record.balance_sheet_id} lacks monthly truth."
            )
        if previous is not None and (
            record.opening_total_deposit_balance_minor != previous.total_deposit_balance_minor
            or record.opening_total_investment_balance_minor
            != previous.total_investment_balance_minor
            or record.opening_total_card_outstanding_minor != previous.total_card_outstanding_minor
            or record.opening_total_loan_principal_minor != previous.total_loan_principal_minor
            or record.opening_net_worth_minor != previous.net_worth_minor
        ):
            raise InvariantViolation("Balance-sheet openings do not continue prior closings.")
        expected_change = (
            month.true_income_minor
            - month.true_expenses_minor
            - month.loan_interest_paid_minor
            + month.investment_return_minor
        )
        if record.net_worth_minor - record.opening_net_worth_minor != expected_change:
            raise InvariantViolation(
                f"Balance sheet {record.balance_sheet_id} violates the net-worth bridge."
            )
        previous = record


__all__ = [
    "validate_balance_sheet_truth",
    "validate_investment_simulation",
    "validate_loan_simulation",
]
