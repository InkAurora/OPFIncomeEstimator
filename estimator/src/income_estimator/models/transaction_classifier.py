"""Portable JSON gradient-boosted stump classifier for transaction income."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from income_estimator.transaction_intelligence.features import TransactionFeatures

MODEL_FEATURE_VERSION = "transaction-model-features-1.0.0"

TOKEN_FEATURES = (
    "PAYROLL",
    "SALARY",
    "WAGE",
    "PENSION",
    "SERVICE",
    "PROFIT",
    "DISTRIBUTION",
    "BONUS",
    "COMMISSION",
    "DIVIDEND",
    "BENEFIT",
    "TRANSFER",
    "LOAN",
    "REDEMPTION",
    "REFUND",
    "REVERSAL",
    "ESTATE",
    "SALE",
)

MODEL_FEATURE_NAMES = (
    "log_amount_minor",
    "day_of_month",
    "has_provider_transaction_type",
    "has_observed_counterparty",
    "has_balance_after",
    "balance_to_amount_ratio",
    "prior_same_counterparty_count",
    "prior_same_counterparty_count_90d",
    "prior_same_amount_ratio",
    "days_since_prior_observation",
    "amount_to_prior_mean_ratio",
    "prior_amount_coefficient_of_variation",
    *(f"description_has_{token.lower()}" for token in TOKEN_FEATURES),
)


class ModelArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DecisionStump(ModelArtifact):
    feature_name: str
    threshold: float
    left_value: float
    right_value: float


class TransactionClassifierArtifact(ModelArtifact):
    schema_version: Literal["1.0"] = "1.0"
    model_version: str = Field(min_length=1)
    feature_version: Literal["transaction-model-features-1.0.0"] = MODEL_FEATURE_VERSION
    feature_names: tuple[str, ...]
    base_score: float
    learning_rate: float = Field(gt=0, le=1)
    decision_threshold_basis_points: int = Field(ge=0, le=10_000)
    trees: tuple[DecisionStump, ...]
    dataset_version: str = Field(min_length=1)
    split_version: str = Field(min_length=1)
    simulator_version: str = Field(min_length=1)
    source_contract_versions: tuple[str, ...] = Field(min_length=1)
    training_rounds_requested: int = Field(gt=0)
    l2_regularization: float = Field(gt=0)
    minimum_leaf_size: int = Field(gt=0)
    training_customer_count: int = Field(gt=0)
    validation_customer_count: int = Field(gt=0)


def build_transaction_model_features(
    features: TransactionFeatures,
) -> dict[str, float]:
    """Create fixed numeric vector from point-in-time observed features only."""

    transaction = features.transaction.source
    description = features.transaction.normalized_description
    balance_after = getattr(transaction, "balance_after_minor", None)
    prior_count = features.prior_same_counterparty_count
    result = {
        "log_amount_minor": math.log1p(transaction.amount_minor),
        "day_of_month": int(transaction.posted_at[8:10]) / 31,
        "has_provider_transaction_type": float(
            bool(getattr(transaction, "provider_transaction_type", None))
        ),
        "has_observed_counterparty": float(
            bool(
                getattr(transaction, "counterparty_document_hash", None)
                or getattr(transaction, "counterparty_name", None)
            )
        ),
        "has_balance_after": float(balance_after is not None),
        "balance_to_amount_ratio": (
            max(-20.0, min(20.0, balance_after / transaction.amount_minor))
            if balance_after is not None
            else 0.0
        ),
        "prior_same_counterparty_count": math.log1p(prior_count),
        "prior_same_counterparty_count_90d": math.log1p(
            features.prior_same_counterparty_count_90d
        ),
        "prior_same_amount_ratio": (
            features.prior_same_amount_count / prior_count if prior_count else 0.0
        ),
        "days_since_prior_observation": min(
            2.0,
            (features.days_since_prior_observation or 0) / 365,
        ),
        "amount_to_prior_mean_ratio": (
            min(
                20.0,
                transaction.amount_minor / features.prior_amount_mean_minor,
            )
            if features.prior_amount_mean_minor
            else 0.0
        ),
        "prior_amount_coefficient_of_variation": min(
            10.0,
            features.prior_amount_coefficient_of_variation or 0.0,
        ),
    }
    result.update(
        {
            f"description_has_{token.lower()}": float(token in description)
            for token in TOKEN_FEATURES
        }
    )
    return result


class GradientBoostedTransactionClassifier:
    """Dependency-free inference for a validated versioned model artifact."""

    def __init__(self, artifact: TransactionClassifierArtifact) -> None:
        if artifact.feature_names != MODEL_FEATURE_NAMES:
            raise ValueError("model feature_names do not match runtime feature version")
        self.artifact = artifact

    @classmethod
    def from_path(cls, path: Path) -> GradientBoostedTransactionClassifier:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(TransactionClassifierArtifact.model_validate(payload))

    def predict_values_basis_points(self, values: dict[str, float]) -> int:
        score = self.artifact.base_score
        for tree in self.artifact.trees:
            leaf = (
                tree.left_value
                if values[tree.feature_name] <= tree.threshold
                else tree.right_value
            )
            score += self.artifact.learning_rate * leaf
        probability = 1 / (1 + math.exp(-max(-40.0, min(40.0, score))))
        return max(0, min(10_000, round(probability * 10_000)))

    def predict_income_basis_points(self, features: TransactionFeatures) -> int:
        return self.predict_values_basis_points(
            build_transaction_model_features(features)
        )


__all__ = [
    "MODEL_FEATURE_NAMES",
    "MODEL_FEATURE_VERSION",
    "DecisionStump",
    "GradientBoostedTransactionClassifier",
    "TransactionClassifierArtifact",
    "build_transaction_model_features",
]
