"""Schema 1.3 private income-diversity ground-truth contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finances_simulator.contracts.ground_truth_v2 import (
    BalanceSheetGroundTruthV2,
    CardTransactionGroundTruthV2,
    CustomerMonthGroundTruthV2,
    InvestmentTransactionGroundTruthV2,
    LoanPaymentGroundTruthV2,
    TransactionGroundTruthV2,
)
from finances_simulator.domain.income import (
    MAX_INCOME_AMOUNT_MINOR,
    BehaviorProfile,
    IncomeFrequency,
    IncomeKind,
    IncomeProfile,
    SeasonalityBasisPoints,
    WealthBand,
)


class GroundTruthModelV3(BaseModel):
    """Strict immutable base for schema 1.3 private records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.3"] = "1.3"


class CustomerGroundTruthV3(GroundTruthModelV3):
    """Private sampled profile, behavior, wealth, and product ownership truth."""

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
    income_source_ids: tuple[str, ...] = Field(max_length=8)
    primary_account_id: str
    opening_balance_minor: int = Field(ge=0)
    account_ids: tuple[str, ...]
    card_ids: tuple[str, ...]
    loan_ids: tuple[str, ...]
    investment_ids: tuple[str, ...]
    total_opening_deposit_balance_minor: int = Field(ge=0)
    total_opening_investment_balance_minor: int = Field(ge=0)
    total_opening_loan_principal_minor: int = Field(ge=0)


class CustomerMonthGroundTruthV3(CustomerMonthGroundTruthV2):
    """Monthly economic truth supporting zero and multiple income events."""

    schema_version: Literal["1.3"] = "1.3"


class TransactionGroundTruthV3(TransactionGroundTruthV2):
    schema_version: Literal["1.3"] = "1.3"


class CardTransactionGroundTruthV3(CardTransactionGroundTruthV2):
    schema_version: Literal["1.3"] = "1.3"


class LoanPaymentGroundTruthV3(LoanPaymentGroundTruthV2):
    schema_version: Literal["1.3"] = "1.3"


class InvestmentTransactionGroundTruthV3(InvestmentTransactionGroundTruthV2):
    schema_version: Literal["1.3"] = "1.3"


class BalanceSheetGroundTruthV3(BalanceSheetGroundTruthV2):
    schema_version: Literal["1.3"] = "1.3"


class IncomeSourceGroundTruthV3(GroundTruthModelV3):
    """Private realized parameters for one selected true-income source."""

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
    "BalanceSheetGroundTruthV3",
    "CardTransactionGroundTruthV3",
    "CustomerGroundTruthV3",
    "CustomerMonthGroundTruthV3",
    "GroundTruthModelV3",
    "IncomeSourceGroundTruthV3",
    "InvestmentTransactionGroundTruthV3",
    "LoanPaymentGroundTruthV3",
    "TransactionGroundTruthV3",
]
