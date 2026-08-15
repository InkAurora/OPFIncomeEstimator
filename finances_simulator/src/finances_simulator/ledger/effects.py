"""Post explicit financial effects across multiple account ledgers."""

from collections.abc import Iterable
from datetime import date
from enum import IntEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from finances_simulator.domain.accounts import Account, Direction, LedgerEntry
from finances_simulator.ledger.book import LedgerPostingError
from finances_simulator.simulation.primitives import deterministic_id


class PostingPriority(IntEnum):
    """Causal ordering for account effects sharing a posting date."""

    INCOME = 10
    LOAN_DISBURSEMENT = 15
    OWN_TRANSFER = 20
    INVESTMENT_CONTRIBUTION = 22
    INVESTMENT_REDEMPTION = 24
    CARD_PAYMENT = 30
    LOAN_PAYMENT = 35
    EXPENSE = 40


class LedgerEffect(BaseModel):
    """One explicit account-level effect of an economic event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    account_id: str
    posted_at: date
    direction: Direction
    amount_minor: int = Field(gt=0)
    posting_priority: PostingPriority
    entry_key: str
    transfer_group_id: str | None = None
    description: str


def post_ledger_effects(
    accounts: Iterable[Account],
    effects: Iterable[LedgerEffect],
    namespace: UUID,
) -> tuple[LedgerEntry, ...]:
    """Post explicit effects to independent account balances in stable order."""

    account_by_id: dict[str, Account] = {}
    for account in accounts:
        if account.account_id in account_by_id:
            raise LedgerPostingError(f"Duplicate account ID: {account.account_id}")
        account_by_id[account.account_id] = account

    ordered_effects = sorted(
        effects,
        key=lambda effect: (
            effect.posted_at,
            effect.posting_priority,
            effect.event_id,
            deterministic_id(
                namespace,
                "entry",
                f"{effect.event_id}:{effect.entry_key}",
            ),
        ),
    )
    balances = {
        account_id: account.opening_balance_minor for account_id, account in account_by_id.items()
    }
    seen_entry_keys: set[tuple[str, str]] = set()
    entries: list[LedgerEntry] = []

    for effect in ordered_effects:
        account = account_by_id.get(effect.account_id)
        if account is None:
            raise LedgerPostingError(
                f"Effect {effect.event_id}:{effect.entry_key} references unknown account "
                f"{effect.account_id}."
            )

        effect_key = (effect.event_id, effect.entry_key)
        if effect_key in seen_entry_keys:
            raise LedgerPostingError(
                f"Duplicate ledger effect key: {effect.event_id}:{effect.entry_key}"
            )
        seen_entry_keys.add(effect_key)

        balance = balances[account.account_id]
        if effect.direction is Direction.CREDIT:
            balance += effect.amount_minor
        else:
            balance -= effect.amount_minor
        balances[account.account_id] = balance

        entries.append(
            LedgerEntry(
                entry_id=deterministic_id(
                    namespace,
                    "entry",
                    f"{effect.event_id}:{effect.entry_key}",
                ),
                event_id=effect.event_id,
                account_id=effect.account_id,
                posted_at=effect.posted_at,
                direction=effect.direction,
                amount_minor=effect.amount_minor,
                balance_after_minor=balance,
                transfer_group_id=effect.transfer_group_id,
                description=effect.description,
            )
        )

    return tuple(entries)


__all__ = ["LedgerEffect", "PostingPriority", "post_ledger_effects"]
