"""Schema 1.2 project-owned observation contracts."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finances_simulator.observations.contracts_v1 import (
    AccountV1,
    BalanceV1,
    CardInvoiceItemV1,
    CardInvoiceV1,
    CardTransactionV1,
    CreditCardV1,
    CreditLimitV1,
    TransactionV1,
)


class ObservationModelV2(BaseModel):
    """Strict immutable base for schema 1.2 estimator-visible records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.2"] = "1.2"


class AccountV2(AccountV1):
    schema_version: Literal["1.2"] = "1.2"


class BalanceV2(BalanceV1):
    schema_version: Literal["1.2"] = "1.2"


class TransactionV2(TransactionV1):
    schema_version: Literal["1.2"] = "1.2"


class CreditCardV2(CreditCardV1):
    schema_version: Literal["1.2"] = "1.2"


class CreditLimitV2(CreditLimitV1):
    schema_version: Literal["1.2"] = "1.2"


class CardTransactionV2(CardTransactionV1):
    schema_version: Literal["1.2"] = "1.2"


class CardInvoiceV2(CardInvoiceV1):
    schema_version: Literal["1.2"] = "1.2"


class CardInvoiceItemV2(CardInvoiceItemV1):
    schema_version: Literal["1.2"] = "1.2"


class LoanV2(ObservationModelV2):
    """Observed originated loan contract."""

    loan_id: str
    customer_id: str
    institution_id: str
    institution_name: str
    loan_label: str
    loan_type: Literal["PERSONAL"]
    currency: str
    originated_at: str
    original_principal_minor: int = Field(gt=0)
    annual_interest_basis_points: int = Field(ge=1, le=10_000)
    term_months: int = Field(ge=1, le=480)
    amortization_system: Literal["CONSTANT_PRINCIPAL"]
    status: Literal["ACTIVE", "PAID_OFF"]
    disbursement_transaction_id: str

    @model_validator(mode="after")
    def principal_must_cover_term(self) -> Self:
        if self.original_principal_minor < self.term_months:
            raise ValueError(
                "original_principal_minor must be greater than or equal to term_months"
            )
        return self


class LoanPaymentV2(ObservationModelV2):
    """Observed fully paid loan installment within the simulation window."""

    loan_payment_id: str
    customer_id: str
    loan_id: str
    installment_number: int = Field(gt=0)
    installment_count: int = Field(gt=0)
    due_date: str
    principal_amount_minor: int = Field(gt=0)
    interest_amount_minor: int = Field(ge=0)
    total_amount_minor: int = Field(gt=0)
    paid_amount_minor: int = Field(gt=0)
    remaining_principal_after_minor: int = Field(ge=0)
    currency: str
    status: Literal["PAID"] = "PAID"
    paid_at: str
    payment_transaction_id: str

    @model_validator(mode="after")
    def payment_must_reconcile(self) -> Self:
        if self.installment_number > self.installment_count:
            raise ValueError("installment_number must not exceed installment_count")
        if self.total_amount_minor != (self.principal_amount_minor + self.interest_amount_minor):
            raise ValueError(
                "total_amount_minor must equal principal_amount_minor + interest_amount_minor"
            )
        if self.paid_amount_minor != self.total_amount_minor:
            raise ValueError("paid_amount_minor must equal total_amount_minor")
        if self.paid_at != self.due_date:
            raise ValueError("paid_at must equal due_date")
        return self


class LoanBalanceV2(ObservationModelV2):
    """Observed month-end remaining principal."""

    loan_balance_id: str
    customer_id: str
    loan_id: str
    reference_date: str
    remaining_principal_minor: int = Field(ge=0)
    currency: str
    balance_type: Literal["CLOSING"] = "CLOSING"


class InvestmentV2(ObservationModelV2):
    """Observed fixed-income investment product."""

    investment_id: str
    customer_id: str
    institution_id: str
    institution_name: str
    investment_label: str
    investment_type: Literal["FIXED_INCOME"]
    currency: str
    opened_on: str
    status: Literal["ACTIVE"] = "ACTIVE"


class InvestmentTransactionV2(ObservationModelV2):
    """Observed accepted investment flow or credited return."""

    investment_transaction_id: str
    customer_id: str
    investment_id: str
    occurred_at: str
    transaction_type: Literal["CONTRIBUTION", "REDEMPTION", "RETURN"]
    amount_minor: int = Field(gt=0)
    currency: str
    description: str
    balance_after_minor: int = Field(ge=0)
    related_account_transaction_id: str | None = None

    @model_validator(mode="after")
    def account_link_must_match_type(self) -> Self:
        if self.transaction_type == "RETURN":
            if self.related_account_transaction_id is not None:
                raise ValueError("RETURN cannot reference an account transaction")
        elif self.related_account_transaction_id is None:
            raise ValueError("external investment flow requires account transaction reference")
        return self


class InvestmentBalanceV2(ObservationModelV2):
    """Observed month-end investment valuation."""

    investment_balance_id: str
    customer_id: str
    investment_id: str
    reference_date: str
    balance_minor: int = Field(ge=0)
    currency: str
    balance_type: Literal["CLOSING"] = "CLOSING"


__all__ = [
    "AccountV2",
    "BalanceV2",
    "CardInvoiceItemV2",
    "CardInvoiceV2",
    "CardTransactionV2",
    "CreditCardV2",
    "CreditLimitV2",
    "InvestmentBalanceV2",
    "InvestmentTransactionV2",
    "InvestmentV2",
    "LoanBalanceV2",
    "LoanPaymentV2",
    "LoanV2",
    "ObservationModelV2",
    "TransactionV2",
]
