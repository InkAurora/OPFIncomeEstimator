"""Project schema-1.1 private evaluation truth."""

from dataclasses import dataclass

from finances_simulator.domain.accounts import LedgerEntry
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.ground_truth.contracts_v1 import (
    CardTransactionGroundTruthV1,
    CustomerGroundTruthV1,
    CustomerMonthGroundTruthV1,
    TransactionGroundTruthV1,
)
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import month_end, month_start


@dataclass(frozen=True, slots=True)
class GroundTruthBundleV1:
    customers: tuple[CustomerGroundTruthV1, ...]
    customer_months: tuple[CustomerMonthGroundTruthV1, ...]
    transactions: tuple[TransactionGroundTruthV1, ...]
    credit_card_transactions: tuple[CardTransactionGroundTruthV1, ...]


def project_ground_truth_v1(run: SimulationRun) -> GroundTruthBundleV1:
    """Build multi-account/card truth while counting economic events only once."""

    twin = run.customer_twin
    accounts = twin.accounts
    primary_account = twin.primary_account
    event_by_id = {event.event_id: event for event in run.events}
    customer = CustomerGroundTruthV1(
        customer_id=twin.customer_id,
        scenario_name=twin.scenario_name,
        employment_status=twin.employment_status,
        currency=twin.currency,
        true_monthly_salary_minor=twin.true_monthly_salary_minor,
        income_source_id=twin.income_source_id,
        primary_account_id=primary_account.account_id,
        opening_balance_minor=primary_account.opening_balance_minor,
        account_ids=tuple(account.account_id for account in accounts),
        card_ids=tuple(card.card_id for card in run.cards),
        total_opening_deposit_balance_minor=sum(
            account.opening_balance_minor for account in accounts
        ),
    )

    transactions = tuple(
        TransactionGroundTruthV1(
            event_id=event.event_id,
            entry_id=entry.entry_id,
            customer_id=event.customer_id,
            account_id=entry.account_id,
            occurred_at=entry.posted_at.isoformat(),
            economic_type=event.economic_type,
            direction=entry.direction,
            amount_minor=entry.amount_minor,
            currency=event.currency,
            source_entity=event.source_entity,
            destination_entity=event.destination_entity,
            income_source_id=event.income_source_id,
            caused_by_event_id=event.caused_by_event_id,
            transfer_group_id=entry.transfer_group_id,
            description=entry.description,
            balance_after_minor=entry.balance_after_minor,
            metadata=event.metadata,
        )
        for entry in run.ledger_entries
        for event in (event_by_id[entry.event_id],)
    )
    card_transactions = tuple(
        CardTransactionGroundTruthV1(
            event_id=event.event_id,
            card_transaction_id=purchase.purchase_id,
            customer_id=purchase.customer_id,
            card_id=purchase.card_id,
            occurred_at=purchase.purchased_at.isoformat(),
            economic_type=event.economic_type,
            amount_minor=purchase.amount_minor,
            currency=purchase.currency,
            source_entity=event.source_entity,
            destination_entity=event.destination_entity,
            description=purchase.description,
            installment_count=purchase.installment_count,
            outstanding_after_minor=purchase.used_limit_after_purchase_minor,
            metadata=event.metadata,
        )
        for purchase in run.card_purchases
        for event in (event_by_id[purchase.event_id],)
    )

    events_by_month: dict[str, list[FinancialEvent]] = {}
    entries_by_account_month: dict[tuple[str, str], list[LedgerEntry]] = {}
    for event in run.events:
        events_by_month.setdefault(event.occurred_at.strftime("%Y-%m"), []).append(event)
    for entry in run.ledger_entries:
        key = (entry.account_id, entry.posted_at.strftime("%Y-%m"))
        entries_by_account_month.setdefault(key, []).append(entry)
    balances = {account.account_id: account.opening_balance_minor for account in accounts}
    previous_card_outstanding = 0
    customer_months: list[CustomerMonthGroundTruthV1] = []
    for month_index in range(run.months):
        current_month = month_start(run.start_date, month_index)
        month_key = current_month.strftime("%Y-%m")
        month_events = events_by_month.get(month_key, [])
        opening_balances = balances.copy()
        for account in accounts:
            month_entries = entries_by_account_month.get((account.account_id, month_key), [])
            if month_entries:
                balances[account.account_id] = month_entries[-1].balance_after_minor
        card_outstanding = sum(
            snapshot.used_limit_minor
            for snapshot in run.credit_limit_snapshots
            if snapshot.reference_date == month_end(current_month)
        )
        customer_months.append(
            CustomerMonthGroundTruthV1(
                customer_id=twin.customer_id,
                month=month_key,
                currency=twin.currency,
                true_income_minor=sum(
                    event.amount_minor
                    for event in month_events
                    if event.economic_type is EconomicType.INCOME
                ),
                true_expenses_minor=sum(
                    event.amount_minor
                    for event in month_events
                    if event.economic_type is EconomicType.EXPENSE
                ),
                income_event_count=sum(
                    event.economic_type is EconomicType.INCOME for event in month_events
                ),
                expense_event_count=sum(
                    event.economic_type is EconomicType.EXPENSE for event in month_events
                ),
                opening_balance_minor=opening_balances[primary_account.account_id],
                closing_balance_minor=balances[primary_account.account_id],
                total_deposit_opening_balance_minor=sum(opening_balances.values()),
                total_deposit_closing_balance_minor=sum(balances.values()),
                total_card_outstanding_opening_minor=previous_card_outstanding,
                total_card_outstanding_closing_minor=card_outstanding,
            )
        )
        previous_card_outstanding = card_outstanding

    return GroundTruthBundleV1(
        customers=(customer,),
        customer_months=tuple(customer_months),
        transactions=transactions,
        credit_card_transactions=card_transactions,
    )


__all__ = ["GroundTruthBundleV1", "project_ground_truth_v1"]
