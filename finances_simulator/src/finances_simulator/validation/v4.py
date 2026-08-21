"""Cross-record invariants for schema-1.4 transitions and anomaly labels."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from finances_simulator.domain.accounts import Direction, LedgerEntry
from finances_simulator.domain.customer import CustomerTwinV4
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.domain.income import IncomeSource
from finances_simulator.domain.life_events import (
    AnomalyType,
    FinancialAnomaly,
    IncomeSourceState,
    LifeEventTransition,
    LifeEventType,
)
from finances_simulator.validation.invariants import InvariantViolation

if TYPE_CHECKING:
    from finances_simulator.contracts.ground_truth_v4 import (
        BalanceSheetGroundTruthV4,
        CustomerMonthGroundTruthV4,
    )

_FINANCIAL_LIFE_EVENT_TYPES = {
    LifeEventType.PROPERTY_PURCHASE,
    LifeEventType.VEHICLE_PURCHASE,
    LifeEventType.BONUS,
    LifeEventType.INHERITANCE,
    LifeEventType.MEDICAL_EXPENSE,
    LifeEventType.VACATION,
}

_ANOMALY_ECONOMIC_TYPES = {
    AnomalyType.LARGE_PIX_TRANSFER: EconomicType.OWN_TRANSFER,
    AnomalyType.REFUND: EconomicType.REFUND,
    AnomalyType.ASSET_SALE: EconomicType.ASSET_SALE,
    AnomalyType.INVESTMENT_REDEMPTION: EconomicType.INVESTMENT_REDEMPTION,
}


def _source_state_at(
    source: IncomeSource,
    event: FinancialEvent,
    transitions: tuple[LifeEventTransition, ...],
) -> IncomeSourceState:
    state = IncomeSourceState(
        income_source_id=source.income_source_id,
        source_ref=source.source_ref,
        active=True,
        base_amount_minor=source.base_amount_minor,
        payer=source.payer,
        description=source.description,
    )
    for transition in transitions:
        if transition.effective_date > event.occurred_at:
            break
        state = next(
            item
            for item in transition.income_sources_after
            if item.income_source_id == source.income_source_id
        )
    return state


def validate_life_event_simulation(
    *,
    twin: CustomerTwinV4,
    sources: Iterable[IncomeSource],
    transitions: Iterable[LifeEventTransition],
    anomalies: Iterable[FinancialAnomaly],
    events: Iterable[FinancialEvent],
    entries: Iterable[LedgerEntry],
) -> None:
    """Validate state continuity, income attribution, and anomaly economic types."""

    source_items = tuple(sources)
    transition_items = tuple(transitions)
    anomaly_items = tuple(anomalies)
    event_items = tuple(events)
    entry_items = tuple(entries)
    source_by_id = {source.income_source_id: source for source in source_items}
    event_by_id = {event.event_id: event for event in event_items}
    entries_by_event: dict[str, list[LedgerEntry]] = {}
    for entry in entry_items:
        entries_by_event.setdefault(entry.event_id, []).append(entry)

    if len(source_by_id) != len(source_items):
        raise InvariantViolation("Income-source IDs must be unique.")
    if len({item.life_event_id for item in transition_items}) != len(transition_items):
        raise InvariantViolation("Life-event IDs must be unique.")
    if len({item.life_event_ref for item in transition_items}) != len(transition_items):
        raise InvariantViolation("Life-event refs must be unique.")
    if tuple(sorted(transition_items, key=lambda item: item.effective_date)) != transition_items:
        raise InvariantViolation("Life-event transitions must be effective-date ordered.")

    expected_state = twin.initial_life_state
    expected_sources = tuple(
        IncomeSourceState(
            income_source_id=source.income_source_id,
            source_ref=source.source_ref,
            active=True,
            base_amount_minor=source.base_amount_minor,
            payer=source.payer,
            description=source.description,
        )
        for source in sorted(source_items, key=lambda item: item.source_ref)
    )
    for transition in transition_items:
        has_financial_event = transition.event_type in _FINANCIAL_LIFE_EVENT_TYPES
        if (
            transition.customer_id != twin.customer_id
            or transition.state_before != expected_state
            or transition.income_sources_before != expected_sources
            or has_financial_event != (transition.financial_event_id is not None)
        ):
            raise InvariantViolation(
                f"Life-event transition {transition.life_event_id} is not continuous."
            )
        if transition.financial_event_id is not None:
            event = event_by_id.get(transition.financial_event_id)
            if event is None or (
                event.occurred_at != transition.effective_date
                or event.metadata.get("life_event_id") != transition.life_event_id
                or event.metadata.get("life_event_type") != transition.event_type.value
            ):
                raise InvariantViolation(
                    f"Life event {transition.life_event_id} lacks its financial event."
                )
        expected_state = transition.state_after
        expected_sources = transition.income_sources_after
    if expected_state != twin.final_life_state:
        raise InvariantViolation("Final customer life state does not match transitions.")

    for event in event_items:
        if event.economic_type is not EconomicType.INCOME:
            if event.income_source_id is not None:
                raise InvariantViolation(
                    f"Non-income event {event.event_id} has income-source attribution."
                )
            continue
        source = source_by_id.get(event.income_source_id or "")
        event_entries = entries_by_event.get(event.event_id, [])
        if source is None:
            raise InvariantViolation(f"Income event {event.event_id} lacks a source.")
        is_bonus = event.metadata.get("life_event_type") == LifeEventType.BONUS.value
        if is_bonus:
            bonus_transition = next(
                (
                    item
                    for item in transition_items
                    if item.life_event_id == event.metadata.get("life_event_id")
                ),
                None,
            )
            if bonus_transition is None:
                raise InvariantViolation(
                    f"Bonus income event {event.event_id} lacks its transition."
                )
            state = next(
                item
                for item in bonus_transition.income_sources_after
                if item.income_source_id == source.income_source_id
            )
        else:
            state = _source_state_at(source, event, transition_items)
        if (
            not state.active
            or event.customer_id != source.customer_id
            or event.currency != source.currency
            or event.source_entity != state.payer
            or event.destination_entity != source.destination_account_id
            or event.metadata.get("income_kind") != source.income_kind.value
            or event.metadata.get("source_ref") != source.source_ref
            or (not is_bonus and event.description != state.description)
            or len(event_entries) != 1
            or event_entries[0].account_id != source.destination_account_id
            or event_entries[0].posted_at != event.occurred_at
            or event_entries[0].direction is not Direction.CREDIT
            or event_entries[0].amount_minor != event.amount_minor
        ):
            raise InvariantViolation(
                f"Income event {event.event_id} does not reconcile to effective source state."
            )

    if len({item.anomaly_id for item in anomaly_items}) != len(anomaly_items):
        raise InvariantViolation("Anomaly IDs must be unique.")
    if len({item.anomaly_ref for item in anomaly_items}) != len(anomaly_items):
        raise InvariantViolation("Anomaly refs must be unique.")
    for anomaly in anomaly_items:
        event = event_by_id.get(anomaly.financial_event_id)
        expected_type = _ANOMALY_ECONOMIC_TYPES[anomaly.anomaly_type]
        if event is None or (
            anomaly.customer_id != twin.customer_id
            or anomaly.occurred_at != event.occurred_at
            or anomaly.economic_type is not expected_type
            or event.economic_type is not expected_type
        ):
            raise InvariantViolation(
                f"Anomaly {anomaly.anomaly_id} does not retain its economic type."
            )


def validate_balance_sheet_truth_v4(
    records: Iterable[BalanceSheetGroundTruthV4],
    customer_months: Iterable[CustomerMonthGroundTruthV4],
) -> None:
    """Validate continuity and Phase-5 external-inflow net-worth bridge."""

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
            + month.external_inflows_minor
            - month.true_expenses_minor
            - month.loan_interest_paid_minor
            + month.investment_return_minor
        )
        if record.net_worth_minor - record.opening_net_worth_minor != expected_change:
            raise InvariantViolation(
                f"Balance sheet {record.balance_sheet_id} violates the Phase-5 net-worth bridge."
            )
        previous = record


__all__ = ["validate_balance_sheet_truth_v4", "validate_life_event_simulation"]
