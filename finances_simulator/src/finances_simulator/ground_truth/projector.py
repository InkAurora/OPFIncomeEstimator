"""Project hidden simulation records into private evaluation datasets."""

from dataclasses import dataclass

from finances_simulator.domain.accounts import LedgerEntry
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.ground_truth.contracts import (
    CustomerGroundTruth,
    CustomerMonthGroundTruth,
    TransactionGroundTruth,
)
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import month_start


@dataclass(frozen=True, slots=True)
class GroundTruthBundle:
    customers: tuple[CustomerGroundTruth, ...]
    customer_months: tuple[CustomerMonthGroundTruth, ...]
    transactions: tuple[TransactionGroundTruth, ...]


def project_ground_truth(run: SimulationRun) -> GroundTruthBundle:
    """Build private truth without changing hidden simulation state."""

    twin = run.customer_twin
    account = twin.primary_account
    event_by_id = {event.event_id: event for event in run.events}

    customer = CustomerGroundTruth(
        customer_id=twin.customer_id,
        scenario_name=twin.scenario_name,
        employment_status=twin.employment_status,
        currency=twin.currency,
        true_monthly_salary_minor=twin.true_monthly_salary_minor,
        income_source_id=twin.income_source_id,
        primary_account_id=account.account_id,
        opening_balance_minor=account.opening_balance_minor,
    )

    transactions = tuple(
        TransactionGroundTruth(
            event_id=event.event_id,
            entry_id=entry.entry_id,
            customer_id=event.customer_id,
            account_id=entry.account_id,
            occurred_at=event.occurred_at.isoformat(),
            economic_type=event.economic_type,
            direction=entry.direction,
            amount_minor=event.amount_minor,
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

    customer_months: list[CustomerMonthGroundTruth] = []
    events_by_month: dict[str, list[FinancialEvent]] = {}
    entries_by_month: dict[str, list[LedgerEntry]] = {}
    for event in run.events:
        events_by_month.setdefault(event.occurred_at.strftime("%Y-%m"), []).append(event)
    for entry in run.ledger_entries:
        entries_by_month.setdefault(entry.posted_at.strftime("%Y-%m"), []).append(entry)

    running_balance = account.opening_balance_minor
    for month_index in range(run.months):
        current_month = month_start(run.start_date, month_index)
        month_key = current_month.strftime("%Y-%m")
        month_events = events_by_month.get(month_key, [])
        month_entries = entries_by_month.get(month_key, [])
        opening_balance = running_balance
        true_income = sum(
            event.amount_minor
            for event in month_events
            if event.economic_type is EconomicType.INCOME
        )
        true_expenses = sum(
            event.amount_minor
            for event in month_events
            if event.economic_type is EconomicType.EXPENSE
        )
        if month_entries:
            running_balance = month_entries[-1].balance_after_minor
        customer_months.append(
            CustomerMonthGroundTruth(
                customer_id=twin.customer_id,
                month=month_key,
                currency=twin.currency,
                true_income_minor=true_income,
                true_expenses_minor=true_expenses,
                income_event_count=sum(
                    event.economic_type is EconomicType.INCOME for event in month_events
                ),
                expense_event_count=sum(
                    event.economic_type is EconomicType.EXPENSE for event in month_events
                ),
                opening_balance_minor=opening_balance,
                closing_balance_minor=running_balance,
            )
        )

    economic_income = sum(
        event.amount_minor for event in run.events if event.economic_type is EconomicType.INCOME
    )
    truth_income = sum(record.true_income_minor for record in customer_months)
    if economic_income != truth_income:
        raise RuntimeError(
            f"Monthly ground-truth income {truth_income} does not match "
            f"economic income {economic_income}."
        )

    return GroundTruthBundle(
        customers=(customer,),
        customer_months=tuple(customer_months),
        transactions=transactions,
    )
