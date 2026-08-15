"""Ground-truth financial event domain models."""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EconomicType(StrEnum):
    """Private economic classifications reserved by contract schema 1.0."""

    INCOME = "INCOME"
    OWN_TRANSFER = "OWN_TRANSFER"
    EXPENSE = "EXPENSE"
    INVESTMENT_CONTRIBUTION = "INVESTMENT_CONTRIBUTION"
    INVESTMENT_REDEMPTION = "INVESTMENT_REDEMPTION"
    LOAN_DISBURSEMENT = "LOAN_DISBURSEMENT"
    LOAN_PAYMENT = "LOAN_PAYMENT"
    REFUND = "REFUND"
    REVERSAL = "REVERSAL"
    GIFT = "GIFT"
    ASSET_SALE = "ASSET_SALE"
    OTHER = "OTHER"


class FinancialEvent(BaseModel):
    """Economic event in hidden simulator state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    customer_id: str
    occurred_at: date
    economic_type: EconomicType
    amount_minor: int = Field(gt=0)
    currency: str
    source_entity: str
    destination_entity: str
    income_source_id: str | None = None
    caused_by_event_id: str | None = None
    description: str
    metadata: dict[str, str | int] = Field(default_factory=dict)
