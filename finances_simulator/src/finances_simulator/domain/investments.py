"""Investment domain models for deterministic fixed-income holdings."""

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InvestmentTransactionType(StrEnum):
    """Provider-visible investment movement classification."""

    CONTRIBUTION = "CONTRIBUTION"
    REDEMPTION = "REDEMPTION"
    RETURN = "RETURN"


class Investment(BaseModel):
    """Fixed-income investment opened at simulation start."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    investment_id: str
    customer_id: str
    institution_id: str
    institution_name: str
    investment_label: str
    investment_type: str
    currency: str
    opened_on: date
    opening_balance_minor: int = Field(ge=0)
    monthly_return_basis_points: int = Field(ge=0, le=10_000)
    return_description: str

    @model_validator(mode="after")
    def type_must_be_supported(self) -> Self:
        if self.investment_type != "FIXED_INCOME":
            raise ValueError("investment_type must be FIXED_INCOME")
        return self


class InvestmentTransaction(BaseModel):
    """One accepted external flow or credited monthly return."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction_id: str
    event_id: str
    customer_id: str
    investment_id: str
    occurred_at: date
    transaction_type: InvestmentTransactionType
    amount_minor: int = Field(gt=0)
    currency: str
    description: str
    balance_after_minor: int = Field(ge=0)
    account_id: str | None = None
    rule_id: str | None = None
    occurrence_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def linkage_must_match_transaction_type(self) -> Self:
        """External flows require schedule/account linkage; returns require neither."""

        linked = (
            self.account_id is not None
            and self.rule_id is not None
            and self.occurrence_index is not None
        )
        unlinked = (
            self.account_id is None and self.rule_id is None and self.occurrence_index is None
        )
        if self.transaction_type is InvestmentTransactionType.RETURN:
            if not unlinked:
                raise ValueError("RETURN transaction cannot contain external-flow linkage")
        elif not linked:
            raise ValueError(
                "CONTRIBUTION and REDEMPTION transactions require account and rule linkage"
            )
        return self


class InvestmentBalanceSnapshot(BaseModel):
    """Investment valuation at one month-end reference date."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    customer_id: str
    investment_id: str
    reference_date: date
    balance_minor: int = Field(ge=0)
    currency: str


__all__ = [
    "Investment",
    "InvestmentBalanceSnapshot",
    "InvestmentTransaction",
    "InvestmentTransactionType",
]
