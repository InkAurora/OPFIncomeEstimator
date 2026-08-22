"""Estimator input contract 1.2 with observed product data for capacity modeling.

Contract `1.1` exposes deposit accounts, transactions, and balances. The capacity estimator also
needs what the customer owes and owns: card behavior and limits, loan servicing and outstanding
principal, and investment positions. Contract `1.2` adds those product domains as optional
collections, so a consent scope that omits any of them stays valid and is reported as missing
rather than as zero.

Product records carry their provider-visible date rather than an arrival timestamp, matching the
balance records introduced in `1.1`. Point-in-time slicing therefore filters each collection on its
own natural date. Arrival delay for product records is not modeled at this contract version.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from income_estimator.contracts.v1 import EstimatorContractModel, _parse_date
from income_estimator.contracts.v1_1 import (
    EstimatorAccountV11,
    EstimatorBalanceV11,
    EstimatorCoverageV11,
    EstimatorInputV11,
    EstimatorInvestmentTransactionV11,
    EstimatorLoanV11,
    EstimatorTransactionV11,
)

ESTIMATOR_INPUT_CONTRACT_VERSION_1_2 = "1.2"


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
    """Base for observed product records added by contract 1.2."""

    schema_version: Literal["1.2"] = "1.2"
    customer_id: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)


class EstimatorCreditCardV12(EstimatorProductModel):
    card_id: str = Field(min_length=1)
    institution_id: str = Field(min_length=1)
    opened_on: str
    status: Literal["ACTIVE", "CLOSED"] = "ACTIVE"

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        _parse_date(self.opened_on, "opened_on")
        return self


class EstimatorCreditLimitV12(EstimatorProductModel):
    credit_limit_id: str = Field(min_length=1)
    card_id: str = Field(min_length=1)
    reference_date: str
    total_limit_minor: int = Field(gt=0)
    used_limit_minor: int = Field(ge=0)
    available_limit_minor: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_limit(self) -> Self:
        _parse_date(self.reference_date, "reference_date")
        if self.total_limit_minor != self.used_limit_minor + self.available_limit_minor:
            raise ValueError("total_limit_minor must equal used plus available limit")
        return self


class EstimatorCardTransactionV12(EstimatorProductModel):
    card_transaction_id: str = Field(min_length=1)
    card_id: str = Field(min_length=1)
    occurred_at: str
    amount_minor: int = Field(gt=0)
    description: str = Field(min_length=1)
    installment_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_occurred_at(self) -> Self:
        _parse_date(self.occurred_at, "occurred_at")
        return self


class EstimatorCardInvoiceV12(EstimatorProductModel):
    invoice_id: str = Field(min_length=1)
    card_id: str = Field(min_length=1)
    statement_close_date: str
    due_date: str
    amount_due_minor: int = Field(gt=0)
    paid_amount_minor: int = Field(ge=0)
    status: Literal["CLOSED", "PAID"]
    paid_at: str | None = None

    @model_validator(mode="after")
    def validate_invoice(self) -> Self:
        close = _parse_date(self.statement_close_date, "statement_close_date")
        due = _parse_date(self.due_date, "due_date")
        if due <= close:
            raise ValueError("due_date must follow statement_close_date")
        if self.paid_at is not None:
            _parse_date(self.paid_at, "paid_at")
        if self.status == "PAID" and self.paid_amount_minor != self.amount_due_minor:
            raise ValueError("a PAID invoice must be settled in full")
        if self.paid_amount_minor > self.amount_due_minor:
            raise ValueError("paid_amount_minor must not exceed amount_due_minor")
        return self


class EstimatorLoanPaymentV12(EstimatorProductModel):
    loan_payment_id: str = Field(min_length=1)
    loan_id: str = Field(min_length=1)
    installment_number: int = Field(gt=0)
    installment_count: int = Field(gt=0)
    due_date: str
    principal_amount_minor: int = Field(gt=0)
    interest_amount_minor: int = Field(ge=0)
    total_amount_minor: int = Field(gt=0)
    remaining_principal_after_minor: int = Field(ge=0)
    paid_at: str | None = None
    payment_transaction_id: str | None = None

    @model_validator(mode="after")
    def validate_payment(self) -> Self:
        _parse_date(self.due_date, "due_date")
        if self.paid_at is not None:
            _parse_date(self.paid_at, "paid_at")
        if self.installment_number > self.installment_count:
            raise ValueError("installment_number must not exceed installment_count")
        if self.total_amount_minor != (
            self.principal_amount_minor + self.interest_amount_minor
        ):
            raise ValueError("total_amount_minor must equal principal plus interest")
        return self


class EstimatorLoanBalanceV12(EstimatorProductModel):
    loan_balance_id: str = Field(min_length=1)
    loan_id: str = Field(min_length=1)
    reference_date: str
    remaining_principal_minor: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_reference_date(self) -> Self:
        _parse_date(self.reference_date, "reference_date")
        return self


class EstimatorInvestmentV12(EstimatorProductModel):
    investment_id: str = Field(min_length=1)
    institution_id: str = Field(min_length=1)
    opened_on: str
    status: Literal["ACTIVE", "CLOSED"] = "ACTIVE"

    @model_validator(mode="after")
    def validate_opened_on(self) -> Self:
        _parse_date(self.opened_on, "opened_on")
        return self


class EstimatorInvestmentBalanceV12(EstimatorProductModel):
    investment_balance_id: str = Field(min_length=1)
    investment_id: str = Field(min_length=1)
    reference_date: str
    balance_minor: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_reference_date(self) -> Self:
        _parse_date(self.reference_date, "reference_date")
        return self


def _require_unique(values: list[str], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} values must be unique")


class EstimatorInputV12(EstimatorInputV11):
    """Backward-compatible product extension; every new collection stays optional."""

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
    def validate_products(self) -> Self:
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
        _require_unique([record.card_id for record in self.credit_cards], "card_id")
        carded = (*self.credit_limits, *self.card_transactions, *self.card_invoices)
        if any(record.card_id not in card_ids for record in carded):
            raise ValueError("card records must reference an observed credit card")

        loan_ids = {record.loan_id for record in self.loans}
        borrowed = (*self.loan_payments, *self.loan_balances)
        if any(record.loan_id not in loan_ids for record in borrowed):
            raise ValueError("loan records must reference an observed loan")

        investment_ids = {record.investment_id for record in self.investments}
        _require_unique(
            [record.investment_id for record in self.investments],
            "investment_id",
        )
        if any(
            record.investment_id not in investment_ids for record in self.investment_balances
        ):
            raise ValueError("investment balances must reference an observed investment")

        _require_unique(
            [record.credit_limit_id for record in self.credit_limits],
            "credit_limit_id",
        )
        _require_unique(
            [record.card_transaction_id for record in self.card_transactions],
            "card_transaction_id",
        )
        _require_unique([record.invoice_id for record in self.card_invoices], "invoice_id")
        _require_unique(
            [record.loan_payment_id for record in self.loan_payments],
            "loan_payment_id",
        )
        _require_unique(
            [record.loan_balance_id for record in self.loan_balances],
            "loan_balance_id",
        )
        _require_unique(
            [record.investment_balance_id for record in self.investment_balances],
            "investment_balance_id",
        )
        _require_unique([record.loan_id for record in self.loans], "loan_id")
        return self


__all__ = [
    "ESTIMATOR_INPUT_CONTRACT_VERSION_1_2",
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
]
