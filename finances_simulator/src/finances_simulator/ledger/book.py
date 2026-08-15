"""Post economic events to a customer account ledger."""

from collections.abc import Iterable
from uuid import UUID

from finances_simulator.domain.accounts import Account, Direction, LedgerEntry
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.simulation.primitives import deterministic_id


class LedgerPostingError(ValueError):
    """Raised when an event cannot be posted to the V0 ledger."""


def post_events(
    account: Account,
    events: Iterable[FinancialEvent],
    namespace: UUID,
) -> tuple[LedgerEntry, ...]:
    """Post events in stable order and return immutable ledger entries."""

    ordered_events = sorted(events, key=lambda event: (event.occurred_at, event.event_id))
    balance = account.opening_balance_minor
    entries: list[LedgerEntry] = []

    for event in ordered_events:
        if event.customer_id != account.customer_id:
            raise LedgerPostingError(f"Event {event.event_id} belongs to a different customer.")
        if event.currency != account.currency:
            raise LedgerPostingError(
                f"Event {event.event_id} currency {event.currency} does not match "
                f"account currency {account.currency}."
            )

        if event.economic_type is EconomicType.INCOME:
            direction = Direction.CREDIT
            balance += event.amount_minor
        elif event.economic_type is EconomicType.EXPENSE:
            direction = Direction.DEBIT
            balance -= event.amount_minor
        else:  # pragma: no cover - protects future enum expansion
            raise LedgerPostingError(
                f"Economic type {event.economic_type.value} is unsupported in V0."
            )

        entries.append(
            LedgerEntry(
                entry_id=deterministic_id(namespace, "entry", event.event_id),
                event_id=event.event_id,
                account_id=account.account_id,
                posted_at=event.occurred_at,
                direction=direction,
                amount_minor=event.amount_minor,
                balance_after_minor=balance,
                description=event.description,
            )
        )

    return tuple(entries)
