"""Runtime invariants for generated financial data."""

from collections.abc import Iterable

from finances_simulator.domain.accounts import Account, Direction, LedgerEntry


class InvariantViolation(RuntimeError):
    """Raised when generated financial records do not reconcile."""


def validate_reconciliation(account: Account, entries: Iterable[LedgerEntry]) -> int:
    """Validate every running balance and return the closing balance."""

    balance = account.opening_balance_minor
    previous_sort_key = None
    seen_entry_ids: set[str] = set()

    for entry in entries:
        if entry.entry_id in seen_entry_ids:
            raise InvariantViolation(f"Duplicate ledger entry ID: {entry.entry_id}")
        seen_entry_ids.add(entry.entry_id)

        if entry.account_id != account.account_id:
            raise InvariantViolation(
                f"Entry {entry.entry_id} references unexpected account {entry.account_id}."
            )
        sort_key = (entry.posted_at, entry.event_id)
        if previous_sort_key is not None and sort_key < previous_sort_key:
            raise InvariantViolation("Ledger entries are not in deterministic posting order.")
        previous_sort_key = sort_key

        if entry.direction is Direction.CREDIT:
            balance += entry.amount_minor
        else:
            balance -= entry.amount_minor
        if entry.balance_after_minor != balance:
            raise InvariantViolation(
                f"Entry {entry.entry_id} has balance {entry.balance_after_minor}; "
                f"expected {balance}."
            )

    return balance
