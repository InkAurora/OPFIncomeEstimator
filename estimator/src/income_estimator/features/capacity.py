"""Card, credit, and investment capacity features from estimator input 1.2.

Contract `1.1` carried no product data, so these features could only be declared and reported as
unavailable. Contract `1.2` adds cards, limits, card transactions, invoices, loan payments, loan
balances, investments, and investment balances, and each feature here is computed from them.

Two kinds of absence stay distinct. A request on contract `1.0` or `1.1` cannot express the domain
at all and reports `CONTRACT_DOMAIN_UNAVAILABLE`. A request on `1.2` that carries no such record by
the cutoff reports `NO_OBSERVED_RECORDS`, because a consent scope that excludes cards is
indistinguishable from a customer who holds none. Neither is reported as zero.
"""

from __future__ import annotations

from income_estimator.features.monthly import PointInTimeView
from income_estimator.features.outcomes import (
    FeatureOutcome,
    missing,
    present,
    round_minor,
    round_ratio,
)
from income_estimator.features.point_in_time import month_index, month_of
from income_estimator.features.schema import (
    MISSING_CONTRACT_DOMAIN_UNAVAILABLE,
    MISSING_NO_OBSERVED_RECORDS,
)

CAPACITY_FEATURE_NAMES = (
    "card_spend_3m_minor",
    "credit_utilization_ratio",
    "installment_commitment_minor",
    "monthly_debt_payment_minor",
    "outstanding_debt_minor",
    "investment_balance_minor",
)


def supports_products(view: PointInTimeView) -> bool:
    """Contract 1.2 exposes the product collections; earlier contracts cannot."""

    return getattr(view.request, "credit_cards", None) is not None


def _latest_by_key(records, key_field: str, date_field: str, id_field: str) -> dict[str, object]:
    latest: dict[str, tuple[str, str, object]] = {}
    for record in records:
        key = getattr(record, key_field)
        stamp = (getattr(record, date_field), getattr(record, id_field))
        current = latest.get(key)
        if current is None or stamp > (current[0], current[1]):
            latest[key] = (*stamp, record)
    return {key: value[2] for key, value in latest.items()}


def _card_spend(view: PointInTimeView) -> FeatureOutcome:
    transactions = view.request.card_transactions
    if not view.request.credit_cards:
        return missing(MISSING_NO_OBSERVED_RECORDS)
    return present(
        sum(
            item.amount_minor
            for item in transactions
            if view.in_trailing_window(month_of(item.occurred_at), 3)
        )
    )


def _credit_utilization(view: PointInTimeView) -> FeatureOutcome:
    latest = _latest_by_key(
        view.request.credit_limits,
        "card_id",
        "reference_date",
        "credit_limit_id",
    )
    if not latest:
        return missing(MISSING_NO_OBSERVED_RECORDS)
    total = sum(item.total_limit_minor for item in latest.values())
    used = sum(item.used_limit_minor for item in latest.values())
    return present(round_ratio(used / total))


def _installment_commitment(view: PointInTimeView) -> FeatureOutcome:
    """Sum the part of each installment purchase that is not yet billed.

    Contract 1.2 does not expose a card's statement close day, so billing is taken to advance one
    installment per calendar month starting in the purchase month. The result is the committed
    remainder of observed installment purchases, not a statement-exact figure.
    """

    if not view.request.credit_cards:
        return missing(MISSING_NO_OBSERVED_RECORDS)
    reference = month_index(view.reference_month)
    committed = 0
    for item in view.request.card_transactions:
        if item.installment_count <= 1:
            continue
        billed = reference - month_index(month_of(item.occurred_at)) + 1
        remaining = max(0, item.installment_count - billed)
        if remaining:
            committed += round_minor(item.amount_minor * remaining / item.installment_count)
    return present(committed)


def _monthly_debt_payment(view: PointInTimeView) -> FeatureOutcome:
    payments = view.request.loan_payments
    if not payments:
        return missing(MISSING_NO_OBSERVED_RECORDS)
    return present(
        sum(
            item.total_amount_minor
            for item in payments
            if view.in_trailing_window(month_of(item.due_date), 1)
        )
    )


def _outstanding_debt(view: PointInTimeView) -> FeatureOutcome:
    """Combine remaining loan principal with the outstanding card balance."""

    loans = _latest_by_key(
        view.request.loan_balances,
        "loan_id",
        "reference_date",
        "loan_balance_id",
    )
    cards = _latest_by_key(
        view.request.credit_limits,
        "card_id",
        "reference_date",
        "credit_limit_id",
    )
    if not loans and not cards:
        return missing(MISSING_NO_OBSERVED_RECORDS)
    return present(
        sum(item.remaining_principal_minor for item in loans.values())
        + sum(item.used_limit_minor for item in cards.values())
    )


def _investment_balance(view: PointInTimeView) -> FeatureOutcome:
    latest = _latest_by_key(
        view.request.investment_balances,
        "investment_id",
        "reference_date",
        "investment_balance_id",
    )
    if not latest:
        return missing(MISSING_NO_OBSERVED_RECORDS)
    return present(sum(item.balance_minor for item in latest.values()))


def capacity_features(view: PointInTimeView) -> dict[str, FeatureOutcome]:
    """Return card, credit, and investment capacity, or why each is unavailable."""

    if not supports_products(view):
        return {
            name: missing(MISSING_CONTRACT_DOMAIN_UNAVAILABLE)
            for name in CAPACITY_FEATURE_NAMES
        }
    return {
        "card_spend_3m_minor": _card_spend(view),
        "credit_utilization_ratio": _credit_utilization(view),
        "installment_commitment_minor": _installment_commitment(view),
        "monthly_debt_payment_minor": _monthly_debt_payment(view),
        "outstanding_debt_minor": _outstanding_debt(view),
        "investment_balance_minor": _investment_balance(view),
    }


__all__ = ["CAPACITY_FEATURE_NAMES", "capacity_features", "supports_products"]
