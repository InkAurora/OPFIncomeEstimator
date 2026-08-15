"""Runtime invariants for generated financial data."""

from bisect import bisect_right
from collections.abc import Iterable
from datetime import date

from finances_simulator.domain.accounts import Account, Direction, LedgerEntry
from finances_simulator.domain.cards import (
    CardInstallment,
    CardInvoice,
    CardPurchase,
    CreditCard,
    CreditLimitSnapshot,
    InvoiceStatus,
)
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.simulation.primitives import month_end, month_start


class InvariantViolation(RuntimeError):
    """Raised when generated financial records do not reconcile."""


def validate_reconciliation(
    account: Account,
    entries: Iterable[LedgerEntry],
    *,
    require_legacy_order: bool = True,
) -> int:
    """Validate every running balance and return the closing balance.

    Schema 1.0 ledgers use event-ID order within a date. Newer engines may supply
    a causal order, which is checked for nondecreasing dates instead.
    """

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
        sort_key = (entry.posted_at, entry.event_id) if require_legacy_order else (entry.posted_at,)
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


def validate_account_ledgers(
    accounts: Iterable[Account],
    entries: Iterable[LedgerEntry],
) -> dict[str, int]:
    """Reconcile multiple account ledgers independently."""

    account_list = tuple(accounts)
    account_by_id = {account.account_id: account for account in account_list}
    if len(account_by_id) == 0:
        raise InvariantViolation("At least one account is required.")
    if len(account_by_id) != len(account_list):
        raise InvariantViolation("Account IDs must be unique.")
    entries_by_account: dict[str, list[LedgerEntry]] = {
        account_id: [] for account_id in account_by_id
    }
    seen_entry_ids: set[str] = set()
    for entry in entries:
        if entry.entry_id in seen_entry_ids:
            raise InvariantViolation(f"Duplicate ledger entry ID: {entry.entry_id}")
        seen_entry_ids.add(entry.entry_id)
        if entry.account_id not in account_by_id:
            raise InvariantViolation(
                f"Entry {entry.entry_id} references unknown account {entry.account_id}."
            )
        entries_by_account[entry.account_id].append(entry)

    return {
        account_id: validate_reconciliation(
            account,
            entries_by_account[account_id],
            require_legacy_order=False,
        )
        for account_id, account in account_by_id.items()
    }


def validate_transfer_pairs(
    events: Iterable[FinancialEvent],
    entries: Iterable[LedgerEntry],
) -> None:
    """Validate paired, cash-conserving own-account transfers."""

    event_list = tuple(events)
    event_by_id = {event.event_id: event for event in event_list}
    if len(event_by_id) != len(event_list):
        raise InvariantViolation("Financial event IDs must be unique.")
    entries_by_event: dict[str, list[LedgerEntry]] = {}
    event_by_transfer_group: dict[str, str] = {}
    for entry in entries:
        if entry.event_id not in event_by_id:
            raise InvariantViolation(
                f"Ledger entry {entry.entry_id} references unknown event {entry.event_id}."
            )
        entries_by_event.setdefault(entry.event_id, []).append(entry)
        if entry.transfer_group_id is not None:
            event = event_by_id.get(entry.event_id)
            if event is None or event.economic_type is not EconomicType.OWN_TRANSFER:
                raise InvariantViolation(
                    f"Transfer entry {entry.entry_id} lacks an OWN_TRANSFER event."
                )

    for event in event_by_id.values():
        if event.economic_type is not EconomicType.OWN_TRANSFER:
            continue
        transfer_entries = entries_by_event.get(event.event_id, [])
        if len(transfer_entries) != 2:
            raise InvariantViolation(
                f"Transfer event {event.event_id} must have exactly two ledger entries."
            )
        debit_entries = [entry for entry in transfer_entries if entry.direction is Direction.DEBIT]
        credit_entries = [
            entry for entry in transfer_entries if entry.direction is Direction.CREDIT
        ]
        transfer_groups = {entry.transfer_group_id for entry in transfer_entries}
        transfer_group_id = next(iter(transfer_groups), None)
        if (
            len(debit_entries) != 1
            or len(credit_entries) != 1
            or None in transfer_groups
            or len(transfer_groups) != 1
            or debit_entries[0].amount_minor != credit_entries[0].amount_minor
            or debit_entries[0].amount_minor != event.amount_minor
            or debit_entries[0].account_id == credit_entries[0].account_id
            or debit_entries[0].account_id != event.source_entity
            or credit_entries[0].account_id != event.destination_entity
            or debit_entries[0].posted_at != event.occurred_at
            or credit_entries[0].posted_at != event.occurred_at
        ):
            raise InvariantViolation(f"Transfer event {event.event_id} does not reconcile.")
        assert transfer_group_id is not None
        previous_event_id = event_by_transfer_group.setdefault(
            transfer_group_id,
            event.event_id,
        )
        if previous_event_id != event.event_id:
            raise InvariantViolation(
                f"Transfer group {transfer_group_id} is shared by multiple events."
            )


