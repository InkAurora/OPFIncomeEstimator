"""Internal audit records not exposed through shared output contract 1.0."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from income_estimator.contracts.v1 import IncomeEstimateV1


class AuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactMetadata(AuditModel):
    estimator_version: str
    feature_version: str
    input_contract_version: str
    output_contract_version: str
    model_versions: tuple[str, ...] = ()


class TransactionDecision(AuditModel):
    transaction_id: str
    posted_month: str
    direction: Literal["CREDIT", "DEBIT"]
    amount_minor: int = Field(gt=0)
    normalized_description: str
    classification: Literal["INCOME", "EXCLUDED", "AMBIGUOUS"]
    income_probability_basis_points: int = Field(ge=0, le=10_000)
    reason_codes: tuple[str, ...] = Field(min_length=1)


class IncomeStream(AuditModel):
    stream_id: str
    counterparty_cluster: str
    first_seen: str
    last_seen: str
    frequency: Literal[
        "ONE_OFF",
        "WEEKLY",
        "BIWEEKLY",
        "MONTHLY",
        "QUARTERLY",
        "IRREGULAR",
    ]
    median_amount_minor: int = Field(gt=0)
    amount_coefficient_of_variation: float = Field(ge=0)
    recurrence_score_basis_points: int = Field(ge=0, le=10_000)
    income_probability_basis_points: int = Field(ge=0, le=10_000)
    transaction_ids: tuple[str, ...] = Field(min_length=1)


class EstimationAudit(AuditModel):
    metadata: ArtifactMetadata
    estimate: IncomeEstimateV1
    transaction_decisions: tuple[TransactionDecision, ...]
    income_streams: tuple[IncomeStream, ...]


__all__ = [
    "ArtifactMetadata",
    "EstimationAudit",
    "IncomeStream",
    "TransactionDecision",
]
