"""Gap-aware reconstruction from stable observed income streams.

A stable stream that skips a month has either stopped or was not fetched. The two are told apart
by the receiver's own consent scope: a month the receiver did not fetch for the stream's account is
a gap and may be filled from the stream's established cadence; a month it did fetch and found
nothing in is a non-payment and stays empty. Before contract 1.3 this module read a provider
coverage ratio the simulator had computed from withheld records; it no longer reads anything a
receiver could not have written.
"""

from __future__ import annotations

from collections import defaultdict

from income_estimator.consent_scope import fetch_gap_months_by_account
from income_estimator.contracts.audit import (
    IncomeStream,
    MonthlyReconstructionAudit,
    TransactionDecision,
)
from income_estimator.contracts.v1 import EstimatorInputV1, MonthlyIncomeEstimateV1
from income_estimator.models.cashflow import _month_sequence

FETCH_GAP_IMPUTATION_UNCERTAINTY_BASIS_POINTS = 2_500


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


def _month_index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:])


# Months between consecutive payments, for each cadence the detector can actually establish.
_CADENCE_MONTH_STEP: dict[str, int] = {
    "WEEKLY": 1,
    "BIWEEKLY": 1,
    "MONTHLY": 1,
    "QUARTERLY": 3,
}


def _due_months(stream: IncomeStream, active_months: set[str]) -> set[str]:
    """Months inside the active span where this stream's cadence expected a payment.

    Every unobserved month in the span used to count as a missing payment, whatever the cadence. A
    quarterly source paying R$9,000 in January, April and July was therefore reported as paying
    R$9,000 in all seven months, turning R$27,000 observed into R$63,000 reconstructed. A month is a
    gap only if a payment was due in it.

    Phase is read from the months that were actually observed. Quarterly payments that do not agree
    on one phase leave the due months unknown, and a cadence the detector could not establish at all
    -- irregular, or one-off -- states nothing about when the next payment was due. Both withhold
    imputation rather than fall back on a monthly rhythm nothing measured.
    """

    step = _CADENCE_MONTH_STEP.get(stream.frequency)
    if step is None:
        return set()
    if step == 1:
        return set(active_months)
    phases = {_month_index(month) % step for month in stream.observed_months}
    if len(phases) != 1:
        return set()
    phase = next(iter(phases))
    return {month for month in active_months if _month_index(month) % step == phase}


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
    """Use observed amounts directly; fill due months the receiver knows it did not fetch."""

    months = _month_sequence(request.window_start, request.months)
    posted_month_by_id = {
        item.transaction_id: item.posted_at[:7] for item in request.transactions
    }
    gaps_by_account = fetch_gap_months_by_account(request, months)
    included_by_month: dict[str, list[TransactionDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.classification == "INCOME":
            included_by_month[decision.posted_month].append(decision)

    eligible_streams: list[tuple[IncomeStream, set[str]]] = []
    withheld_months: set[str] = set()
    for stream in streams:
        # A stream may be filled only into months the receiver did not fetch for one of the
        # accounts it pays into. An account without a declared scope has no known gaps.
        stream_gaps: set[str] = set()
        for account_id in stream.account_ids:
            stream_gaps.update(gaps_by_account.get(account_id, frozenset()))
        if (
            stream.pattern in {"RECURRING_SOURCE", "INCOME_ECOSYSTEM"}
            and stream.recurrence_score_basis_points >= 7_000
            and len(stream.observed_months) >= 3
            and stream_gaps
        ):
            active = _active_months(stream, months, has_incomplete_coverage=True)
            due = _due_months(stream, active) & stream_gaps
            eligible_streams.append((stream, due))
            # A month inside an eligible stream's span that its cadence does not call due is a
            # non-payment month, not a hidden payment. Recording it keeps the difference between
            # "nothing was due" and "nothing was found" visible to a reviewer.
            withheld_months.update(
                month
                for month in active - due
                if month not in stream.observed_months
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

        for stream, due_months in eligible_streams:
            if month not in due_months or month in stream.observed_months:
                continue
            imputed += stream.expected_monthly_amount_minor
            imputed_stream_ids.append(stream.stream_id)
            contributors.update(_supporting_ids(stream, month, posted_month_by_id))
            imputation_uncertainty_basis_points.append(
                max(
                    FETCH_GAP_IMPUTATION_UNCERTAINTY_BASIS_POINTS,
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
        if month in withheld_months:
            reason_codes.append("RECURRING_STREAM_NOT_DUE_THIS_MONTH")
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
