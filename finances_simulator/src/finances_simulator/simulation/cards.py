"""Deterministic credit-card purchase, invoice, payment, and limit simulation."""

from dataclasses import dataclass
from datetime import date
from heapq import heappop, heappush
from uuid import UUID

from finances_simulator.config_v1 import ScenarioConfigV1
from finances_simulator.domain.accounts import Direction
from finances_simulator.domain.cards import (
    CardInstallment,
    CardInvoice,
    CardPurchase,
    CreditCard,
    CreditLimitSnapshot,
    InvoiceStatus,
)
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.ledger.effects import LedgerEffect, PostingPriority
from finances_simulator.simulation.primitives import (
    deterministic_id,
    month_end,
    month_start,
    scheduled_date,
)


class CardAuthorizationError(ValueError):
    """Raised when configured card behavior cannot reconcile."""


@dataclass(frozen=True, slots=True)
class CardSimulation:
    """Complete hidden card state plus account-settlement effects."""

    purchases: tuple[CardPurchase, ...]
    installments: tuple[CardInstallment, ...]
    invoices: tuple[CardInvoice, ...]
    limit_snapshots: tuple[CreditLimitSnapshot, ...]
    events: tuple[FinancialEvent, ...]
    payment_effects: tuple[LedgerEffect, ...]


def split_installments(amount_minor: int, installment_count: int) -> tuple[int, ...]:
    """Split an integer amount exactly, assigning remainder to earliest installments."""

    if amount_minor <= 0:
        raise ValueError("amount_minor must be positive")
    if installment_count <= 0:
        raise ValueError("installment_count must be positive")
    if amount_minor < installment_count:
        raise ValueError("amount_minor must be at least installment_count")
    base_amount, remainder = divmod(amount_minor, installment_count)
    return tuple(
        base_amount + (1 if index < remainder else 0) for index in range(installment_count)
    )


def statement_close_for_purchase(purchased_at: date, close_day: int) -> date:
    """Return statement close receiving a purchase; close-day purchases stay current."""

    purchase_month = purchased_at.replace(day=1)
    current_close = scheduled_date(purchase_month, close_day)
    if purchased_at <= current_close:
        return current_close
    return scheduled_date(month_start(purchase_month, 1), close_day)


def due_date_after_close(statement_close_date: date, due_day: int) -> date:
    """Return first configured due day strictly after a statement close."""

    close_month = statement_close_date.replace(day=1)
    candidate = scheduled_date(close_month, due_day)
    if candidate > statement_close_date:
        return candidate
    return scheduled_date(month_start(close_month, 1), due_day)


