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
    has_linked_own_transfer_pair: bool
    has_unlinked_same_day_amount_debit: bool
    is_reversed_original: bool
    prior_same_counterparty_count: int
    prior_same_counterparty_count_90d: int
    prior_same_amount_count: int
    days_since_prior_observation: int | None
    prior_amount_mean_minor: int | None
    prior_amount_coefficient_of_variation: float | None


def _has_counterparty_evidence(cluster: str) -> bool:
    """Whether a cluster names a counterparty rather than falling back to free text.

    ``_counterparty_cluster`` prefers a document hash, then a counterparty name, and only then the
    normalized description. The first two identify who was on the other side of the payment; the
    third is text a provider chose, and two transactions sharing it are not thereby two legs of one
    transfer.
    """

    return cluster.startswith(("document:", "name:"))


def _own_transfer_pairs(
    available: tuple[NormalizedTransaction, ...],
    debits_by_key: dict[tuple[str, int], tuple[NormalizedTransaction, ...]],
) -> tuple[frozenset[str], frozenset[str]]:
    """Split same-day equal-amount cross-account credits into linked pairs and bare coincidences.

    Equal amounts on one day across two consented accounts is what an own transfer looks like, and
    also what an unrelated salary and rent payment look like. Treating the coincidence as proof
    deleted a R$5,000 salary because R$5,000 of rent left another account that morning. A pair is
    established here only when both legs name the same counterparty; everything else is returned as
    a coincidence for the classifier to weigh against whatever income evidence the credit carries.

    Matching is one-to-one and settled in a deterministic order, so one debit cannot cancel several
    equal credits on the same day.
    """

    linked: set[str] = set()
    coincident: set[str] = set()
    consumed: set[str] = set()
    credits_in_order = sorted(
        (item for item in available if item.source.direction == "CREDIT"),
        key=lambda item: (
            item.source.posted_at,
            item.source.amount_minor,
            item.source.transaction_id,
        ),
    )
    for item in credits_in_order:
        candidates = [
            candidate
            for candidate in debits_by_key.get(
                (item.source.posted_at, item.source.amount_minor), ()
            )
            if candidate.source.account_id != item.source.account_id
            and candidate.source.transaction_id not in consumed
        ]
        if not candidates:
            continue
        ordered = sorted(candidates, key=lambda entry: entry.source.transaction_id)
        match = next(
            (
                candidate
                for candidate in ordered
                if _has_counterparty_evidence(item.counterparty_cluster)
                and candidate.counterparty_cluster == item.counterparty_cluster
            ),
            None,
        )
        # The debit is spent either way. One outgoing payment can be the other leg of at most one
        # incoming payment, so leaving an unlinked partner available would let a single debit settle
        # every equal credit that day, which is the many-to-one failure this pairing must not have.
        if match is None:
            consumed.add(ordered[0].source.transaction_id)
            coincident.add(item.source.transaction_id)
            continue
        consumed.add(match.source.transaction_id)
        linked.add(item.source.transaction_id)
    return frozenset(linked), frozenset(coincident)


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

    linked_transfer_ids, unlinked_coincidence_ids = _own_transfer_pairs(available, debits_by_key)

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
                has_linked_own_transfer_pair=(
                    item.source.transaction_id in linked_transfer_ids
                ),
                has_unlinked_same_day_amount_debit=(
                    item.source.transaction_id in unlinked_coincidence_ids
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
