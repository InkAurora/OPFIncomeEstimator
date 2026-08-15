"""Private versioned contracts unavailable to the estimator."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finances_simulator.domain.accounts import Direction
from finances_simulator.domain.events import EconomicType


class GroundTruthModel(BaseModel):
    """Strict immutable base for private records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"


class CustomerGroundTruth(GroundTruthModel):
    customer_id: str
    scenario_name: str
    employment_status: Literal["SALARIED"]
    currency: str
    true_monthly_salary_minor: int = Field(gt=0)
    income_source_id: str
    primary_account_id: str
    opening_balance_minor: int


class CustomerMonthGroundTruth(GroundTruthModel):
    customer_id: str
    month: str
    currency: str
    true_income_minor: int = Field(ge=0)
    true_expenses_minor: int = Field(ge=0)
    income_event_count: int = Field(ge=0)
    expense_event_count: int = Field(ge=0)
    opening_balance_minor: int
    closing_balance_minor: int


class TransactionGroundTruth(GroundTruthModel):
    event_id: str
    entry_id: str
    customer_id: str
    account_id: str
    occurred_at: str
    economic_type: EconomicType
    direction: Direction
    amount_minor: int = Field(gt=0)
    currency: str
    source_entity: str
    destination_entity: str
    income_source_id: str | None = None
    caused_by_event_id: str | None = None
    transfer_group_id: str | None = None
    description: str
    balance_after_minor: int
    metadata: dict[str, str | int] = Field(default_factory=dict)
