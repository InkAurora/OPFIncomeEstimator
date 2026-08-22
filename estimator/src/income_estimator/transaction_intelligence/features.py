"""Observed-only transaction feature extraction."""

from __future__ import annotations

from dataclasses import dataclass

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

    return tuple(
        TransactionFeatures(
            transaction=item,
            is_known_loan_disbursement=(item.source.transaction_id in loan_ids),
            is_known_investment_redemption=(item.source.transaction_id in redemption_ids),
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
        )
        for item in normalized
    )


__all__ = ["FEATURE_VERSION", "TransactionFeatures", "extract_transaction_features"]
