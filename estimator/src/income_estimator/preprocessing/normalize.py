"""Normalize observed transactions without accessing private simulator fields."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from income_estimator.contracts.v1 import EstimatorInputV1, EstimatorTransactionV1

_WHITESPACE = re.compile(r"\s+")
_INSTITUTION_PREFIX = re.compile(r"^[A-Z0-9]{2,12}\s*\|\s*")


@dataclass(frozen=True, slots=True)
class NormalizedTransaction:
    source: EstimatorTransactionV1
    normalized_description: str
    counterparty_cluster: str
    posted_month: str
    available_at_cutoff: bool
    inside_window: bool


def normalize_description(description: str) -> str:
    """Create deterministic matching text while retaining auditable source text."""

    decomposed = unicodedata.normalize("NFKD", description)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    normalized = _WHITESPACE.sub(" ", without_marks.upper()).strip()
    return _INSTITUTION_PREFIX.sub("", normalized)


def _counterparty_cluster(
    transaction: EstimatorTransactionV1,
    normalized_description: str,
) -> str:
    document_hash = getattr(transaction, "counterparty_document_hash", None)
    if document_hash:
        return f"document:{document_hash.lower()}"
    counterparty_name = getattr(transaction, "counterparty_name", None)
    if counterparty_name:
        return f"name:{normalize_description(counterparty_name)}"
    return f"description:{normalized_description}"


def normalize_transactions(request: EstimatorInputV1) -> tuple[NormalizedTransaction, ...]:
    """Normalize in stable ID order and mark records unavailable at reference cutoff."""

    result: list[NormalizedTransaction] = []
    for transaction in request.transactions:
        available_at_cutoff = transaction.observed_at <= request.window_end
        normalized_description = (
            normalize_description(transaction.description)
            if available_at_cutoff
            else ""
        )
        result.append(
            NormalizedTransaction(
                source=transaction,
                normalized_description=normalized_description,
                counterparty_cluster=(
                    _counterparty_cluster(transaction, normalized_description)
                    if available_at_cutoff
                    else ""
                ),
                posted_month=transaction.posted_at[:7],
                available_at_cutoff=available_at_cutoff,
                inside_window=(
                    request.window_start <= transaction.posted_at <= request.window_end
                ),
            )
        )
    return tuple(sorted(result, key=lambda item: item.source.transaction_id))


__all__ = ["NormalizedTransaction", "normalize_description", "normalize_transactions"]
