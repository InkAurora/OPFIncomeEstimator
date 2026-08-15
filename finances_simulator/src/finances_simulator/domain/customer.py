"""Hidden customer-state domain models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finances_simulator.domain.accounts import Account


class CustomerTwin(BaseModel):
    """Ground-truth state for a basic salaried simulator customer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    customer_id: str
    scenario_name: str
    currency: str
    true_monthly_salary_minor: int = Field(gt=0)
    employment_status: Literal["SALARIED"] = "SALARIED"
    income_source_id: str
    primary_account: Account
