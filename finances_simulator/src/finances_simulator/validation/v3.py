"""Cross-record invariants for schema-1.3 income sources."""

from collections.abc import Iterable

from finances_simulator.domain.accounts import Direction, LedgerEntry
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.domain.income import IncomeSource
from finances_simulator.validation.invariants import InvariantViolation


def validate_income_simulation(
    *,
    sources: Iterable[IncomeSource],
    events: Iterable[FinancialEvent],
    entries: Iterable[LedgerEntry],
) -> None:
    """Validate private source attribution and each accepted cash receipt."""

    source_items = tuple(sources)
    event_items = tuple(events)
    entry_items = tuple(entries)
    source_by_id = {source.income_source_id: source for source in source_items}
    if len(source_by_id) != len(source_items):
        raise InvariantViolation("Income-source IDs must be unique.")

    entries_by_event: dict[str, list[LedgerEntry]] = {}
    for entry in entry_items:
        entries_by_event.setdefault(entry.event_id, []).append(entry)

    for event in event_items:
        if event.economic_type is not EconomicType.INCOME:
            if event.income_source_id is not None:
                raise InvariantViolation(
                    f"Non-income event {event.event_id} has income-source attribution."
                )
            continue
        source = source_by_id.get(event.income_source_id or "")
        event_entries = entries_by_event.get(event.event_id, [])
        if source is None or (
            event.customer_id != source.customer_id
            or event.currency != source.currency
            or event.source_entity != source.payer
            or event.destination_entity != source.destination_account_id
            or event.description != source.description
            or event.metadata.get("income_kind") != source.income_kind.value
            or event.metadata.get("source_ref") != source.source_ref
            or len(event_entries) != 1
            or event_entries[0].account_id != source.destination_account_id
            or event_entries[0].posted_at != event.occurred_at
            or event_entries[0].direction is not Direction.CREDIT
            or event_entries[0].amount_minor != event.amount_minor
        ):
            raise InvariantViolation(
                f"Income event {event.event_id} does not reconcile to its source."
            )


__all__ = ["validate_income_simulation"]
