"""Hidden customer-state domain models."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finances_simulator.domain.accounts import Account
from finances_simulator.domain.income import (
    BehaviorProfile,
    IncomeKind,
    IncomeProfile,
    IncomeSource,
    WealthBand,
)
from finances_simulator.domain.life_events import CustomerLifeState


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
    additional_accounts: tuple[Account, ...] = ()

    @property
    def accounts(self) -> tuple[Account, ...]:
        """Return every owned account with the primary account first."""

        return (self.primary_account, *self.additional_accounts)


class CustomerTwinV3(BaseModel):
    """Hidden customer state for one sampled income-diversity archetype."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    customer_id: str
    scenario_name: str
    currency: str
    income_profile: IncomeProfile
    source_bundle_ref: str
    behavior_profile: BehaviorProfile
    wealth_band: WealthBand
    spending_multiplier_basis_points: int = Field(ge=0, le=100_000)
    saving_multiplier_basis_points: int = Field(ge=0, le=100_000)
    deposit_balance_multiplier_basis_points: int = Field(ge=0, le=100_000)
    investment_balance_multiplier_basis_points: int = Field(ge=0, le=100_000)
    income_sources: tuple[IncomeSource, ...] = Field(max_length=8)
    primary_account: Account
    additional_accounts: tuple[Account, ...] = ()

    @property
    def accounts(self) -> tuple[Account, ...]:
        """Return every owned account with the primary account first."""

        return (self.primary_account, *self.additional_accounts)

    @model_validator(mode="after")
    def validate_income_sources(self) -> Self:
        source_ids = [source.income_source_id for source in self.income_sources]
        source_refs = [source.source_ref for source in self.income_sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("income_sources income_source_id values must be unique")
        if len(source_refs) != len(set(source_refs)):
            raise ValueError("income_sources source_ref values must be unique")

        account_ids = {account.account_id for account in self.accounts}
        for source in self.income_sources:
            if source.customer_id != self.customer_id:
                raise ValueError("income source customer_id must match customer twin")
            if source.currency != self.currency:
                raise ValueError("income source currency must match customer twin")
            if source.source_bundle_ref != self.source_bundle_ref:
                raise ValueError("income source bundle must match customer twin")
            if source.destination_account_id not in account_ids:
                raise ValueError("income source must reference an owned destination account")

        kinds = {source.income_kind for source in self.income_sources}
        required_kind = {
            IncomeProfile.SALARIED: IncomeKind.SALARY,
            IncomeProfile.SELF_EMPLOYED: IncomeKind.SELF_EMPLOYMENT,
            IncomeProfile.BUSINESS_OWNER: IncomeKind.BUSINESS_PROFIT,
            IncomeProfile.RETIRED: IncomeKind.PENSION,
            IncomeProfile.INVESTOR: IncomeKind.INVESTMENT_DISTRIBUTION,
        }.get(self.income_profile)
        if required_kind is not None and required_kind not in kinds:
            raise ValueError(f"{self.income_profile} requires an income source of {required_kind}")
        if self.income_profile is IncomeProfile.MIXED and len(kinds) < 2:
            raise ValueError("MIXED requires at least two distinct income kinds")
        if self.income_profile is IncomeProfile.UNEMPLOYED and self.income_sources:
            raise ValueError("UNEMPLOYED cannot contain income sources")
        return self


class CustomerTwinV4(CustomerTwinV3):
    """Hidden customer state with a complete Phase-5 life-state interval."""

    initial_life_state: CustomerLifeState
    final_life_state: CustomerLifeState


__all__ = ["CustomerTwin", "CustomerTwinV3", "CustomerTwinV4"]
