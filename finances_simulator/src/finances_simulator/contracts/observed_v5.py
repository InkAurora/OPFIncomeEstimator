"""Schema 1.5 estimator-visible contracts for incomplete observations."""

from typing import Literal, Self

from pydantic import Field, model_validator

from finances_simulator.contracts.observed_v4 import (
    AccountV4,
    BalanceV4,
    CardInvoiceItemV4,
    CardInvoiceV4,
    CardTransactionV4,
    CreditCardV4,
    CreditLimitV4,
    InvestmentBalanceV4,
    InvestmentTransactionV4,
    InvestmentV4,
    LoanBalanceV4,
    LoanPaymentV4,
    LoanV4,
    ObservationModelV4,
    TransactionV4,
)


class ObservationModelV5(ObservationModelV4):
    schema_version: Literal["1.5"] = "1.5"


class AccountV5(AccountV4):
    schema_version: Literal["1.5"] = "1.5"


class BalanceV5(BalanceV4):
    schema_version: Literal["1.5"] = "1.5"


class TransactionV5(TransactionV4):
    """One provider record, including traceable duplicate/reversal lineage."""

    schema_version: Literal["1.5"] = "1.5"
    observed_at: str
    duplicate_of_transaction_id: str | None = None
    reversal_of_transaction_id: str | None = None

    @model_validator(mode="after")
    def lineage_must_be_unambiguous(self) -> Self:
        links = (
            self.duplicate_of_transaction_id,
            self.reversal_of_transaction_id,
        )
        if all(link is not None for link in links):
            raise ValueError("a record cannot be both a duplicate and a reversal")
        if self.transaction_id in links:
            raise ValueError("a record cannot reference itself")
        if self.observed_at < self.posted_at:
            raise ValueError("observed_at must not precede posted_at")
        return self


class CreditCardV5(CreditCardV4):
    schema_version: Literal["1.5"] = "1.5"


class CreditLimitV5(CreditLimitV4):
    schema_version: Literal["1.5"] = "1.5"


class CardTransactionV5(CardTransactionV4):
    schema_version: Literal["1.5"] = "1.5"


class CardInvoiceV5(CardInvoiceV4):
    schema_version: Literal["1.5"] = "1.5"


class CardInvoiceItemV5(CardInvoiceItemV4):
    schema_version: Literal["1.5"] = "1.5"


class LoanV5(LoanV4):
    schema_version: Literal["1.5"] = "1.5"


class LoanPaymentV5(LoanPaymentV4):
    schema_version: Literal["1.5"] = "1.5"


class LoanBalanceV5(LoanBalanceV4):
    schema_version: Literal["1.5"] = "1.5"


class InvestmentV5(InvestmentV4):
    schema_version: Literal["1.5"] = "1.5"


class InvestmentTransactionV5(InvestmentTransactionV4):
    schema_version: Literal["1.5"] = "1.5"


class InvestmentBalanceV5(InvestmentBalanceV4):
    schema_version: Literal["1.5"] = "1.5"


class ObservationCoverageV5(ObservationModelV5):
    """Measurable deposit-transaction coverage for one account."""

    coverage_id: str
    customer_id: str
    institution_id: str
    institution_name: str
    account_id: str
    configured_coverage_percent: Literal[100, 70, 40]
    eligible_record_count: int = Field(ge=0)
    consented_record_count: int = Field(ge=0)
    observed_original_record_count: int = Field(ge=0)
    missing_record_count: int = Field(ge=0)
    late_record_count: int = Field(ge=0)
    duplicate_record_count: int = Field(ge=0)
    reversal_record_count: int = Field(ge=0)
    effective_coverage_basis_points: int = Field(ge=0, le=10_000)


__all__ = [
    "AccountV5",
    "BalanceV5",
    "CardInvoiceItemV5",
    "CardInvoiceV5",
    "CardTransactionV5",
    "CreditCardV5",
    "CreditLimitV5",
    "InvestmentBalanceV5",
    "InvestmentTransactionV5",
    "InvestmentV5",
    "LoanBalanceV5",
    "LoanPaymentV5",
    "LoanV5",
    "ObservationCoverageV5",
    "ObservationModelV5",
    "TransactionV5",
]
