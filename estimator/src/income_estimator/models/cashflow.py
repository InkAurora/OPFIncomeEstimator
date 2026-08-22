"""Monthly realized-income reconstruction from classified observed credits."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from income_estimator.contracts.audit import TransactionDecision
from income_estimator.contracts.v1 import (
    EstimatorInputV1,
    MonthlyIncomeEstimateV1,
)


def _month_sequence(window_start: str, count: int) -> tuple[str, ...]:
    start = date.fromisoformat(window_start)
    return tuple(
        f"{(start.year * 12 + start.month - 1 + index) // 12:04d}-"
        f"{(start.year * 12 + start.month - 1 + index) % 12 + 1:02d}"
        for index in range(count)
    )


def _coverage_by_account(request: EstimatorInputV1) -> dict[str, int]:
    result: dict[str, int] = {}
    for coverage in request.coverage:
        if coverage.eligible_record_count:
            basis_points = (
                coverage.observed_original_record_count * 10_000
                + coverage.eligible_record_count // 2
            ) // coverage.eligible_record_count
        else:
            basis_points = coverage.effective_coverage_basis_points
        result[coverage.account_id] = min(10_000, max(1, basis_points))
    return result


def reconstruct_monthly_income(
    request: EstimatorInputV1,
    decisions: tuple[TransactionDecision, ...],
) -> tuple[MonthlyIncomeEstimateV1, ...]:
    """Sum selected credits and adjust each account for measured observation coverage."""

    transaction_by_id = {item.transaction_id: item for item in request.transactions}
    coverage_by_account = _coverage_by_account(request)
    included_by_month_account: dict[tuple[str, str], list[TransactionDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.classification != "INCOME":
            continue
        account_id = transaction_by_id[decision.transaction_id].account_id
        included_by_month_account[(decision.posted_month, account_id)].append(decision)

    estimates: list[MonthlyIncomeEstimateV1] = []
    for month in _month_sequence(request.window_start, request.months):
        estimate = 0
        contributors: list[str] = []
        used_coverages: list[int] = []
        for account in request.accounts:
            items = included_by_month_account.get((month, account.account_id), ())
            if not items:
                continue
            observed = sum(item.amount_minor for item in items)
            coverage = coverage_by_account.get(account.account_id, 10_000)
            estimate += (observed * 10_000 + coverage // 2) // coverage
            used_coverages.append(coverage)
            contributors.extend(item.transaction_id for item in items)

        effective_coverage = min(used_coverages, default=10_000)
        uncertainty_basis_points = max(500, 10_000 - effective_coverage)
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
