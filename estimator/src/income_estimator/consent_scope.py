"""Receiver-knowable observation gaps derived from consent scope.

Every reader of coverage information in the estimator goes through this module. It answers one
question per account and month: did the receiver fetch that month? A month is fetched when the
declared range covers it from its first day through its last day (or the window end, whichever is
earlier) and pagination completed. Anything else is a gap the receiver itself created and can
therefore account for.

Requests older than contract 1.3 carry no consent scope. They report no gaps: the pre-1.3 coverage
record is never read, because its counts came from records the receiver never saw.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable
from typing import Protocol


class _ConsentScope(Protocol):
    account_id: str
    fetched_from: str
    fetched_through: str
    pagination_complete: bool


class _ScopedRequest(Protocol):
    window_end: str
    consent_scopes: tuple[_ConsentScope, ...]


def _month_bounds(month: str, window_end: str) -> tuple[str, str]:
    year = int(month[:4])
    number = int(month[5:7])
    first = f"{month}-01"
    last = f"{month}-{monthrange(year, number)[1]:02d}"
    return first, min(last, window_end)


def consent_scopes(request: object) -> tuple[_ConsentScope, ...]:
    """Consent scopes on the request, or none for contracts that predate them."""

    scopes = getattr(request, "consent_scopes", None)
    return tuple(scopes) if scopes else ()


def fetch_gap_months_by_account(
    request: object,
    months: Iterable[str],
) -> dict[str, frozenset[str]]:
    """Months each consented account was not fully fetched for, keyed by account."""

    scopes = consent_scopes(request)
    if not scopes:
        return {}
    window_end = str(getattr(request, "window_end"))
    month_list = tuple(months)
    result: dict[str, frozenset[str]] = {}
    for scope in scopes:
        gaps: set[str] = set()
        for month in month_list:
            first, last = _month_bounds(month, window_end)
            fetched = (
                scope.pagination_complete
                and scope.fetched_from <= first
                and scope.fetched_through >= last
            )
            if not fetched:
                gaps.add(month)
        result[scope.account_id] = frozenset(gaps)
    return result


def fetched_window_basis_points(request: object, months: Iterable[str]) -> int | None:
    """Share of account-months inside the window the receiver fetched, or None pre-1.3."""

    month_list = tuple(months)
    gaps = fetch_gap_months_by_account(request, month_list)
    if not gaps:
        return None
    total = len(month_list) * len(gaps)
    if not total:
        return None
    fetched = total - sum(len(item) for item in gaps.values())
    return (fetched * 10_000 + total // 2) // total


__all__ = [
    "consent_scopes",
    "fetch_gap_months_by_account",
    "fetched_window_basis_points",
]
