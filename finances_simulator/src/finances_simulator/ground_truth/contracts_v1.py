"""Schema 1.1 private ground-truth contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finances_simulator.domain.accounts import Direction
from finances_simulator.domain.events import EconomicType


class GroundTruthModelV1(BaseModel):
    """Strict immutable base for schema 1.1 private records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.1"] = "1.1"


class CustomerGroundTruthV1(GroundTruthModelV1):
    """Private customer-level truth for multi-account card simulations."""

    customer_id: str
    scenario_name: str
    employment_status: Literal["SALARIED"]
    currency: str
    true_monthly_salary_minor: int = Field(gt=0)
    income_source_id: str
    primary_account_id: str
    opening_balance_minor: int
    account_ids: tuple[str, ...]
    card_ids: tuple[str, ...]
    total_opening_deposit_balance_minor: int = Field(ge=0)


class CustomerMonthGroundTruthV1(GroundTruthModelV1):
    """Private monthly truth across deposit accounts and credit cards."""

    customer_id: str
    month: str
    currency: str
    true_income_minor: int = Field(ge=0)
    true_expenses_minor: int = Field(ge=0)
    income_event_count: int = Field(ge=0)
    expense_event_count: int = Field(ge=0)
    opening_balance_minor: int
    closing_balance_minor: int
    total_deposit_opening_balance_minor: int
    total_deposit_closing_balance_minor: int
    total_card_outstanding_opening_minor: int = Field(ge=0)
    total_card_outstanding_closing_minor: int = Field(ge=0)


class TransactionGroundTruthV1(GroundTruthModelV1):
    """Private truth for one deposit-account ledger entry."""

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


class CardTransactionGroundTruthV1(GroundTruthModelV1):
    """Private economic truth for one credit-card purchase."""

    event_id: str
    card_transaction_id: str
    customer_id: str
    card_id: str
    occurred_at: str
    economic_type: EconomicType
    amount_minor: int = Field(gt=0)
    currency: str
    source_entity: str
    destination_entity: str
    description: str
    installment_count: int = Field(gt=0)
    outstanding_after_minor: int = Field(ge=0)
    metadata: dict[str, str | int] = Field(default_factory=dict)


__all__ = [
    "CardTransactionGroundTruthV1",
    "CustomerGroundTruthV1",
    "CustomerMonthGroundTruthV1",
    "GroundTruthModelV1",
    "TransactionGroundTruthV1",
]
