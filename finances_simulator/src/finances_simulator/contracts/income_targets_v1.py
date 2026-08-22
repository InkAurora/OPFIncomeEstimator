"""Private income-target contract 1.0.

These records implement [ADR 0002](../../../../docs/adr/0002-income-target-construction.md). They
are private truth: they may be joined with observed features only inside an isolated training or
evaluation step, never inside estimator runtime.

The contract is additive. It introduces its own version and leaves observation contracts 1.0
through 1.5, and every record they define, unchanged.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

INCOME_TARGET_CONTRACT_VERSION = "1.0"


class IncomeTargetModel(BaseModel):
    """Strict immutable base for private income-target records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"


class CustomerMonthIncomeTargetV1(IncomeTargetModel):
    """The five ADR 0001 targets for one customer and one reference month."""

    customer_id: str = Field(min_length=1)
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    currency: str = Field(min_length=3, max_length=3)

    realized_income_month_minor: int = Field(ge=0)
    expected_income_month_minor: int = Field(ge=0)
    sustainable_monthly_income_minor: int = Field(ge=0)
    realized_income_trailing_12m_minor: int | None = Field(default=None, ge=0)
    expected_income_next_12m_minor: int = Field(ge=0)

    active_source_count: int = Field(ge=0)
    recurring_source_count: int = Field(ge=0)
    bonus_income_month_minor: int = Field(ge=0)
    is_partial_month: bool = False

    @model_validator(mode="after")
    def validate_counts(self) -> "CustomerMonthIncomeTargetV1":
        if self.recurring_source_count > self.active_source_count:
            raise ValueError("recurring_source_count cannot exceed active_source_count")
        if self.bonus_income_month_minor > self.realized_income_month_minor:
            raise ValueError("bonus income cannot exceed realized income")
        if self.active_source_count == 0 and self.sustainable_monthly_income_minor:
            raise ValueError("sustainable income requires at least one active source")
        return self


__all__ = [
    "INCOME_TARGET_CONTRACT_VERSION",
    "CustomerMonthIncomeTargetV1",
    "IncomeTargetModel",
]