def simulate_cards(
    *,
    config: ScenarioConfigV1,
    cards_by_ref: dict[str, CreditCard],
    customer_id: str,
    start_date: date,
    end_date: date,
    months: int,
    namespace: UUID,
) -> CardSimulation:
    """Simulate accepted purchases, installments, statements, payments, and limits."""

    purchases_by_card: dict[str, list[CardPurchase]] = {
        card.card_id: [] for card in cards_by_ref.values()
    }
    installments_by_card: dict[str, list[CardInstallment]] = {
        card.card_id: [] for card in cards_by_ref.values()
    }
    used_limit_by_card = {card.card_id: 0 for card in cards_by_ref.values()}
    pending_releases_by_card: dict[str, list[tuple[date, str, int]]] = {
        card.card_id: [] for card in cards_by_ref.values()
    }
    purchase_events: list[FinancialEvent] = []

    candidates = []
    for rule in config.card_purchase_rules:
        if rule.start_month_index >= months:
            continue
        occurrences_in_window = min(
            rule.occurrences,
            (months - 1 - rule.start_month_index) // rule.interval_months + 1,
        )
        for occurrence_index in range(occurrences_in_window):
            month_index = rule.start_month_index + occurrence_index * rule.interval_months
            purchase_month = month_start(start_date, month_index)
            candidates.append(
                (
                    scheduled_date(purchase_month, rule.day_of_month),
                    rule.rule_id,
                    occurrence_index,
                    rule,
                )
            )

    for purchased_at, _, occurrence_index, rule in sorted(
        candidates, key=lambda item: (item[0], item[1], item[2])
    ):
        card = cards_by_ref[rule.card_ref]
        card_purchases = purchases_by_card[card.card_id]
        card_installments = installments_by_card[card.card_id]
        pending_releases = pending_releases_by_card[card.card_id]
        while pending_releases and pending_releases[0][0] <= purchased_at:
            _, _, released_amount = heappop(pending_releases)
            used_limit_by_card[card.card_id] -= released_amount
        used_before = used_limit_by_card[card.card_id]
        maximum_used = card.credit_limit_minor * card.maximum_utilization_basis_points // 10_000
        used_after = used_before + rule.amount_minor
        if used_after > maximum_used:
            continue

        purchase_id = deterministic_id(
            namespace,
            "card_transaction",
            f"{rule.card_ref}:{rule.rule_id}:{occurrence_index}",
        )
        event_id = deterministic_id(namespace, "event", f"card-purchase:{purchase_id}")
        purchase = CardPurchase(
            purchase_id=purchase_id,
            event_id=event_id,
            customer_id=customer_id,
            card_id=card.card_id,
            purchased_at=purchased_at,
            amount_minor=rule.amount_minor,
            currency=card.currency,
            merchant=rule.merchant,
            description=rule.description,
            installment_count=rule.installment_count,
            rule_id=rule.rule_id,
            occurrence_index=occurrence_index,
            used_limit_after_purchase_minor=used_after,
        )
        card_purchases.append(purchase)
        used_limit_by_card[card.card_id] = used_after
        purchase_events.append(
            FinancialEvent(
                event_id=event_id,
                customer_id=customer_id,
                occurred_at=purchased_at,
                economic_type=EconomicType.EXPENSE,
                amount_minor=rule.amount_minor,
                currency=card.currency,
                source_entity=card.card_id,
                destination_entity=rule.merchant,
                description=rule.description,
                metadata={
                    "expense_kind": "CARD_PURCHASE",
                    "purchase_id": purchase_id,
                    "rule_id": rule.rule_id,
                    "installment_count": rule.installment_count,
                },
            )
        )

        first_close = statement_close_for_purchase(purchased_at, card.statement_close_day)
        for installment_index, installment_amount in enumerate(
            split_installments(rule.amount_minor, rule.installment_count)
        ):
            statement_month = month_start(first_close.replace(day=1), installment_index)
            statement_close = scheduled_date(statement_month, card.statement_close_day)
            due_date = due_date_after_close(statement_close, card.payment_due_day)
            invoice_id = deterministic_id(
                namespace,
                "invoice",
                f"{card.card_id}:{statement_close.isoformat()}",
            )
            installment_number = installment_index + 1
            invoice_item_id = deterministic_id(
                namespace,
                "invoice_item",
                f"{purchase_id}:{installment_number}",
            )
            card_installments.append(
                CardInstallment(
                    invoice_item_id=invoice_item_id,
                    purchase_id=purchase_id,
                    card_id=card.card_id,
                    invoice_id=invoice_id,
                    statement_close_date=statement_close,
                    due_date=due_date,
                    installment_number=installment_number,
                    installment_count=rule.installment_count,
                    amount_minor=installment_amount,
                    description=rule.description,
                )
            )
            heappush(
                pending_releases,
                (due_date, invoice_item_id, installment_amount),
            )

    invoices: list[CardInvoice] = []
    payment_events: list[FinancialEvent] = []
    payment_effects: list[LedgerEffect] = []
    for card in sorted(cards_by_ref.values(), key=lambda item: item.card_id):
        installments = installments_by_card[card.card_id]
        installments_by_close: dict[date, list[CardInstallment]] = {}
        for installment in installments:
            if installment.statement_close_date <= end_date:
                installments_by_close.setdefault(installment.statement_close_date, []).append(
                    installment
                )

        for statement_close, invoice_installments in sorted(installments_by_close.items()):
            invoice_id = invoice_installments[0].invoice_id
            due_date = due_date_after_close(statement_close, card.payment_due_day)
            amount_due = sum(item.amount_minor for item in invoice_installments)
            paid = due_date <= end_date
            payment_event_id = (
                deterministic_id(namespace, "event", f"card-payment:{invoice_id}") if paid else None
            )
            invoices.append(
                CardInvoice(
                    invoice_id=invoice_id,
                    customer_id=customer_id,
                    card_id=card.card_id,
                    statement_close_date=statement_close,
                    due_date=due_date,
                    amount_due_minor=amount_due,
                    paid_amount_minor=amount_due if paid else 0,
                    status=InvoiceStatus.PAID if paid else InvoiceStatus.CLOSED,
                    paid_at=due_date if paid else None,
                    payment_event_id=payment_event_id,
                    installment_ids=tuple(
                        item.invoice_item_id
                        for item in sorted(
                            invoice_installments,
                            key=lambda item: (item.purchase_id, item.installment_number),
                        )
                    ),
                )
            )
            if not paid or payment_event_id is None:
                continue
            payment_events.append(
                FinancialEvent(
                    event_id=payment_event_id,
                    customer_id=customer_id,
                    occurred_at=due_date,
                    economic_type=EconomicType.CARD_PAYMENT,
                    amount_minor=amount_due,
                    currency=card.currency,
                    source_entity=card.payment_account_id,
                    destination_entity=card.card_id,
                    description=card.payment_description,
                    metadata={"invoice_id": invoice_id, "card_id": card.card_id},
                )
            )
            payment_effects.append(
                LedgerEffect(
                    event_id=payment_event_id,
                    account_id=card.payment_account_id,
                    posted_at=due_date,
                    direction=Direction.DEBIT,
                    amount_minor=amount_due,
                    posting_priority=PostingPriority.CARD_PAYMENT,
                    entry_key=f"card-payment:{invoice_id}",
                    description=card.payment_description,
                )
            )

    reference_dates = tuple(
        month_end(month_start(start_date, month_index)) for month_index in range(months)
    )
    used_limit_by_reference: dict[tuple[str, date], int] = {}
    for card in sorted(cards_by_ref.values(), key=lambda item: item.card_id):
        card_purchases = sorted(
            purchases_by_card[card.card_id],
            key=lambda item: (item.purchased_at, item.rule_id, item.occurrence_index),
        )
        card_installments = sorted(
            installments_by_card[card.card_id],
            key=lambda item: (item.due_date, item.invoice_item_id),
        )
        purchase_index = 0
        installment_index = 0
        used_limit = 0
        for reference_date in reference_dates:
            while (
                purchase_index < len(card_purchases)
                and card_purchases[purchase_index].purchased_at <= reference_date
            ):
                used_limit += card_purchases[purchase_index].amount_minor
                purchase_index += 1
            while (
                installment_index < len(card_installments)
                and card_installments[installment_index].due_date <= reference_date
            ):
                used_limit -= card_installments[installment_index].amount_minor
                installment_index += 1
            used_limit_by_reference[(card.card_id, reference_date)] = used_limit

    snapshots: list[CreditLimitSnapshot] = []
    for reference_date in reference_dates:
        for card in sorted(cards_by_ref.values(), key=lambda item: item.card_id):
            used_limit = used_limit_by_reference[(card.card_id, reference_date)]
            if not 0 <= used_limit <= card.credit_limit_minor:
                raise CardAuthorizationError(
                    f"Card {card.card_id} has invalid used limit {used_limit}."
                )
            snapshots.append(
                CreditLimitSnapshot(
                    snapshot_id=deterministic_id(
                        namespace,
                        "credit_limit",
                        f"{card.card_id}:{reference_date.isoformat()}",
                    ),
                    customer_id=customer_id,
                    card_id=card.card_id,
                    reference_date=reference_date,
                    total_limit_minor=card.credit_limit_minor,
                    used_limit_minor=used_limit,
                    available_limit_minor=card.credit_limit_minor - used_limit,
                    currency=card.currency,
                )
            )

    all_purchases = tuple(
        sorted(
            (purchase for items in purchases_by_card.values() for purchase in items),
            key=lambda item: (
                item.purchased_at,
                item.rule_id,
                item.occurrence_index,
            ),
        )
    )
    all_installments = tuple(
        sorted(
            (item for items in installments_by_card.values() for item in items),
            key=lambda item: (item.statement_close_date, item.card_id, item.invoice_item_id),
        )
    )
    return CardSimulation(
        purchases=all_purchases,
        installments=all_installments,
        invoices=tuple(
            sorted(invoices, key=lambda item: (item.statement_close_date, item.card_id))
        ),
        limit_snapshots=tuple(
            sorted(snapshots, key=lambda item: (item.reference_date, item.card_id))
        ),
        events=tuple(
            sorted(
                (*purchase_events, *payment_events),
                key=lambda event: (event.occurred_at, event.event_id),
            )
        ),
        payment_effects=tuple(
            sorted(
                payment_effects,
                key=lambda effect: (effect.posted_at, effect.event_id, effect.entry_key),
            )
        ),
    )


__all__ = [
    "CardAuthorizationError",
    "CardSimulation",
    "due_date_after_close",
    "simulate_cards",
    "split_installments",
    "statement_close_for_purchase",
]
