"""Income-source structure features from detected streams."""

from __future__ import annotations

from statistics import fmean

from income_estimator.contracts.audit import IncomeStream
from income_estimator.features.monthly import PointInTimeView
from income_estimator.features.outcomes import (
    FeatureOutcome,
    missing,
    present,
    round_basis_points,
    round_ratio,
)
from income_estimator.features.schema import (
    MISSING_NO_OBSERVED_RECORDS,
    MISSING_UNDEFINED_DENOMINATOR,
)


def _stream_amount_in_window(
    view: PointInTimeView,
    stream: IncomeStream,
    window: int,
) -> int:
    total = 0
    for transaction_id in stream.transaction_ids:
        decision = view.decision_by_id.get(transaction_id)
        if decision is not None and view.in_trailing_window(decision.posted_month, window):
            total += decision.amount_minor
    return total


def _last_activity_month(view: PointInTimeView, stream: IncomeStream) -> str | None:
    months = [
        view.decision_by_id[transaction_id].posted_month
        for transaction_id in stream.transaction_ids
        if transaction_id in view.decision_by_id
    ]
    return max(months) if months else None


def source_features(view: PointInTimeView) -> dict[str, FeatureOutcome]:
    """Summarize how many sources pay the customer and how concentrated they are."""

    streams = view.audit.income_streams
    amounts = {
        stream.stream_id: _stream_amount_in_window(view, stream, 12) for stream in streams
    }
    active = tuple(stream for stream in streams if amounts[stream.stream_id] > 0)
    active_amounts = [amounts[stream.stream_id] for stream in active]
    total = sum(active_amounts)

    result: dict[str, FeatureOutcome] = {
        "source_count_12m": present(len(active)),
        "recurring_source_count_12m": present(
            sum(1 for stream in active if stream.pattern == "RECURRING_SOURCE")
        ),
        "ecosystem_source_count_12m": present(
            sum(1 for stream in active if stream.pattern == "INCOME_ECOSYSTEM")
        ),
        "active_source_count_3m": present(
            sum(1 for stream in streams if _stream_amount_in_window(view, stream, 3) > 0)
        ),
        "source_income_12m_minor": present(total),
    }

    if not active:
        result["largest_source_share_12m"] = missing(MISSING_NO_OBSERVED_RECORDS)
        result["source_concentration_hhi_12m"] = missing(MISSING_NO_OBSERVED_RECORDS)
        result["recurrence_score_mean_12m_basis_points"] = missing(MISSING_NO_OBSERVED_RECORDS)
        result["recurrence_score_max_12m_basis_points"] = missing(MISSING_NO_OBSERVED_RECORDS)
        result["source_amount_cv_mean_12m"] = missing(MISSING_NO_OBSERVED_RECORDS)
    elif total == 0:
        result["largest_source_share_12m"] = missing(MISSING_UNDEFINED_DENOMINATOR)
        result["source_concentration_hhi_12m"] = missing(MISSING_UNDEFINED_DENOMINATOR)
        result["recurrence_score_mean_12m_basis_points"] = missing(MISSING_UNDEFINED_DENOMINATOR)
        result["recurrence_score_max_12m_basis_points"] = missing(MISSING_UNDEFINED_DENOMINATOR)
        result["source_amount_cv_mean_12m"] = missing(MISSING_UNDEFINED_DENOMINATOR)
    else:
        shares = [amount / total for amount in active_amounts]
        result["largest_source_share_12m"] = present(round_ratio(max(shares)))
        result["source_concentration_hhi_12m"] = present(
            round_ratio(sum(share * share for share in shares))
        )
        result["recurrence_score_mean_12m_basis_points"] = present(
            round_basis_points(
                fmean(stream.recurrence_score_basis_points for stream in active)
            )
        )
        result["recurrence_score_max_12m_basis_points"] = present(
            max(stream.recurrence_score_basis_points for stream in active)
        )
        result["source_amount_cv_mean_12m"] = present(
            round_ratio(fmean(stream.amount_coefficient_of_variation for stream in active))
        )

    last_months = [
        month
        for month in (_last_activity_month(view, stream) for stream in streams)
        if month is not None
    ]
    result["months_since_last_source_activity"] = (
        present(view.months_since(max(last_months)))
        if last_months
        else missing(MISSING_NO_OBSERVED_RECORDS)
    )
    return result


__all__ = ["source_features"]
