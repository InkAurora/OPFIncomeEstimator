"""Shared, versioned boundary between simulator and income estimator."""

from __future__ import annotations

from datetime import date
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

ESTIMATOR_CONTRACT_VERSION = "1.0"
ESTIMATOR_INPUT_CONTRACT_VERSION = "1.1"
ESTIMATOR_INPUT_CONTRACT_VERSION_1_2 = "1.2"


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
    repost_of_transaction_id: str | None = None

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if self.observed_at < self.posted_at:
            raise ValueError("observed_at must not precede posted_at")
        links = (
            self.duplicate_of_transaction_id,
            self.reversal_of_transaction_id,
            self.repost_of_transaction_id,
        )
        if sum(link is not None for link in links) > 1:
            raise ValueError(
                "a transaction may carry at most one of duplicate, reversal, or re-post lineage"
            )
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


class EstimatorAccountV12(EstimatorAccountV11):
    schema_version: Literal["1.2"] = "1.2"


class EstimatorTransactionV12(EstimatorTransactionV11):
    schema_version: Literal["1.2"] = "1.2"


class EstimatorLoanV12(EstimatorLoanV11):
    schema_version: Literal["1.2"] = "1.2"


class EstimatorInvestmentTransactionV12(EstimatorInvestmentTransactionV11):
    schema_version: Literal["1.2"] = "1.2"


class EstimatorCoverageV12(EstimatorCoverageV11):
    schema_version: Literal["1.2"] = "1.2"


class EstimatorBalanceV12(EstimatorBalanceV11):
    schema_version: Literal["1.2"] = "1.2"


class EstimatorProductModel(EstimatorContractModel):
    """Observed product record added by estimator input 1.2."""

    schema_version: Literal["1.2"] = "1.2"
    customer_id: str
    currency: str


class EstimatorCreditCardV12(EstimatorProductModel):
    card_id: str
    institution_id: str
    opened_on: str
    status: Literal["ACTIVE", "CLOSED"] = "ACTIVE"


class EstimatorCreditLimitV12(EstimatorProductModel):
    credit_limit_id: str
    card_id: str
    reference_date: str
    total_limit_minor: int = Field(gt=0)
    used_limit_minor: int = Field(ge=0)
    available_limit_minor: int = Field(ge=0)


class EstimatorCardTransactionV12(EstimatorProductModel):
    card_transaction_id: str
    card_id: str
    occurred_at: str
    amount_minor: int = Field(gt=0)
    description: str
    installment_count: int = Field(gt=0)


class EstimatorCardInvoiceV12(EstimatorProductModel):
    invoice_id: str
    card_id: str
    statement_close_date: str
    due_date: str
    amount_due_minor: int = Field(gt=0)
    paid_amount_minor: int = Field(ge=0)
    status: Literal["CLOSED", "PAID"]
    paid_at: str | None = None


class EstimatorLoanPaymentV12(EstimatorProductModel):
    loan_payment_id: str
    loan_id: str
    installment_number: int = Field(gt=0)
    installment_count: int = Field(gt=0)
    due_date: str
    principal_amount_minor: int = Field(gt=0)
    interest_amount_minor: int = Field(ge=0)
    total_amount_minor: int = Field(gt=0)
    remaining_principal_after_minor: int = Field(ge=0)
    paid_at: str | None = None
    payment_transaction_id: str | None = None


class EstimatorLoanBalanceV12(EstimatorProductModel):
    loan_balance_id: str
    loan_id: str
    reference_date: str
    remaining_principal_minor: int = Field(ge=0)


class EstimatorInvestmentV12(EstimatorProductModel):
    investment_id: str
    institution_id: str
    opened_on: str
    status: Literal["ACTIVE", "CLOSED"] = "ACTIVE"


class EstimatorInvestmentBalanceV12(EstimatorProductModel):
    investment_balance_id: str
    investment_id: str
    reference_date: str
    balance_minor: int = Field(ge=0)


class EstimatorInputV12(EstimatorInputV11):
    """Observed product data for capacity modeling; every collection stays optional."""

    schema_version: Literal["1.2"] = "1.2"
    accounts: tuple[EstimatorAccountV12, ...]
    transactions: tuple[EstimatorTransactionV12, ...]
    loans: tuple[EstimatorLoanV12, ...] = ()
    investment_transactions: tuple[EstimatorInvestmentTransactionV12, ...] = ()
    coverage: tuple[EstimatorCoverageV12, ...] = ()
    balances: tuple[EstimatorBalanceV12, ...] = ()
    credit_cards: tuple[EstimatorCreditCardV12, ...] = ()
    credit_limits: tuple[EstimatorCreditLimitV12, ...] = ()
    card_transactions: tuple[EstimatorCardTransactionV12, ...] = ()
    card_invoices: tuple[EstimatorCardInvoiceV12, ...] = ()
    loan_payments: tuple[EstimatorLoanPaymentV12, ...] = ()
    loan_balances: tuple[EstimatorLoanBalanceV12, ...] = ()
    investments: tuple[EstimatorInvestmentV12, ...] = ()
    investment_balances: tuple[EstimatorInvestmentBalanceV12, ...] = ()

    @model_validator(mode="after")
    def validate_product_scope(self) -> Self:
        products = (
            *self.credit_cards,
            *self.credit_limits,
            *self.card_transactions,
            *self.card_invoices,
            *self.loan_payments,
            *self.loan_balances,
            *self.investments,
            *self.investment_balances,
        )
        if any(record.customer_id != self.customer_id for record in products):
            raise ValueError("all product records must belong to customer_id")
        if any(record.currency != self.currency for record in products):
            raise ValueError("product currencies must match input currency")
        card_ids = {record.card_id for record in self.credit_cards}
        carded = (*self.credit_limits, *self.card_transactions, *self.card_invoices)
        if any(record.card_id not in card_ids for record in carded):
            raise ValueError("card records must reference an observed credit card")
        loan_ids = {record.loan_id for record in self.loans}
        if any(
            record.loan_id not in loan_ids
            for record in (*self.loan_payments, *self.loan_balances)
        ):
            raise ValueError("loan records must reference an observed loan")
        investment_ids = {record.investment_id for record in self.investments}
        if any(
            record.investment_id not in investment_ids
            for record in self.investment_balances
        ):
            raise ValueError("investment balances must reference an observed investment")
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
    "ESTIMATOR_INPUT_CONTRACT_VERSION_1_2",
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
    "EstimatorAccountV12",
    "EstimatorBalanceV12",
    "EstimatorCardInvoiceV12",
    "EstimatorCardTransactionV12",
    "EstimatorCoverageV12",
    "EstimatorCreditCardV12",
    "EstimatorCreditLimitV12",
    "EstimatorInputV12",
    "EstimatorInvestmentBalanceV12",
    "EstimatorInvestmentTransactionV12",
    "EstimatorInvestmentV12",
    "EstimatorLoanBalanceV12",
    "EstimatorLoanPaymentV12",
    "EstimatorLoanV12",
    "EstimatorTransactionV12",
    "IncomeEstimateV1",
    "IncomeEstimator",
    "MonthlyIncomeEstimateV1",
]
