"""Life-event state transitions and labeled financial anomalies."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finances_simulator.domain.events import EconomicType


class LifeEventType(StrEnum):
    """Supported changes to customer state or exceptional cash flow."""

    RAISE = "RAISE"
    PROMOTION = "PROMOTION"
    JOB_LOSS = "JOB_LOSS"
    JOB_CHANGE = "JOB_CHANGE"
    MARRIAGE = "MARRIAGE"
    DIVORCE = "DIVORCE"
    DEPENDENT_ADDED = "DEPENDENT_ADDED"
    DEPENDENT_REMOVED = "DEPENDENT_REMOVED"
    PROPERTY_PURCHASE = "PROPERTY_PURCHASE"
    VEHICLE_PURCHASE = "VEHICLE_PURCHASE"
    BONUS = "BONUS"
    INHERITANCE = "INHERITANCE"
    MEDICAL_EXPENSE = "MEDICAL_EXPENSE"
    VACATION = "VACATION"


class AnomalyType(StrEnum):
    """Private labels for estimator-confounding exceptional observations."""

    LARGE_PIX_TRANSFER = "LARGE_PIX_TRANSFER"
    REFUND = "REFUND"
    ASSET_SALE = "ASSET_SALE"
    INVESTMENT_REDEMPTION = "INVESTMENT_REDEMPTION"


class EmploymentStatus(StrEnum):
    """Employment dimension of hidden customer state."""

    SALARIED = "SALARIED"
    SELF_EMPLOYED = "SELF_EMPLOYED"
    BUSINESS_OWNER = "BUSINESS_OWNER"
    RETIRED = "RETIRED"
    INVESTOR = "INVESTOR"
    MIXED = "MIXED"
    UNEMPLOYED = "UNEMPLOYED"


class MaritalStatus(StrEnum):
    """Marital dimension of hidden customer state."""

    SINGLE = "SINGLE"
    MARRIED = "MARRIED"
    DIVORCED = "DIVORCED"


class LifeEventDomainModel(BaseModel):
    """Strict immutable base for Phase-5 hidden domain records."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CustomerLifeState(LifeEventDomainModel):
    """Customer state immediately before or after a transition."""

    employment_status: EmploymentStatus
    employer: str | None = None
    job_title: str | None = None
    marital_status: MaritalStatus
    dependent_count: int = Field(ge=0, le=100)
    property_count: int = Field(ge=0, le=100)
    vehicle_count: int = Field(ge=0, le=100)


class IncomeSourceState(LifeEventDomainModel):
    """Effective parameters of one income source at a point in time."""

    income_source_id: str
    source_ref: str
    active: bool
    base_amount_minor: int = Field(gt=0)
    payer: str
    description: str


class LifeEventTransition(LifeEventDomainModel):
    """Auditable state transition produced by one configured life event."""

    life_event_id: str
    life_event_ref: str
    customer_id: str
    event_type: LifeEventType
    effective_date: date
    state_before: CustomerLifeState
    state_after: CustomerLifeState
    income_sources_before: tuple[IncomeSourceState, ...]
    income_sources_after: tuple[IncomeSourceState, ...]
    financial_event_id: str | None = None

    @model_validator(mode="after")
    def source_sets_must_match(self) -> LifeEventTransition:
        before = {item.income_source_id for item in self.income_sources_before}
        after = {item.income_source_id for item in self.income_sources_after}
        if len(before) != len(self.income_sources_before):
            raise ValueError("income_sources_before IDs must be unique")
        if len(after) != len(self.income_sources_after):
            raise ValueError("income_sources_after IDs must be unique")
        if before != after:
            raise ValueError("life events cannot add or remove materialized income-source IDs")
        return self


class FinancialAnomaly(LifeEventDomainModel):
    """Private anomaly label linked to one correctly typed financial event."""

    anomaly_id: str
    anomaly_ref: str
    customer_id: str
    anomaly_type: AnomalyType
    occurred_at: date
    financial_event_id: str
    economic_type: EconomicType


__all__ = [
    "AnomalyType",
    "CustomerLifeState",
    "EmploymentStatus",
    "FinancialAnomaly",
    "IncomeSourceState",
    "LifeEventTransition",
    "LifeEventType",
    "MaritalStatus",
]
