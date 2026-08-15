"""Project schema-1.1 estimator observations from ledgers and card state."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from finances_simulator.domain.accounts import Account, LedgerEntry
from finances_simulator.domain.cards import (
    CardInstallment,
    CardInvoice,
    CardPurchase,
    CreditCard,
    CreditLimitSnapshot,
)
from finances_simulator.observations.contracts_v1 import (
    AccountV1,
    BalanceV1,
    CardInvoiceItemV1,
    CardInvoiceV1,
    CardTransactionV1,
    CreditCardV1,
    CreditLimitV1,
    TransactionV1,
)
from finances_simulator.simulation.primitives import (
    deterministic_id,
    month_end,
    month_start,
)


@dataclass(frozen=True, slots=True)
class ObservationBundleV1:
    accounts: tuple[AccountV1, ...]
    balances: tuple[BalanceV1, ...]
    transactions: tuple[TransactionV1, ...]
    credit_cards: tuple[CreditCardV1, ...]
    credit_limits: tuple[CreditLimitV1, ...]
    credit_card_transactions: tuple[CardTransactionV1, ...]
    credit_card_invoices: tuple[CardInvoiceV1, ...]
    credit_card_invoice_items: tuple[CardInvoiceItemV1, ...]


def project_observations_v1(
    *,
    accounts: tuple[Account, ...],
    ledger_entries: tuple[LedgerEntry, ...],
    cards: tuple[CreditCard, ...],
    card_purchases: tuple[CardPurchase, ...],
    card_installments: tuple[CardInstallment, ...],
    card_invoices: tuple[CardInvoice, ...],
    credit_limit_snapshots: tuple[CreditLimitSnapshot, ...],
    start_date: date,
    end_date: date,
    months: int,
    namespace: UUID,
) -> ObservationBundleV1:
    """Build complete observations without economic classifications or transfer labels."""

    account_by_id = {account.account_id: account for account in accounts}
    card_by_id = {card.card_id: card for card in cards}
    purchase_by_id = {purchase.purchase_id: purchase for purchase in card_purchases}
    payment_entry_by_event = {
        entry.event_id: entry for entry in ledger_entries if entry.event_id is not None
    }

    observed_accounts = tuple(
        AccountV1(
            customer_id=account.customer_id,
            account_id=account.account_id,
            institution_id=account.institution_id,
            institution_name=account.institution_name,
            account_label=account.account_label,
            account_type=account.account_type,
            currency=account.currency,
            opened_on=account.opened_on.isoformat(),
        )
        for account in sorted(accounts, key=lambda item: item.account_id)
    )
    observed_transactions = tuple(
        TransactionV1(
            transaction_id=entry.entry_id,
            customer_id=account_by_id[entry.account_id].customer_id,
            account_id=entry.account_id,
            posted_at=entry.posted_at.isoformat(),
            direction=entry.direction,
            amount_minor=entry.amount_minor,
            currency=account_by_id[entry.account_id].currency,
            description=entry.description,
            balance_after_minor=entry.balance_after_minor,
        )
        for entry in ledger_entries
    )

    entries_by_account: dict[str, list[LedgerEntry]] = {
        account.account_id: [] for account in accounts
    }
    for entry in ledger_entries:
        entries_by_account[entry.account_id].append(entry)
    running_balances = {account.account_id: account.opening_balance_minor for account in accounts}
    entry_indexes = {account.account_id: 0 for account in accounts}
    balances: list[BalanceV1] = []
    for month_index in range(months):
        reference_date = month_end(month_start(start_date, month_index))
        for account in sorted(accounts, key=lambda item: item.account_id):
            account_entries = entries_by_account[account.account_id]
            entry_index = entry_indexes[account.account_id]
            while (
                entry_index < len(account_entries)
                and account_entries[entry_index].posted_at <= reference_date
            ):
                running_balances[account.account_id] = account_entries[
                    entry_index
                ].balance_after_minor
                entry_index += 1
            entry_indexes[account.account_id] = entry_index
            balances.append(
                BalanceV1(
                    balance_id=deterministic_id(
                        namespace,
                        "balance",
                        f"{account.account_id}:{reference_date.isoformat()}",
                    ),
                    customer_id=account.customer_id,
                    account_id=account.account_id,
                    reference_date=reference_date.isoformat(),
                    balance_minor=running_balances[account.account_id],
                    currency=account.currency,
                )
            )

    observed_cards = tuple(
        CreditCardV1(
            customer_id=card.customer_id,
            card_id=card.card_id,
            institution_id=card.institution_id,
            institution_name=card.institution_name,
            card_label=card.card_label,
            currency=card.currency,
            opened_on=card.opened_on.isoformat(),
        )
        for card in sorted(cards, key=lambda item: item.card_id)
    )
    observed_limits = tuple(
        CreditLimitV1(
            credit_limit_id=snapshot.snapshot_id,
            customer_id=snapshot.customer_id,
            card_id=snapshot.card_id,
            reference_date=snapshot.reference_date.isoformat(),
            total_limit_minor=snapshot.total_limit_minor,
            used_limit_minor=snapshot.used_limit_minor,
            available_limit_minor=snapshot.available_limit_minor,
            currency=snapshot.currency,
        )
        for snapshot in credit_limit_snapshots
    )
    observed_card_transactions = tuple(
        CardTransactionV1(
            card_transaction_id=purchase.purchase_id,
            customer_id=purchase.customer_id,
            card_id=purchase.card_id,
            occurred_at=purchase.purchased_at.isoformat(),
            amount_minor=purchase.amount_minor,
            currency=purchase.currency,
            description=purchase.description,
            installment_count=purchase.installment_count,
        )
        for purchase in sorted(
            card_purchases,
            key=lambda item: (item.purchased_at, item.event_id),
        )
    )
    observed_invoices = tuple(
        CardInvoiceV1(
            invoice_id=invoice.invoice_id,
            customer_id=invoice.customer_id,
            card_id=invoice.card_id,
            statement_close_date=invoice.statement_close_date.isoformat(),
            due_date=invoice.due_date.isoformat(),
            amount_due_minor=invoice.amount_due_minor,
            paid_amount_minor=invoice.paid_amount_minor,
            currency=card_by_id[invoice.card_id].currency,
            status=invoice.status.value,
            paid_at=invoice.paid_at.isoformat() if invoice.paid_at else None,
            payment_transaction_id=(
                payment_entry_by_event[invoice.payment_event_id].entry_id
                if invoice.payment_event_id is not None
                else None
            ),
        )
        for invoice in card_invoices
    )
    observed_invoice_items = tuple(
        CardInvoiceItemV1(
            invoice_item_id=installment.invoice_item_id,
            customer_id=card_by_id[installment.card_id].customer_id,
            card_id=installment.card_id,
            invoice_id=installment.invoice_id,
            card_transaction_id=installment.purchase_id,
            installment_number=installment.installment_number,
            installment_count=installment.installment_count,
            amount_minor=installment.amount_minor,
            currency=card_by_id[installment.card_id].currency,
            description=purchase_by_id[installment.purchase_id].description,
        )
        for installment in card_installments
        if installment.statement_close_date <= end_date
    )

    return ObservationBundleV1(
        accounts=observed_accounts,
        balances=tuple(balances),
        transactions=observed_transactions,
        credit_cards=observed_cards,
        credit_limits=observed_limits,
        credit_card_transactions=observed_card_transactions,
        credit_card_invoices=observed_invoices,
        credit_card_invoice_items=observed_invoice_items,
    )


__all__ = ["ObservationBundleV1", "project_observations_v1"]
