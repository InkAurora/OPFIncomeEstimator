"""Estimator explanation contract 1.0.

Output `1.1` says what the estimator concluded. This contract says why: which credits were included
or excluded and under which rule, which streams were detected, what each component estimated, how
confidence decomposed, and which features moved the capacity model.

The contract is production-facing evidence, so it carries only observed identifiers and rule names.
No private label may appear in it, and a leakage test enforces that against the frozen truth field
list rather than against a hand-kept list here.

Feature contributions are exact rather than approximated. The capacity model is additive over
stumps, so each tree attributes to exactly one feature and the decomposition is a property of the
model, not an estimate of it.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from income_estimator.contracts.output_v1_1 import (
    ComponentEstimateV11,
    ConfidenceComponentV11,
    IncomeStreamSummaryV11,
)
from income_estimator.contracts.v1 import EstimatorContractModel

ESTIMATOR_EXPLANATION_CONTRACT_VERSION = "1.0"


class TransactionExplanationV1(EstimatorContractModel):
    """One observed credit and the rule that decided its fate."""

    schema_version: Literal["1.0"] = "1.0"
    transaction_id: str = Field(min_length=1)
    posted_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount_minor: int = Field(gt=0)
    decision: Literal["INCOME", "EXCLUDED", "AMBIGUOUS"]
    income_probability_basis_points: int = Field(ge=0, le=10_000)
    reason_codes: tuple[str, ...] = Field(min_length=1)
    counterparty_cluster: str = Field(min_length=1)


class FeatureContributionV1(EstimatorContractModel):
    """One feature's additive effect on the capacity model's log estimate."""

    schema_version: Literal["1.0"] = "1.0"
    feature_name: str = Field(min_length=1)
    feature_value: float | int | None = None
    log_contribution: float
    is_missing_feature: bool = False

    @model_validator(mode="after")
    def validate_missingness(self) -> Self:
        if self.is_missing_feature != (self.feature_value is None):
            raise ValueError("is_missing_feature must agree with feature_value")
        return self


class CapacityExplanationV1(EstimatorContractModel):
    """How the capacity model reached its number, decomposed exactly."""

    schema_version: Literal["1.0"] = "1.0"
    model_version: str = Field(min_length=1)
    anchor_feature_name: str = Field(min_length=1)
    anchor_log_value: float
    base_score: float
    predicted_log_target: float
    predicted_minor: int = Field(ge=0)
    positive_gate_basis_points: int = Field(ge=0, le=10_000)
    gate_threshold_basis_points: int = Field(ge=0, le=10_000)
    contributions: tuple[FeatureContributionV1, ...] = ()

    @model_validator(mode="after")
    def validate_decomposition(self) -> Self:
        names = [item.feature_name for item in self.contributions]
        if len(names) != len(set(names)):
            raise ValueError("feature contributions must be unique")
        total = self.anchor_log_value + self.base_score + sum(
            item.log_contribution for item in self.contributions
        )
        if abs(total - self.predicted_log_target) > 1e-6:
            raise ValueError(
                "contributions must reconstruct predicted_log_target within tolerance"
            )
        return self


class MonthlyExplanationV1(EstimatorContractModel):
    """Every reason behind one reference month."""

    schema_version: Literal["1.0"] = "1.0"
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    realized_income_estimate_minor: int = Field(ge=0)
    sustainable_income_estimate_minor: int | None = Field(default=None, ge=0)
    routing_reason_codes: tuple[str, ...] = ()
    component_estimates: tuple[ComponentEstimateV11, ...] = ()
    confidence_score_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    confidence_components: tuple[ConfidenceComponentV11, ...] = ()
    included_transactions: tuple[TransactionExplanationV1, ...] = ()
    excluded_transactions: tuple[TransactionExplanationV1, ...] = ()
    capacity: CapacityExplanationV1 | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        included = {item.transaction_id for item in self.included_transactions}
        excluded = {item.transaction_id for item in self.excluded_transactions}
        if included & excluded:
            raise ValueError("a transaction cannot be both included and excluded")
        if any(item.decision != "INCOME" for item in self.included_transactions):
            raise ValueError("included transactions must carry the INCOME decision")
        if any(item.decision == "INCOME" for item in self.excluded_transactions):
            raise ValueError("excluded transactions must not carry the INCOME decision")
        return self


class EstimationExplanationV1(EstimatorContractModel):
    """Traceable evidence for one customer, versioned end to end."""

    schema_version: Literal["1.0"] = "1.0"
    estimator_version: str = Field(min_length=1)
    feature_version: str = Field(min_length=1)
    input_contract_version: str = Field(min_length=1)
    output_contract_version: str = Field(min_length=1)
    model_versions: tuple[str, ...] = ()
    component_versions: tuple[str, ...] = ()
    run_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    income_streams: tuple[IncomeStreamSummaryV11, ...] = ()
    monthly_explanations: tuple[MonthlyExplanationV1, ...]

    @model_validator(mode="after")
    def validate_months(self) -> Self:
        months = tuple(item.month for item in self.monthly_explanations)
        if months != tuple(sorted(months)) or len(months) != len(set(months)):
            raise ValueError("monthly explanations must be uniquely ordered by month")
        return self


__all__ = [
    "ESTIMATOR_EXPLANATION_CONTRACT_VERSION",
    "CapacityExplanationV1",
    "EstimationExplanationV1",
    "FeatureContributionV1",
    "MonthlyExplanationV1",
    "TransactionExplanationV1",
]
