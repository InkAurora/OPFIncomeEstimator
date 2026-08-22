"""Shared, versioned boundary between simulator and income estimator."""

from __future__ import annotations

from datetime import date
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

ESTIMATOR_CONTRACT_VERSION = "1.0"
ESTIMATOR_INPUT_CONTRACT_VERSION = "1.1"


class EstimatorContractModel(BaseModel):
    """Strict immutable base for estimator-boundary records."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"


class EstimatorAccountV1(EstimatorContractModel):
    customer_id: str
    account_id: str
    institution_id: str
    currency: str


class EstimatorTransactionV1(EstimatorContractModel):
    transaction_id: str
    customer_id: str
    account_id: str
    posted_at: str
    observed_at: str
    direction: Literal["CREDIT", "DEBIT"]
    amount_minor: int = Field(gt=0)
    currency: str
    description: str
    duplicate_of_transaction_id: str | None = None
    reversal_of_transaction_id: str | None = None

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if self.observed_at < self.posted_at:
            raise ValueError("observed_at must not precede posted_at")
        if (
            self.duplicate_of_transaction_id is not None
            and self.reversal_of_transaction_id is not None
        ):
            raise ValueError("a transaction cannot be both a duplicate and a reversal")
        return self


class EstimatorLoanV1(EstimatorContractModel):
    customer_id: str
    loan_id: str
    disbursement_transaction_id: str


class EstimatorInvestmentTransactionV1(EstimatorContractModel):
    customer_id: str
    investment_transaction_id: str
    transaction_type: Literal["CONTRIBUTION", "REDEMPTION", "RETURN"]
    related_account_transaction_id: str | None = None


class EstimatorCoverageV1(EstimatorContractModel):
    customer_id: str
    account_id: str
    configured_coverage_percent: int = Field(ge=0, le=100)
    eligible_record_count: int = Field(ge=0)
    observed_original_record_count: int = Field(ge=0)
    effective_coverage_basis_points: int = Field(ge=0, le=10_000)


class EstimatorInputV1(EstimatorContractModel):
    """Minimal observation-only input accepted by any integrated estimator."""

    source_contract_schema_version: str
    run_id: str
    customer_id: str
    currency: str
    window_start: str
    window_end: str
    months: int = Field(gt=0, le=1_200)
    accounts: tuple[EstimatorAccountV1, ...]
    transactions: tuple[EstimatorTransactionV1, ...]
    loans: tuple[EstimatorLoanV1, ...] = ()
    investment_transactions: tuple[EstimatorInvestmentTransactionV1, ...] = ()
    coverage: tuple[EstimatorCoverageV1, ...] = ()

    @model_validator(mode="after")
    def validate_customer_scope(self) -> Self:
        records = (
            *self.accounts,
            *self.transactions,
            *self.loans,
            *self.investment_transactions,
            *self.coverage,
        )
        if any(record.customer_id != self.customer_id for record in records):
            raise ValueError("all estimator input records must belong to customer_id")
        account_ids = {record.account_id for record in self.accounts}
        if any(record.account_id not in account_ids for record in self.transactions):
            raise ValueError("transactions must reference an observed account")
        transaction_ids = [record.transaction_id for record in self.transactions]
        if len(transaction_ids) != len(set(transaction_ids)):
            raise ValueError("transaction_id values must be unique")
        if any(record.currency != self.currency for record in self.accounts):
            raise ValueError("account currencies must match input currency")
        if any(record.currency != self.currency for record in self.transactions):
            raise ValueError("transaction currencies must match input currency")
        return self


class EstimatorAccountV11(EstimatorAccountV1):
    schema_version: Literal["1.1"] = "1.1"


class EstimatorTransactionV11(EstimatorTransactionV1):
    schema_version: Literal["1.1"] = "1.1"
    provider_transaction_type: str | None = Field(default=None, min_length=1)
    counterparty_name: str | None = Field(default=None, min_length=1)
    counterparty_document_hash: str | None = Field(
        default=None,
        min_length=16,
        max_length=128,
    )
    balance_after_minor: int | None = None


class EstimatorLoanV11(EstimatorLoanV1):
    schema_version: Literal["1.1"] = "1.1"


class EstimatorInvestmentTransactionV11(EstimatorInvestmentTransactionV1):
    schema_version: Literal["1.1"] = "1.1"


class EstimatorCoverageV11(EstimatorCoverageV1):
    schema_version: Literal["1.1"] = "1.1"


class EstimatorBalanceV11(EstimatorContractModel):
    schema_version: Literal["1.1"] = "1.1"
    balance_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    reference_date: str
    balance_minor: int
    currency: str = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_reference_date(self) -> Self:
        try:
            date.fromisoformat(self.reference_date)
        except ValueError as error:
            raise ValueError(
                "reference_date must be an ISO-8601 calendar date"
            ) from error
        return self


class EstimatorInputV11(EstimatorInputV1):
    """Optional provider context and balances added without private labels."""

    schema_version: Literal["1.1"] = "1.1"
    accounts: tuple[EstimatorAccountV11, ...]
    transactions: tuple[EstimatorTransactionV11, ...]
    loans: tuple[EstimatorLoanV11, ...] = ()
    investment_transactions: tuple[EstimatorInvestmentTransactionV11, ...] = ()
    coverage: tuple[EstimatorCoverageV11, ...] = ()
    balances: tuple[EstimatorBalanceV11, ...] = ()

    @model_validator(mode="after")
    def validate_balances(self) -> Self:
        account_ids = {record.account_id for record in self.accounts}
        if any(record.customer_id != self.customer_id for record in self.balances):
            raise ValueError("all balances must belong to customer_id")
        if any(record.account_id not in account_ids for record in self.balances):
            raise ValueError("balances must reference an observed account")
        if any(record.currency != self.currency for record in self.balances):
            raise ValueError("balance currencies must match input currency")
        balance_ids = [record.balance_id for record in self.balances]
        if len(balance_ids) != len(set(balance_ids)):
            raise ValueError("balance_id values must be unique")
        return self


class MonthlyIncomeEstimateV1(EstimatorContractModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    estimated_income_minor: int = Field(ge=0)
    confidence_lower_minor: int = Field(ge=0)
    confidence_upper_minor: int = Field(ge=0)
    contributing_transaction_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if not (
            self.confidence_lower_minor
            <= self.estimated_income_minor
            <= self.confidence_upper_minor
        ):
            raise ValueError("confidence interval must contain estimated_income_minor")
        if len(self.contributing_transaction_ids) != len(
            set(self.contributing_transaction_ids)
        ):
            raise ValueError("contributing_transaction_ids must be unique")
        return self


class IncomeEstimateV1(EstimatorContractModel):
    """Auditable monthly estimates returned through shared contract 1.0."""

    estimator_version: str = Field(min_length=1)
    run_id: str
    customer_id: str
    currency: str
    monthly_estimates: tuple[MonthlyIncomeEstimateV1, ...]

    @model_validator(mode="after")
    def months_must_be_ordered_and_unique(self) -> Self:
        months = tuple(item.month for item in self.monthly_estimates)
        if months != tuple(sorted(months)) or len(months) != len(set(months)):
            raise ValueError("monthly estimates must be uniquely ordered by month")
        return self


@runtime_checkable
class IncomeEstimator(Protocol):
    """Structural interface; estimator package need not depend on simulator internals."""

    def estimate(self, request: EstimatorInputV1) -> IncomeEstimateV1:
        """Estimate monthly income from observation-only input."""


__all__ = [
    "ESTIMATOR_CONTRACT_VERSION",
    "ESTIMATOR_INPUT_CONTRACT_VERSION",
    "EstimatorAccountV1",
    "EstimatorContractModel",
    "EstimatorCoverageV1",
    "EstimatorInputV1",
    "EstimatorInvestmentTransactionV1",
    "EstimatorLoanV1",
    "EstimatorTransactionV1",
    "EstimatorAccountV11",
    "EstimatorBalanceV11",
    "EstimatorCoverageV11",
    "EstimatorInputV11",
    "EstimatorInvestmentTransactionV11",
    "EstimatorLoanV11",
    "EstimatorTransactionV11",
    "IncomeEstimateV1",
    "IncomeEstimator",
    "MonthlyIncomeEstimateV1",
]
