"""Monthly realized-income reconstruction from classified observed credits.

Until estimator `0.1.0` this module divided each account's observed income by a coverage ratio
built from ``eligible_record_count`` and ``observed_original_record_count``. Those counts were the
simulator's own record of what it had withheld; no receiver can produce them, and dividing by them
recovered hidden income exactly. The reconstruction now reports what was observed. A known fetch gap
is a reason to widen or withhold, never to multiply.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from income_estimator.consent_scope import fetch_gap_months_by_account
from income_estimator.contracts.audit import TransactionDecision
from income_estimator.contracts.v1 import (
    EstimatorInputV1,
    MonthlyIncomeEstimateV1,
)

OBSERVED_UNCERTAINTY_BASIS_POINTS = 500
FETCH_GAP_UNCERTAINTY_BASIS_POINTS = 2_500


def _month_sequence(window_start: str, count: int) -> tuple[str, ...]:
    start = date.fromisoformat(window_start)
    return tuple(
        f"{(start.year * 12 + start.month - 1 + index) // 12:04d}-"
        f"{(start.year * 12 + start.month - 1 + index) % 12 + 1:02d}"
        for index in range(count)
    )


def reconstruct_monthly_income(
    request: EstimatorInputV1,
    decisions: tuple[TransactionDecision, ...],
) -> tuple[MonthlyIncomeEstimateV1, ...]:
    """Sum selected credits per month; widen the interval where the receiver knows it has a gap."""

    transaction_by_id = {item.transaction_id: item for item in request.transactions}
    months = _month_sequence(request.window_start, request.months)
    gaps_by_account = fetch_gap_months_by_account(request, months)
    included_by_month_account: dict[tuple[str, str], list[TransactionDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.classification != "INCOME":
            continue
        account_id = transaction_by_id[decision.transaction_id].account_id
        included_by_month_account[(decision.posted_month, account_id)].append(decision)

    estimates: list[MonthlyIncomeEstimateV1] = []
    for month in months:
        estimate = 0
        contributors: list[str] = []
        for account in request.accounts:
            items = included_by_month_account.get((month, account.account_id), ())
            if not items:
                continue
            estimate += sum(item.amount_minor for item in items)
            contributors.extend(item.transaction_id for item in items)

        month_has_gap = any(month in gaps for gaps in gaps_by_account.values())
        uncertainty_basis_points = (
            FETCH_GAP_UNCERTAINTY_BASIS_POINTS
            if month_has_gap
            else OBSERVED_UNCERTAINTY_BASIS_POINTS
        )
        uncertainty = (estimate * uncertainty_basis_points + 5_000) // 10_000
        estimates.append(
            MonthlyIncomeEstimateV1(
                month=month,
                estimated_income_minor=estimate,
                confidence_lower_minor=max(0, estimate - uncertainty),
                confidence_upper_minor=estimate + uncertainty,
                contributing_transaction_ids=tuple(sorted(contributors)),
            )
        )
    return tuple(estimates)


__all__ = ["reconstruct_monthly_income"]
