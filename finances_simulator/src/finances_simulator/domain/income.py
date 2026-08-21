"""Income-diversity and sampled-customer domain contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_INCOME_AMOUNT_MINOR = 1_000_000_000_000
MAX_FACTORY_CUSTOMERS = 1_000_000

BasisPoints = Annotated[int, Field(ge=0, le=100_000)]
SeasonalityBasisPoints = Annotated[int, Field(ge=0, le=20_000)]


class IncomeProfile(StrEnum):
    """Customer-level income archetype selected by the population factory."""

    SALARIED = "SALARIED"
    SELF_EMPLOYED = "SELF_EMPLOYED"
    BUSINESS_OWNER = "BUSINESS_OWNER"
    RETIRED = "RETIRED"
    INVESTOR = "INVESTOR"
    MIXED = "MIXED"
    UNEMPLOYED = "UNEMPLOYED"


class IncomeKind(StrEnum):
    """Private economic origin of one true-income source."""

    SALARY = "SALARY"
    SELF_EMPLOYMENT = "SELF_EMPLOYMENT"
    BUSINESS_PROFIT = "BUSINESS_PROFIT"
    PENSION = "PENSION"
    INVESTMENT_DISTRIBUTION = "INVESTMENT_DISTRIBUTION"
    OTHER = "OTHER"


class IncomeFrequency(StrEnum):
    """Supported regular schedule frequencies for income attempts."""

    MONTHLY = "MONTHLY"
    EVERY_TWO_MONTHS = "EVERY_TWO_MONTHS"
    QUARTERLY = "QUARTERLY"
    SEMIANNUALLY = "SEMIANNUALLY"
    ANNUALLY = "ANNUALLY"

    @property
    def interval_months(self) -> int:
        """Return number of calendar months between scheduled attempts."""

        return {
            IncomeFrequency.MONTHLY: 1,
            IncomeFrequency.EVERY_TWO_MONTHS: 2,
            IncomeFrequency.QUARTERLY: 3,
            IncomeFrequency.SEMIANNUALLY: 6,
            IncomeFrequency.ANNUALLY: 12,
        }[self]


class BehaviorProfile(StrEnum):
    """Income-independent spending and saving dimension."""

    LOW_SPENDING = "LOW_SPENDING"
    BALANCED = "BALANCED"
    HIGH_SPENDING = "HIGH_SPENDING"


class WealthBand(StrEnum):
    """Income-independent opening financial-wealth dimension."""

    LOW = "LOW"
    MIDDLE = "MIDDLE"
    HIGH = "HIGH"


class IncomeDomainModel(BaseModel):
    """Strict immutable base for Phase-4 income domain values."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SampledIncomeSource(IncomeDomainModel):
    """Factory-selected source parameters before run identifiers are materialized."""

    source_ref: str
    income_kind: IncomeKind
    payer: str
    description: str
    destination_account_ref: str
    base_amount_minor: int = Field(gt=0, le=MAX_INCOME_AMOUNT_MINOR)
    day_of_month: int = Field(ge=1, le=31)
    frequency: IncomeFrequency
    start_month_index: int = Field(ge=0, le=1_199)
    occurrences: int = Field(ge=1, le=1_200)
    payment_probability_basis_points: int = Field(ge=0, le=10_000)
    volatility_basis_points: int = Field(ge=0, le=10_000)
    seasonality_basis_points: tuple[SeasonalityBasisPoints, ...] = Field(
        min_length=12,
        max_length=12,
    )


class CustomerFactoryMember(IncomeDomainModel):
    """One reproducible draw from independent and conditional factory dimensions."""

    customer_index: int = Field(ge=0, lt=MAX_FACTORY_CUSTOMERS)
    income_profile: IncomeProfile
    source_bundle_ref: str
    behavior_profile: BehaviorProfile
    wealth_band: WealthBand
    spending_multiplier_basis_points: BasisPoints
    saving_multiplier_basis_points: BasisPoints
    deposit_balance_multiplier_basis_points: BasisPoints
    investment_balance_multiplier_basis_points: BasisPoints
    income_sources: tuple[SampledIncomeSource, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def source_references_must_be_unique(self) -> CustomerFactoryMember:
        refs = [source.source_ref for source in self.income_sources]
        if len(refs) != len(set(refs)):
            raise ValueError("income_sources source_ref values must be unique")

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


class IncomeSource(IncomeDomainModel):
    """Materialized hidden source for one simulated customer."""

    income_source_id: str
    customer_id: str
    source_ref: str
    source_bundle_ref: str
    income_kind: IncomeKind
    currency: str
    payer: str
    description: str
    destination_account_id: str
    base_amount_minor: int = Field(gt=0, le=MAX_INCOME_AMOUNT_MINOR)
    day_of_month: int = Field(ge=1, le=31)
    frequency: IncomeFrequency
    start_month_index: int = Field(ge=0, le=1_199)
    occurrences: int = Field(ge=1, le=1_200)
    payment_probability_basis_points: int = Field(ge=0, le=10_000)
    volatility_basis_points: int = Field(ge=0, le=10_000)
    seasonality_basis_points: tuple[SeasonalityBasisPoints, ...] = Field(
        min_length=12,
        max_length=12,
    )


__all__ = [
    "BehaviorProfile",
    "CustomerFactoryMember",
    "IncomeFrequency",
    "IncomeKind",
    "IncomeProfile",
    "IncomeSource",
    "MAX_FACTORY_CUSTOMERS",
    "MAX_INCOME_AMOUNT_MINOR",
    "SampledIncomeSource",
    "SeasonalityBasisPoints",
    "WealthBand",
]
