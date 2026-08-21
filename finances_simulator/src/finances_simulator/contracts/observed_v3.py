"""Schema 1.3 project-owned observation contracts."""

from typing import Literal

from finances_simulator.contracts.observed_v2 import (
    AccountV2,
    BalanceV2,
    CardInvoiceItemV2,
    CardInvoiceV2,
    CardTransactionV2,
    CreditCardV2,
    CreditLimitV2,
    InvestmentBalanceV2,
    InvestmentTransactionV2,
    InvestmentV2,
    LoanBalanceV2,
    LoanPaymentV2,
    LoanV2,
    ObservationModelV2,
    TransactionV2,
)


class ObservationModelV3(ObservationModelV2):
    """Strict immutable base for schema 1.3 estimator-visible records."""

    schema_version: Literal["1.3"] = "1.3"


class AccountV3(AccountV2):
    schema_version: Literal["1.3"] = "1.3"


class BalanceV3(BalanceV2):
    schema_version: Literal["1.3"] = "1.3"


class TransactionV3(TransactionV2):
    schema_version: Literal["1.3"] = "1.3"


class CreditCardV3(CreditCardV2):
    schema_version: Literal["1.3"] = "1.3"


class CreditLimitV3(CreditLimitV2):
    schema_version: Literal["1.3"] = "1.3"


class CardTransactionV3(CardTransactionV2):
    schema_version: Literal["1.3"] = "1.3"


class CardInvoiceV3(CardInvoiceV2):
    schema_version: Literal["1.3"] = "1.3"


class CardInvoiceItemV3(CardInvoiceItemV2):
    schema_version: Literal["1.3"] = "1.3"


class LoanV3(LoanV2):
    schema_version: Literal["1.3"] = "1.3"


class LoanPaymentV3(LoanPaymentV2):
    schema_version: Literal["1.3"] = "1.3"


class LoanBalanceV3(LoanBalanceV2):
    schema_version: Literal["1.3"] = "1.3"


class InvestmentV3(InvestmentV2):
    schema_version: Literal["1.3"] = "1.3"


class InvestmentTransactionV3(InvestmentTransactionV2):
    schema_version: Literal["1.3"] = "1.3"


class InvestmentBalanceV3(InvestmentBalanceV2):
    schema_version: Literal["1.3"] = "1.3"


__all__ = [
    "AccountV3",
    "BalanceV3",
    "CardInvoiceItemV3",
    "CardInvoiceV3",
    "CardTransactionV3",
    "CreditCardV3",
    "CreditLimitV3",
    "InvestmentBalanceV3",
    "InvestmentTransactionV3",
    "InvestmentV3",
    "LoanBalanceV3",
    "LoanPaymentV3",
    "LoanV3",
    "ObservationModelV3",
    "TransactionV3",
]
