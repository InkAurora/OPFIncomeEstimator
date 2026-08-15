"""Derive the estimator-visible view from the account ledger."""

from dataclasses import dataclass
from uuid import UUID

from finances_simulator.domain.accounts import Account, LedgerEntry
from finances_simulator.observations.contracts import (
    ObservedAccount,
    ObservedBalance,
    ObservedTransaction,
)
from finances_simulator.simulation.primitives import (
    deterministic_id,
    month_end,
    month_start,
)


@dataclass(frozen=True, slots=True)
class ObservationBundle:
    accounts: tuple[ObservedAccount, ...]
    balances: tuple[ObservedBalance, ...]
    transactions: tuple[ObservedTransaction, ...]


def project_observations(
    *,
    account: Account,
    ledger_entries: tuple[LedgerEntry, ...],
    start_date,
    months: int,
    namespace: UUID,
) -> ObservationBundle:
    """Project complete V0 observations without access to truth events or latent state."""

    observed_account = ObservedAccount(
        customer_id=account.customer_id,
        account_id=account.account_id,
        institution_id=account.institution_id,
        institution_name=account.institution_name,
        account_label=account.account_label,
        account_type=account.account_type,
        currency=account.currency,
        opened_on=account.opened_on.isoformat(),
    )
    transactions = tuple(
        ObservedTransaction(
            transaction_id=entry.entry_id,
            customer_id=account.customer_id,
            account_id=entry.account_id,
            posted_at=entry.posted_at.isoformat(),
            direction=entry.direction,
            amount_minor=entry.amount_minor,
            currency=account.currency,
            description=entry.description,
            balance_after_minor=entry.balance_after_minor,
        )
        for entry in ledger_entries
    )

    balances: list[ObservedBalance] = []
    running_balance = account.opening_balance_minor
    entry_index = 0
    for month_index in range(months):
        reference_date = month_end(month_start(start_date, month_index))
        while (
            entry_index < len(ledger_entries)
            and ledger_entries[entry_index].posted_at <= reference_date
        ):
            running_balance = ledger_entries[entry_index].balance_after_minor
            entry_index += 1
        balances.append(
            ObservedBalance(
                balance_id=deterministic_id(namespace, "balance", reference_date.isoformat()),
                customer_id=account.customer_id,
                account_id=account.account_id,
                reference_date=reference_date.isoformat(),
                balance_minor=running_balance,
                currency=account.currency,
            )
        )

    return ObservationBundle(
        accounts=(observed_account,),
        balances=tuple(balances),
        transactions=transactions,
    )
