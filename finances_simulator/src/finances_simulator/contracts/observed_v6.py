"""Schema 1.6 estimator-visible contracts adding corrected re-post lineage."""

from typing import Literal, Self

from pydantic import Field, model_validator

from finances_simulator.contracts.observed_v5 import (
    AccountV5,
    BalanceV5,
    CardInvoiceItemV5,
    CardInvoiceV5,
    CardTransactionV5,
    CreditCardV5,
    CreditLimitV5,
    InvestmentBalanceV5,
    InvestmentTransactionV5,
    InvestmentV5,
    LoanBalanceV5,
    LoanPaymentV5,
    LoanV5,
    ObservationModelV5,
    TransactionV5,
)


class ObservationModelV6(ObservationModelV5):
    schema_version: Literal["1.6"] = "1.6"


class AccountV6(AccountV5):
    schema_version: Literal["1.6"] = "1.6"


class BalanceV6(BalanceV5):
    schema_version: Literal["1.6"] = "1.6"


class TransactionV6(TransactionV5):
    """One provider record, including duplicate, reversal, and re-post lineage.

    A reversal in this schema is always an observation artifact. ADR 0004 requires every artifact
    reversal to be followed by a corrected re-post, so the observed feed reconverges to truth
    instead of losing the amount permanently.
    """

    schema_version: Literal["1.6"] = "1.6"
    repost_of_transaction_id: str | None = None

    @model_validator(mode="after")
    def lineage_must_be_unambiguous(self) -> Self:
        links = (
            self.duplicate_of_transaction_id,
            self.reversal_of_transaction_id,
            self.repost_of_transaction_id,
        )
        if sum(link is not None for link in links) > 1:
            raise ValueError(
                "a record may carry at most one of duplicate, reversal, or re-post lineage"
            )
        if self.transaction_id in links:
            raise ValueError("a record cannot reference itself")
        if self.observed_at < self.posted_at:
            raise ValueError("observed_at must not precede posted_at")
        return self


class CreditCardV6(CreditCardV5):
    schema_version: Literal["1.6"] = "1.6"


class CreditLimitV6(CreditLimitV5):
    schema_version: Literal["1.6"] = "1.6"


class CardTransactionV6(CardTransactionV5):
    schema_version: Literal["1.6"] = "1.6"


class CardInvoiceV6(CardInvoiceV5):
    schema_version: Literal["1.6"] = "1.6"


class CardInvoiceItemV6(CardInvoiceItemV5):
    schema_version: Literal["1.6"] = "1.6"


class LoanV6(LoanV5):
    schema_version: Literal["1.6"] = "1.6"


class LoanPaymentV6(LoanPaymentV5):
    schema_version: Literal["1.6"] = "1.6"


class LoanBalanceV6(LoanBalanceV5):
    schema_version: Literal["1.6"] = "1.6"


class InvestmentV6(InvestmentV5):
    schema_version: Literal["1.6"] = "1.6"


class InvestmentTransactionV6(InvestmentTransactionV5):
    schema_version: Literal["1.6"] = "1.6"


class InvestmentBalanceV6(InvestmentBalanceV5):
    schema_version: Literal["1.6"] = "1.6"


class ObservationCoverageV6(ObservationModelV6):
    """Measurable deposit-transaction coverage for one account.

    `repost_record_count` is reported separately so a correction can never be mistaken for an
    observed original, which would overstate effective coverage.
    """

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
    repost_record_count: int = Field(ge=0)
    effective_coverage_basis_points: int = Field(ge=0, le=10_000)

    @model_validator(mode="after")
    def every_reversal_is_corrected(self) -> Self:
        if self.repost_record_count != self.reversal_record_count:
            raise ValueError("each reversal record requires exactly one corrected re-post")
        return self


__all__ = [
    "AccountV6",
    "BalanceV6",
    "CardInvoiceItemV6",
    "CardInvoiceV6",
    "CardTransactionV6",
    "CreditCardV6",
    "CreditLimitV6",
    "InvestmentBalanceV6",
    "InvestmentTransactionV6",
    "InvestmentV6",
    "LoanBalanceV6",
    "LoanPaymentV6",
    "LoanV6",
    "ObservationCoverageV6",
    "ObservationModelV6",
    "TransactionV6",
]
