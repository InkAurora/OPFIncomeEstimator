"""Coverage-aware reconstruction from stable observed income streams."""

from __future__ import annotations

from collections import defaultdict

from income_estimator.contracts.audit import (
    IncomeStream,
    MonthlyReconstructionAudit,
    TransactionDecision,
)
from income_estimator.contracts.v1 import EstimatorInputV1, MonthlyIncomeEstimateV1
from income_estimator.models.cashflow import _coverage_by_account, _month_sequence


def _active_months(
    stream: IncomeStream,
    months: tuple[str, ...],
    has_incomplete_coverage: bool,
) -> set[str]:
    """Bound inferred activity to observed span plus at most one missing edge month."""

    indexes = [months.index(month) for month in stream.observed_months if month in months]
    if not indexes:
        return set()
    first = min(indexes)
    last = max(indexes)
    if has_incomplete_coverage and first == 1:
        first = 0
    if has_incomplete_coverage and last == len(months) - 2:
        last = len(months) - 1
    return set(months[first : last + 1])


def _supporting_ids(
    stream: IncomeStream,
    month: str,
    posted_month_by_id: dict[str, str],
) -> tuple[str, ...]:
    target = int(month[:4]) * 12 + int(month[5:])

    def distance(transaction_id: str) -> tuple[int, str]:
        posted_month = posted_month_by_id[transaction_id]
        source = int(posted_month[:4]) * 12 + int(posted_month[5:])
        return abs(source - target), transaction_id

    return tuple(sorted(stream.transaction_ids, key=distance)[:3])


def reconstruct_recurring_income(
    request: EstimatorInputV1,
    decisions: tuple[TransactionDecision, ...],
    streams: tuple[IncomeStream, ...],
) -> tuple[
    tuple[MonthlyIncomeEstimateV1, ...],
    tuple[MonthlyReconstructionAudit, ...],
]:
    """Use observed amounts directly; fill evidence-backed gaps under incomplete coverage."""

    months = _month_sequence(request.window_start, request.months)
    posted_month_by_id = {
        item.transaction_id: item.posted_at[:7] for item in request.transactions
    }
    coverage_by_account = _coverage_by_account(request)
    included_by_month: dict[str, list[TransactionDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.classification == "INCOME":
            included_by_month[decision.posted_month].append(decision)

    eligible_streams: list[tuple[IncomeStream, set[str], int]] = []
    for stream in streams:
        stream_coverage = min(
            (coverage_by_account.get(account_id, 10_000) for account_id in stream.account_ids),
            default=10_000,
        )
        if (
            stream.pattern in {"RECURRING_SOURCE", "INCOME_ECOSYSTEM"}
            and stream.recurrence_score_basis_points >= 7_000
            and len(stream.observed_months) >= 3
            and stream_coverage < 10_000
        ):
            eligible_streams.append(
                (
                    stream,
                    _active_months(stream, months, has_incomplete_coverage=True),
                    stream_coverage,
                )
            )

    estimates: list[MonthlyIncomeEstimateV1] = []
    audits: list[MonthlyReconstructionAudit] = []
    for month in months:
        observed_items = included_by_month.get(month, ())
        observed = sum(item.amount_minor for item in observed_items)
        contributors = {item.transaction_id for item in observed_items}
        imputed = 0
        imputed_stream_ids: list[str] = []
        imputation_uncertainty_basis_points: list[int] = []

        for stream, active_months, stream_coverage in eligible_streams:
            if month not in active_months or month in stream.observed_months:
                continue
            imputed += stream.expected_monthly_amount_minor
            imputed_stream_ids.append(stream.stream_id)
            contributors.update(_supporting_ids(stream, month, posted_month_by_id))
            imputation_uncertainty_basis_points.append(
                max(
                    1_000,
                    10_000 - stream_coverage,
                    round(stream.amount_coefficient_of_variation * 10_000),
                )
            )

        estimate = observed + imputed
        if imputation_uncertainty_basis_points:
            uncertainty_basis_points = max(imputation_uncertainty_basis_points)
        elif estimate:
            uncertainty_basis_points = 500
        else:
            uncertainty_basis_points = 0
        uncertainty = (estimate * uncertainty_basis_points + 5_000) // 10_000
        contributor_ids = tuple(sorted(contributors))
        reason_codes: list[str] = []
        if observed:
            reason_codes.append("OBSERVED_INCOME")
        if imputed:
            reason_codes.append("RECURRING_STREAM_GAP_IMPUTED")
        if not reason_codes:
            reason_codes.append("NO_INCOME_EVIDENCE")

        estimates.append(
            MonthlyIncomeEstimateV1(
                month=month,
                estimated_income_minor=estimate,
                confidence_lower_minor=max(0, estimate - uncertainty),
                confidence_upper_minor=estimate + uncertainty,
                contributing_transaction_ids=contributor_ids,
            )
        )
        audits.append(
            MonthlyReconstructionAudit(
                month=month,
                observed_income_minor=observed,
                imputed_income_minor=imputed,
                coverage_adjustment_minor=0,
                estimated_income_minor=estimate,
                imputed_stream_ids=tuple(sorted(imputed_stream_ids)),
                contributing_transaction_ids=contributor_ids,
                reason_codes=tuple(reason_codes),
            )
        )

    return tuple(estimates), tuple(audits)


__all__ = ["reconstruct_recurring_income"]
