"""Isolated join between observed transaction features and private labels."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from finances_simulator.batch import GeneratedPopulation
from finances_simulator.integration import build_estimator_input_v1_1

from income_estimator.contracts import validate_estimator_input
from income_estimator.models.transaction_classifier import (
    build_transaction_model_features,
)
from income_estimator.transaction_intelligence import (
    IncomeRuleClassifier,
    extract_transaction_features,
)

DATASET_VERSION = "synthetic-transactions-1.0.0"
SPLIT_VERSION = "customer-sha256-70-15-15-v1"


@dataclass(frozen=True, slots=True)
class LabeledTransaction:
    customer_id: str
    transaction_id: str
    label: int
    economic_type: str
    model_features: dict[str, float]
    baseline_is_income: bool
    hard_excluded: bool
    baseline_reason_codes: tuple[str, ...]


def build_labeled_dataset(
    populations: tuple[GeneratedPopulation, ...],
) -> tuple[LabeledTransaction, ...]:
    """Join only after observed feature extraction has completed."""

    classifier = IncomeRuleClassifier()
    records: list[LabeledTransaction] = []
    for population in populations:
        for generated in population.members:
            external_request = build_estimator_input_v1_1(generated)
            request = validate_estimator_input(external_request)
            observed_features = extract_transaction_features(request)
            truth_by_id = {
                item.entry_id: str(getattr(item.economic_type, "value", item.economic_type))
                for item in generated.ground_truth.transactions
            }
            for features in observed_features:
                transaction = features.transaction.source
                if (
                    transaction.direction != "CREDIT"
                    or not features.transaction.available_at_cutoff
                    or not features.transaction.inside_window
                ):
                    continue
                source_id = (
                    transaction.duplicate_of_transaction_id
                    or transaction.reversal_of_transaction_id
                    or transaction.transaction_id
                )
                economic_type = truth_by_id.get(source_id, "UNMATCHED")
                label = int(
                    economic_type == "INCOME"
                    and transaction.duplicate_of_transaction_id is None
                    and transaction.reversal_of_transaction_id is None
                )
                baseline = classifier.classify(features)
                records.append(
                    LabeledTransaction(
                        customer_id=request.customer_id,
                        transaction_id=transaction.transaction_id,
                        label=label,
                        economic_type=economic_type,
                        model_features=build_transaction_model_features(features),
                        baseline_is_income=baseline.classification == "INCOME",
                        hard_excluded=baseline.classification == "EXCLUDED",
                        baseline_reason_codes=baseline.reason_codes,
                    )
                )
    return tuple(sorted(records, key=lambda item: (item.customer_id, item.transaction_id)))


def customer_partition(customer_id: str) -> str:
    bucket = int.from_bytes(
        sha256(f"{SPLIT_VERSION}:{customer_id}".encode()).digest()[:8],
        "big",
    ) % 10_000
    if bucket < 7_000:
        return "train"
    if bucket < 8_500:
        return "validation"
    return "test"


def split_by_customer(
    records: tuple[LabeledTransaction, ...],
) -> dict[str, tuple[LabeledTransaction, ...]]:
    result: dict[str, list[LabeledTransaction]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    for record in records:
        result[customer_partition(record.customer_id)].append(record)
    return {name: tuple(items) for name, items in result.items()}


__all__ = [
    "DATASET_VERSION",
    "SPLIT_VERSION",
    "LabeledTransaction",
    "build_labeled_dataset",
    "customer_partition",
    "split_by_customer",
]
