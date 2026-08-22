"""Reference-month cutoffs and observation slicing.

A customer-month row must never contain information that arrived after its reference month. Rather
than filtering derived values, the request itself is narrowed to the records observable at the
cutoff and the whole deterministic pipeline is replayed on that narrowed request. Point-in-time
safety is therefore a property of the input, not of individual feature formulas.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from income_estimator.contracts.v1 import EstimatorInputV1
from income_estimator.models.cashflow import _month_sequence


def month_index(month: str) -> int:
    """Map ``YYYY-MM`` to a monotonic month number."""

    return int(month[:4]) * 12 + int(month[5:7]) - 1


def month_of(day: str) -> str:
    return day[:7]


def month_end_date(month: str) -> date:
    year = int(month[:4])
    number = int(month[5:7])
    return date(year, number, monthrange(year, number)[1])


def reference_months(request: EstimatorInputV1) -> tuple[str, ...]:
    """Return every requested calendar month in ascending order."""

    return _month_sequence(request.window_start, request.months)


def cutoff_date(request: EstimatorInputV1, reference_month: str) -> date:
    """Clip the reference month end to the consented observation window."""

    window_end = date.fromisoformat(request.window_end)
    return min(month_end_date(reference_month), window_end)


PRODUCT_DATE_FIELDS = (
    ("balances", "reference_date"),
    ("credit_cards", "opened_on"),
    ("credit_limits", "reference_date"),
    ("card_transactions", "occurred_at"),
    ("card_invoices", "statement_close_date"),
    ("loan_payments", "due_date"),
    ("loan_balances", "reference_date"),
    ("investments", "opened_on"),
    ("investment_balances", "reference_date"),
)


def slice_request(request: EstimatorInputV1, cutoff: date) -> EstimatorInputV1:
    """Narrow a validated request to records observable at ``cutoff``.

    Transactions keep only those observed at or before the cutoff, every product collection keeps
    only records whose provider-visible date has passed, and the window shrinks so downstream code
    treats the cutoff as the end of history. Accounts, coverage, loan links, and investment
    transaction links are consent metadata without observation dates; they are retained but can
    only ever exclude a visible transaction.
    """

    cutoff_iso = cutoff.isoformat()
    if cutoff_iso < request.window_start:
        raise ValueError("cutoff must not precede window_start")

    months = month_index(month_of(cutoff_iso)) - month_index(month_of(request.window_start)) + 1
    update: dict[str, object] = {
        "window_end": cutoff_iso,
        "months": months,
        "transactions": tuple(
            item for item in request.transactions if item.observed_at <= cutoff_iso
        ),
    }
    for collection_name, date_field in PRODUCT_DATE_FIELDS:
        records = getattr(request, collection_name, None)
        if records is None:
            continue
        update[collection_name] = tuple(
            item for item in records if getattr(item, date_field) <= cutoff_iso
        )
    return request.model_copy(update=update)


__all__ = [
    "cutoff_date",
    "month_end_date",
    "month_index",
    "month_of",
    "reference_months",
    "slice_request",
]
