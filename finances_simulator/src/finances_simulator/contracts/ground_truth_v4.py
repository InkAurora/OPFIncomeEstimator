"""Schema 1.4 private life-event and anomaly ground-truth contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finances_simulator.contracts.ground_truth_v3 import (
    BalanceSheetGroundTruthV3,
    CardTransactionGroundTruthV3,
    CustomerGroundTruthV3,
    CustomerMonthGroundTruthV3,
    IncomeSourceGroundTruthV3,
    InvestmentTransactionGroundTruthV3,
    LoanPaymentGroundTruthV3,
    TransactionGroundTruthV3,
)
from finances_simulator.domain.events import EconomicType
from finances_simulator.domain.life_events import (
    AnomalyType,
    CustomerLifeState,
    EmploymentStatus,
    IncomeSourceState,
    LifeEventType,
    MaritalStatus,
)


class GroundTruthModelV4(BaseModel):
    """Strict immutable base for schema 1.4 private records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.4"] = "1.4"


class CustomerGroundTruthV4(CustomerGroundTruthV3):
    schema_version: Literal["1.4"] = "1.4"
    initial_life_state: CustomerLifeState
    final_life_state: CustomerLifeState


class CustomerMonthGroundTruthV4(CustomerMonthGroundTruthV3):
    schema_version: Literal["1.4"] = "1.4"
    external_inflows_minor: int = Field(ge=0)
    life_event_count: int = Field(ge=0)
    anomaly_count: int = Field(ge=0)
    employment_status: EmploymentStatus
    active_income_source_ids: tuple[str, ...]
    marital_status: MaritalStatus
    dependent_count: int = Field(ge=0)
    property_count: int = Field(ge=0)
    vehicle_count: int = Field(ge=0)


class TransactionGroundTruthV4(TransactionGroundTruthV3):
    schema_version: Literal["1.4"] = "1.4"


class CardTransactionGroundTruthV4(CardTransactionGroundTruthV3):
    schema_version: Literal["1.4"] = "1.4"


class LoanPaymentGroundTruthV4(LoanPaymentGroundTruthV3):
    schema_version: Literal["1.4"] = "1.4"


class InvestmentTransactionGroundTruthV4(InvestmentTransactionGroundTruthV3):
    schema_version: Literal["1.4"] = "1.4"


class BalanceSheetGroundTruthV4(BalanceSheetGroundTruthV3):
    schema_version: Literal["1.4"] = "1.4"


class IncomeSourceGroundTruthV4(IncomeSourceGroundTruthV3):
    schema_version: Literal["1.4"] = "1.4"


class LifeEventGroundTruthV4(GroundTruthModelV4):
    """State and source parameters immediately around one effective transition."""

    life_event_id: str
    life_event_ref: str
    customer_id: str
    event_type: LifeEventType
    effective_date: str
    state_before: CustomerLifeState
    state_after: CustomerLifeState
    income_sources_before: tuple[IncomeSourceState, ...]
    income_sources_after: tuple[IncomeSourceState, ...]
    annualized_base_income_before_minor: int = Field(ge=0)
    annualized_base_income_after_minor: int = Field(ge=0)
    financial_event_id: str | None = None


class AnomalyGroundTruthV4(GroundTruthModelV4):
    """Private anomaly label with preserved underlying economic type."""

    anomaly_id: str
    anomaly_ref: str
    customer_id: str
    anomaly_type: AnomalyType
    occurred_at: str
    financial_event_id: str
    economic_type: EconomicType


__all__ = [
    "AnomalyGroundTruthV4",
    "BalanceSheetGroundTruthV4",
    "CardTransactionGroundTruthV4",
    "CustomerGroundTruthV4",
    "CustomerMonthGroundTruthV4",
    "GroundTruthModelV4",
    "IncomeSourceGroundTruthV4",
    "InvestmentTransactionGroundTruthV4",
    "LifeEventGroundTruthV4",
    "LoanPaymentGroundTruthV4",
    "TransactionGroundTruthV4",
]
