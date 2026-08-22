"""Estimator input/output contract 1.0, owned by estimator runtime."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ESTIMATOR_CONTRACT_VERSION = "1.0"


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO-8601 calendar date") from error


class EstimatorContractModel(BaseModel):
    """Strict immutable base for observation-boundary records."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"


class EstimatorAccountV1(EstimatorContractModel):
    customer_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    institution_id: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)


class EstimatorTransactionV1(EstimatorContractModel):
    transaction_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    posted_at: str
    observed_at: str
    direction: Literal["CREDIT", "DEBIT"]
    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    description: str = Field(min_length=1)
    duplicate_of_transaction_id: str | None = None
    reversal_of_transaction_id: str | None = None

    @model_validator(mode="after")
    def validate_dates_and_lineage(self) -> Self:
        posted_at = _parse_date(self.posted_at, "posted_at")
        observed_at = _parse_date(self.observed_at, "observed_at")
        if observed_at < posted_at:
            raise ValueError("observed_at must not precede posted_at")
        if (
            self.duplicate_of_transaction_id is not None
            and self.reversal_of_transaction_id is not None
        ):
            raise ValueError("a transaction cannot be both a duplicate and a reversal")
        return self


class EstimatorLoanV1(EstimatorContractModel):
    customer_id: str = Field(min_length=1)
    loan_id: str = Field(min_length=1)
    disbursement_transaction_id: str = Field(min_length=1)


class EstimatorInvestmentTransactionV1(EstimatorContractModel):
    customer_id: str = Field(min_length=1)
    investment_transaction_id: str = Field(min_length=1)
    transaction_type: Literal["CONTRIBUTION", "REDEMPTION", "RETURN"]
    related_account_transaction_id: str | None = None


class EstimatorCoverageV1(EstimatorContractModel):
    customer_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    configured_coverage_percent: int = Field(ge=0, le=100)
    eligible_record_count: int = Field(ge=0)
    observed_original_record_count: int = Field(ge=0)
    effective_coverage_basis_points: int = Field(ge=0, le=10_000)


class EstimatorInputV1(EstimatorContractModel):
    """Observation-only request accepted by estimator 0.1."""

    source_contract_schema_version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    window_start: str
    window_end: str
    months: int = Field(gt=0, le=1_200)
    accounts: tuple[EstimatorAccountV1, ...]
    transactions: tuple[EstimatorTransactionV1, ...]
    loans: tuple[EstimatorLoanV1, ...] = ()
    investment_transactions: tuple[EstimatorInvestmentTransactionV1, ...] = ()
    coverage: tuple[EstimatorCoverageV1, ...] = ()

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        window_start = _parse_date(self.window_start, "window_start")
        window_end = _parse_date(self.window_end, "window_end")
        if window_end < window_start:
            raise ValueError("window_end must not precede window_start")

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
        if len(account_ids) != len(self.accounts):
            raise ValueError("account_id values must be unique")
        if any(record.account_id not in account_ids for record in self.transactions):
            raise ValueError("transactions must reference an observed account")
        if any(record.account_id not in account_ids for record in self.coverage):
            raise ValueError("coverage must reference an observed account")
        transaction_ids = [record.transaction_id for record in self.transactions]
        if len(transaction_ids) != len(set(transaction_ids)):
            raise ValueError("transaction_id values must be unique")
        if any(record.currency != self.currency for record in self.accounts):
            raise ValueError("account currencies must match input currency")
        if any(record.currency != self.currency for record in self.transactions):
            raise ValueError("transaction currencies must match input currency")
        return self


class MonthlyIncomeEstimateV1(EstimatorContractModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    estimated_income_minor: int = Field(ge=0)
    confidence_lower_minor: int = Field(ge=0)
    confidence_upper_minor: int = Field(ge=0)
    contributing_transaction_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_estimate(self) -> Self:
        try:
            date.fromisoformat(f"{self.month}-01")
        except ValueError as error:
            raise ValueError("month must identify a valid calendar month") from error
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
    estimator_version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    monthly_estimates: tuple[MonthlyIncomeEstimateV1, ...]

    @model_validator(mode="after")
    def validate_month_order(self) -> Self:
        months = tuple(item.month for item in self.monthly_estimates)
        if months != tuple(sorted(months)) or len(months) != len(set(months)):
            raise ValueError("monthly estimates must be uniquely ordered by month")
        return self


def validate_estimator_input(request: Any) -> EstimatorInputV1:
    """Validate local, mapping, or structurally compatible external requests."""

    if isinstance(request, EstimatorInputV1):
        return request
    if isinstance(request, Mapping):
        payload = request
    else:
        dump = getattr(request, "model_dump", None)
        if not callable(dump):
            raise TypeError("request must be a mapping or expose model_dump()")
        payload = dump(mode="python")
    schema_version = payload.get("schema_version", "1.0")
    if schema_version == "1.2":
        from income_estimator.contracts.v1_2 import EstimatorInputV12

        return EstimatorInputV12.model_validate(payload)
    if schema_version == "1.1":
        from income_estimator.contracts.v1_1 import EstimatorInputV11

        return EstimatorInputV11.model_validate(payload)
    return EstimatorInputV1.model_validate(payload)


__all__ = [
    "ESTIMATOR_CONTRACT_VERSION",
    "EstimatorAccountV1",
    "EstimatorCoverageV1",
    "EstimatorInputV1",
    "EstimatorInvestmentTransactionV1",
    "EstimatorLoanV1",
    "EstimatorTransactionV1",
    "IncomeEstimateV1",
    "MonthlyIncomeEstimateV1",
    "validate_estimator_input",
]