def validate_card_simulation(
    *,
    cards: Iterable[CreditCard],
    purchases: Iterable[CardPurchase],
    installments: Iterable[CardInstallment],
    invoices: Iterable[CardInvoice],
    snapshots: Iterable[CreditLimitSnapshot],
    events: Iterable[FinancialEvent],
    entries: Iterable[LedgerEntry],
    start_date: date,
    end_date: date,
    months: int,
) -> None:
    """Validate the purchase, invoice, settlement, and limit causal chain."""

    card_items = tuple(cards)
    purchase_items = tuple(purchases)
    installment_items = tuple(installments)
    invoice_items = tuple(invoices)
    snapshot_items = tuple(snapshots)
    event_items = tuple(events)
    entry_items = tuple(entries)

    def unique_by_id(items: tuple[object, ...], attribute: str, label: str) -> dict[str, object]:
        indexed = {getattr(item, attribute): item for item in items}
        if len(indexed) != len(items):
            raise InvariantViolation(f"{label} IDs must be unique.")
        return indexed

    card_by_id = unique_by_id(card_items, "card_id", "Card")
    purchase_by_id = unique_by_id(purchase_items, "purchase_id", "Card purchase")
    unique_by_id(installment_items, "invoice_item_id", "Invoice item")
    invoice_by_id = unique_by_id(invoice_items, "invoice_id", "Invoice")
    unique_by_id(snapshot_items, "snapshot_id", "Credit-limit snapshot")
    event_by_id = unique_by_id(event_items, "event_id", "Financial event")

    expected_purchase_order = tuple(
        sorted(
            purchase_items,
            key=lambda item: (
                item.purchased_at,
                item.rule_id,
                item.occurrence_index,
            ),
        )
    )
    if purchase_items != expected_purchase_order:
        raise InvariantViolation("Card purchases are not in authorization order.")

    entries_by_event: dict[str, list[LedgerEntry]] = {}
    for entry in entry_items:
        entries_by_event.setdefault(entry.event_id, []).append(entry)
    installments_by_purchase: dict[str, list[CardInstallment]] = {}
    installments_by_invoice: dict[str, list[CardInstallment]] = {}
    installments_by_card: dict[str, list[CardInstallment]] = {}
    for installment in installment_items:
        if installment.purchase_id not in purchase_by_id:
            raise InvariantViolation(
                f"Invoice item {installment.invoice_item_id} references an unknown purchase."
            )
        if installment.card_id not in card_by_id:
            raise InvariantViolation(
                f"Invoice item {installment.invoice_item_id} references an unknown card."
            )
        installments_by_purchase.setdefault(installment.purchase_id, []).append(installment)
        installments_by_invoice.setdefault(installment.invoice_id, []).append(installment)
        installments_by_card.setdefault(installment.card_id, []).append(installment)

    due_dates_by_card: dict[str, list[date]] = {}
    due_prefix_by_card: dict[str, list[int]] = {}
    for card_id, scheduled in installments_by_card.items():
        ordered_due_amounts = sorted((item.due_date, item.amount_minor) for item in scheduled)
        due_dates_by_card[card_id] = [item[0] for item in ordered_due_amounts]
        prefix = [0]
        for _, amount_minor in ordered_due_amounts:
            prefix.append(prefix[-1] + amount_minor)
        due_prefix_by_card[card_id] = prefix

    def paid_through(card_id: str, reference_date: date) -> int:
        dates = due_dates_by_card.get(card_id, [])
        prefix = due_prefix_by_card.get(card_id, [0])
        return prefix[bisect_right(dates, reference_date)]

    purchase_totals_by_card: dict[str, int] = {card_id: 0 for card_id in card_by_id}
    purchase_event_ids: set[str] = set()
    for purchase in purchase_items:
        card = card_by_id.get(purchase.card_id)
        event = event_by_id.get(purchase.event_id)
        if card is None or not isinstance(card, CreditCard):
            raise InvariantViolation(f"Purchase {purchase.purchase_id} references an unknown card.")
        if purchase.event_id in purchase_event_ids:
            raise InvariantViolation("Card purchase event IDs must be unique.")
        purchase_event_ids.add(purchase.event_id)
        if not isinstance(event, FinancialEvent) or (
            event.economic_type is not EconomicType.EXPENSE
            or event.customer_id != card.customer_id
            or event.occurred_at != purchase.purchased_at
            or event.amount_minor != purchase.amount_minor
            or event.currency != purchase.currency
            or purchase.customer_id != card.customer_id
            or purchase.currency != card.currency
            or event.source_entity != purchase.card_id
            or event.destination_entity != purchase.merchant
            or event.description != purchase.description
        ):
            raise InvariantViolation(
                f"Purchase {purchase.purchase_id} lacks its matching EXPENSE event."
            )
        if entries_by_event.get(purchase.event_id):
            raise InvariantViolation(
                f"Card purchase {purchase.purchase_id} must not post to a deposit ledger."
            )

        scheduled = installments_by_purchase.get(purchase.purchase_id, [])
        if (
            len(scheduled) != purchase.installment_count
            or {item.installment_number for item in scheduled}
            != set(range(1, purchase.installment_count + 1))
            or sum(item.amount_minor for item in scheduled) != purchase.amount_minor
            or any(item.card_id != purchase.card_id for item in scheduled)
            or any(item.description != purchase.description for item in scheduled)
        ):
            raise InvariantViolation(
                f"Purchase {purchase.purchase_id} has an invalid installment schedule."
            )

        purchase_totals_by_card[purchase.card_id] += purchase.amount_minor
        expected_used = purchase_totals_by_card[purchase.card_id] - paid_through(
            purchase.card_id, purchase.purchased_at
        )
        maximum_used = card.credit_limit_minor * card.maximum_utilization_basis_points // 10_000
        if (
            purchase.used_limit_after_purchase_minor != expected_used
            or expected_used > maximum_used
        ):
            raise InvariantViolation(
                f"Purchase {purchase.purchase_id} violates card utilization state."
            )

    expected_invoice_ids = {
        item.invoice_id for item in installment_items if item.statement_close_date <= end_date
    }
    if set(invoice_by_id) != expected_invoice_ids:
        raise InvariantViolation("Emitted invoices do not match closed statement items.")

    payment_event_ids: set[str] = set()
    for invoice in invoice_items:
        card = card_by_id.get(invoice.card_id)
        scheduled = installments_by_invoice.get(invoice.invoice_id, [])
        if not isinstance(card, CreditCard) or (
            not scheduled
            or invoice.customer_id != card.customer_id
            or any(
                item.card_id != invoice.card_id
                or item.statement_close_date != invoice.statement_close_date
                or item.due_date != invoice.due_date
                for item in scheduled
            )
            or set(invoice.installment_ids) != {item.invoice_item_id for item in scheduled}
            or invoice.amount_due_minor != sum(item.amount_minor for item in scheduled)
        ):
            raise InvariantViolation(f"Invoice {invoice.invoice_id} does not reconcile to items.")

        should_be_paid = invoice.due_date <= end_date
        if should_be_paid != (invoice.status is InvoiceStatus.PAID):
            raise InvariantViolation(f"Invoice {invoice.invoice_id} has an invalid status.")
        if invoice.payment_event_id is None:
            continue
        if invoice.payment_event_id in payment_event_ids:
            raise InvariantViolation("Invoice payment event IDs must be unique.")
        payment_event_ids.add(invoice.payment_event_id)
        event = event_by_id.get(invoice.payment_event_id)
        payment_entries = entries_by_event.get(invoice.payment_event_id, [])
        if not isinstance(event, FinancialEvent) or (
            event.economic_type is not EconomicType.CARD_PAYMENT
            or event.customer_id != card.customer_id
            or event.occurred_at != invoice.due_date
            or event.amount_minor != invoice.amount_due_minor
            or event.currency != card.currency
            or event.source_entity != card.payment_account_id
            or event.destination_entity != invoice.card_id
            or len(payment_entries) != 1
            or payment_entries[0].account_id != card.payment_account_id
            or payment_entries[0].posted_at != invoice.due_date
            or payment_entries[0].direction is not Direction.DEBIT
            or payment_entries[0].amount_minor != invoice.amount_due_minor
        ):
            raise InvariantViolation(
                f"Invoice {invoice.invoice_id} lacks its matching card payment."
            )

    actual_payment_event_ids = {
        event.event_id for event in event_items if event.economic_type is EconomicType.CARD_PAYMENT
    }
    if actual_payment_event_ids != payment_event_ids:
        raise InvariantViolation("Card payment events do not match paid invoices.")

    expected_snapshot_keys = {
        (card_id, month_end(month_start(start_date, month_index)))
        for month_index in range(months)
        for card_id in card_by_id
    }
    actual_snapshot_keys = {
        (snapshot.card_id, snapshot.reference_date) for snapshot in snapshot_items
    }
    if actual_snapshot_keys != expected_snapshot_keys or len(actual_snapshot_keys) != len(
        snapshot_items
    ):
        raise InvariantViolation("Credit-limit snapshots do not cover every card month.")

    purchases_by_card: dict[str, list[CardPurchase]] = {}
    for purchase in purchase_items:
        purchases_by_card.setdefault(purchase.card_id, []).append(purchase)
    purchase_dates_by_card: dict[str, list[date]] = {}
    purchase_prefix_by_card: dict[str, list[int]] = {}
    for card_id, accepted in purchases_by_card.items():
        ordered_purchase_amounts = sorted(
            (purchase.purchased_at, purchase.amount_minor) for purchase in accepted
        )
        purchase_dates_by_card[card_id] = [item[0] for item in ordered_purchase_amounts]
        prefix = [0]
        for _, amount_minor in ordered_purchase_amounts:
            prefix.append(prefix[-1] + amount_minor)
        purchase_prefix_by_card[card_id] = prefix
    for snapshot in snapshot_items:
        card = card_by_id.get(snapshot.card_id)
        if not isinstance(card, CreditCard):
            raise InvariantViolation(
                f"Credit-limit snapshot {snapshot.snapshot_id} references an unknown card."
            )
        purchase_dates = purchase_dates_by_card.get(snapshot.card_id, [])
        purchase_prefix = purchase_prefix_by_card.get(snapshot.card_id, [0])
        expected_used = purchase_prefix[
            bisect_right(purchase_dates, snapshot.reference_date)
        ] - paid_through(snapshot.card_id, snapshot.reference_date)
        maximum_used = card.credit_limit_minor * card.maximum_utilization_basis_points // 10_000
        if (
            snapshot.customer_id != card.customer_id
            or snapshot.currency != card.currency
            or snapshot.total_limit_minor != card.credit_limit_minor
            or snapshot.used_limit_minor != expected_used
            or snapshot.available_limit_minor != card.credit_limit_minor - expected_used
            or not 0 <= expected_used <= maximum_used
        ):
            raise InvariantViolation(
                f"Credit-limit snapshot {snapshot.snapshot_id} does not reconcile."
            )
