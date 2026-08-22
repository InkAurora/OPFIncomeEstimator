"""Observed-only transaction feature extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import fmean, pstdev

from income_estimator.contracts.v1 import EstimatorInputV1
from income_estimator.preprocessing import NormalizedTransaction, normalize_transactions

FEATURE_VERSION = "transaction-features-1.0.0"


@dataclass(frozen=True, slots=True)
class TransactionFeatures:
    transaction: NormalizedTransaction
    is_known_loan_disbursement: bool
    is_known_investment_redemption: bool
    has_visible_own_transfer_pair: bool
    is_reversed_original: bool
    prior_same_counterparty_count: int
    prior_same_counterparty_count_90d: int
    prior_same_amount_count: int
    days_since_prior_observation: int | None
    prior_amount_mean_minor: int | None
    prior_amount_coefficient_of_variation: float | None


def extract_transaction_features(request: EstimatorInputV1) -> tuple[TransactionFeatures, ...]:
    """Derive only features available at request cutoff."""

    normalized = normalize_transactions(request)
    available = tuple(item for item in normalized if item.available_at_cutoff)
    loan_ids = {item.disbursement_transaction_id for item in request.loans}
    redemption_ids = {
        item.related_account_transaction_id
        for item in request.investment_transactions
        if item.transaction_type == "REDEMPTION"
        and item.related_account_transaction_id is not None
    }
    reversed_ids = {
        item.source.reversal_of_transaction_id
        for item in available
        if item.source.reversal_of_transaction_id is not None
    }
    debits_by_key: dict[tuple[str, int], tuple[NormalizedTransaction, ...]] = {}
    for item in available:
        transaction = item.source
        if transaction.direction != "DEBIT":
            continue
        key = (transaction.posted_at, transaction.amount_minor)
        debits_by_key[key] = (*debits_by_key.get(key, ()), item)

    recurrence_by_id: dict[
        str,
        tuple[int, int, int, int | None, int | None, float | None],
    ] = {}
    history: dict[str, list[NormalizedTransaction]] = {}
    chronological = sorted(
        available,
        key=lambda item: (
            item.source.observed_at,
            item.source.posted_at,
            item.source.transaction_id,
        ),
    )
    for item in chronological:
        transaction = item.source
        prior = history.setdefault(item.counterparty_cluster, [])
        observed_date = date.fromisoformat(transaction.observed_at)
        prior_90d = [
            previous
            for previous in prior
            if 0
            <= (observed_date - date.fromisoformat(previous.source.observed_at)).days
            <= 90
        ]
        amounts = [previous.source.amount_minor for previous in prior]
        amount_mean = round(fmean(amounts)) if amounts else None
        amount_cv = (
            pstdev(amounts) / fmean(amounts)
            if len(amounts) >= 2 and fmean(amounts)
            else None
        )
        days_since_prior = (
            (
                observed_date
                - date.fromisoformat(prior[-1].source.observed_at)
            ).days
            if prior
            else None
        )
        recurrence_by_id[transaction.transaction_id] = (
            len(prior),
            len(prior_90d),
            sum(
                previous.source.amount_minor == transaction.amount_minor
                for previous in prior
            ),
            days_since_prior,
            amount_mean,
            round(amount_cv, 8) if amount_cv is not None else None,
        )
        prior.append(item)

    result: list[TransactionFeatures] = []
    for item in normalized:
        recurrence = recurrence_by_id.get(
            item.source.transaction_id,
            (0, 0, 0, None, None, None),
        )
        result.append(
            TransactionFeatures(
                transaction=item,
                is_known_loan_disbursement=(item.source.transaction_id in loan_ids),
                is_known_investment_redemption=(
                    item.source.transaction_id in redemption_ids
                ),
                has_visible_own_transfer_pair=(
                    item.source.direction == "CREDIT"
                    and any(
                        candidate.source.account_id != item.source.account_id
                        for candidate in debits_by_key.get(
                            (item.source.posted_at, item.source.amount_minor), ()
                        )
                    )
                ),
                is_reversed_original=(item.source.transaction_id in reversed_ids),
                prior_same_counterparty_count=recurrence[0],
                prior_same_counterparty_count_90d=recurrence[1],
                prior_same_amount_count=recurrence[2],
                days_since_prior_observation=recurrence[3],
                prior_amount_mean_minor=recurrence[4],
                prior_amount_coefficient_of_variation=recurrence[5],
            )
        )
    return tuple(result)


__all__ = ["FEATURE_VERSION", "TransactionFeatures", "extract_transaction_features"]
