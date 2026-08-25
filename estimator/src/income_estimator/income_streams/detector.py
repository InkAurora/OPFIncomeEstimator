"""Group included transactions into reproducible description-based streams."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from hashlib import sha256
from statistics import fmean, median, pstdev

from income_estimator.contracts.audit import IncomeStream, TransactionDecision


def _frequency(dates: list[date]) -> str:
    if len(dates) < 2:
        return "ONE_OFF"
    gaps = [(right - left).days for left, right in zip(dates, dates[1:])]
    typical = median(gaps)
    if 5 <= typical <= 9:
        return "WEEKLY"
    if 12 <= typical <= 16:
        return "BIWEEKLY"
    if 25 <= typical <= 35:
        return "MONTHLY"
    if 75 <= typical <= 105:
        return "QUARTERLY"
    return "IRREGULAR"


# Payments per year for each inferred frequency. ADR 0006: a quarterly source of 9,000 supports
# 3,000 a month, and the detector previously reported the paying-month amount as if it were the
# monthly rate.
_PAYMENTS_PER_YEAR: dict[str, float] = {
    "WEEKLY": 52.0,
    "BIWEEKLY": 26.0,
    "MONTHLY": 12.0,
    "QUARTERLY": 4.0,
}


def _monthly_capacity(frequency: str, amounts: list[int], dates: list[date]) -> int:
    """Normalize a stream to the monthly rate it can sustain.

    A regular frequency converts by its own cadence. An irregular or one-off stream has no cadence
    to convert, so it is spread across the span it was actually observed over, which is the only
    defensible run rate available from the observations.
    """

    per_year = _PAYMENTS_PER_YEAR.get(frequency)
    if per_year is not None:
        return max(0, round(median(amounts) * per_year / 12))
    span_days = (dates[-1] - dates[0]).days
    months = max(1.0, (span_days + 30) / 30.44)
    return max(0, round(sum(amounts) / months))


def _frequency_confidence(dates: list[date], frequency: str) -> int:
    """How much the observed cadence supports the inferred frequency.

    A frequency read off two transactions is a guess; one read off twelve evenly spaced ones is
    close to a fact. The capacity model needs to tell those apart, because the monthly rate derived
    from a guessed cadence carries the guess with it.
    """

    if frequency in {"ONE_OFF", "IRREGULAR"} or len(dates) < 2:
        return 0
    gaps = [(right - left).days for left, right in zip(dates, dates[1:])]
    gap_mean = fmean(gaps)
    gap_cv = pstdev(gaps) / gap_mean if gap_mean else 0.0
    regularity = max(0.0, 1.0 - min(1.0, gap_cv))
    observations = min(1.0, len(dates) / 6)
    return min(10_000, round(10_000 * regularity * observations))


def _recurrence_score(dates: list[date], amounts: list[int]) -> int:
    if len(dates) < 2:
        return 2_000
    gaps = [(right - left).days for left, right in zip(dates, dates[1:])]
    gap_mean = fmean(gaps)
    gap_cv = pstdev(gaps) / gap_mean if gap_mean else 0.0
    amount_mean = fmean(amounts)
    amount_cv = pstdev(amounts) / amount_mean if amount_mean else 0.0
    count_score = min(4_000, len(dates) * 1_000)
    regularity_score = max(0, round(3_000 * (1 - min(1.0, gap_cv))))
    stability_score = max(0, round(3_000 * (1 - min(1.0, amount_cv))))
    return min(10_000, count_score + regularity_score + stability_score)


def _stream_pattern(
    frequency: str,
    recurrence_score: int,
    item_count: int,
    month_count: int,
) -> str:
    if (
        frequency in {"WEEKLY", "BIWEEKLY", "MONTHLY", "QUARTERLY"}
        and recurrence_score >= 7_000
        and item_count >= 3
        and month_count >= 3
    ):
        return "RECURRING_SOURCE"
    if item_count >= 4 and month_count >= 3:
        return "INCOME_ECOSYSTEM"
    return "ONE_OFF"


def detect_income_streams(
    decisions: tuple[TransactionDecision, ...],
    posted_at_by_id: dict[str, str],
    account_id_by_id: dict[str, str],
) -> tuple[IncomeStream, ...]:
    """Group classified income using normalized description as v1 cluster key."""

    grouped: dict[str, list[TransactionDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.classification == "INCOME":
            grouped[decision.counterparty_cluster].append(decision)

    streams: list[IncomeStream] = []
    for cluster, items in sorted(grouped.items()):
        ordered = sorted(
            items,
            key=lambda item: (
                posted_at_by_id[item.transaction_id],
                item.transaction_id,
            ),
        )
        dates = [date.fromisoformat(posted_at_by_id[item.transaction_id]) for item in ordered]
        amounts = [item.amount_minor for item in ordered]
        mean_amount = fmean(amounts)
        amount_cv = pstdev(amounts) / mean_amount if mean_amount else 0.0
        frequency = _frequency(dates)
        recurrence_score = _recurrence_score(dates, amounts)
        monthly_amounts: dict[str, int] = defaultdict(int)
        for item in ordered:
            monthly_amounts[item.posted_month] += item.amount_minor
        observed_months = tuple(sorted(monthly_amounts))
        identity = sha256(cluster.encode("utf-8")).hexdigest()[:16]
        streams.append(
            IncomeStream(
                stream_id=f"stream-{identity}",
                counterparty_cluster=cluster,
                first_seen=dates[0].isoformat(),
                last_seen=dates[-1].isoformat(),
                frequency=frequency,
                median_amount_minor=round(median(amounts)),
                amount_coefficient_of_variation=round(amount_cv, 8),
                recurrence_score_basis_points=recurrence_score,
                income_probability_basis_points=round(
                    fmean(item.income_probability_basis_points for item in ordered)
                ),
                pattern=_stream_pattern(
                    frequency,
                    recurrence_score,
                    len(ordered),
                    len(observed_months),
                ),
                expected_monthly_amount_minor=round(median(monthly_amounts.values())),
                monthly_capacity_minor=_monthly_capacity(frequency, amounts, dates),
                frequency_confidence_basis_points=_frequency_confidence(dates, frequency),
                observed_months=observed_months,
                account_ids=tuple(
                    sorted({account_id_by_id[item.transaction_id] for item in ordered})
                ),
                transaction_ids=tuple(item.transaction_id for item in ordered),
            )
        )
    return tuple(streams)


__all__ = ["detect_income_streams"]
