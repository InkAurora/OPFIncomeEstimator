"""Schema 1.4 estimator-visible contracts without private event labels."""

from typing import Literal

from finances_simulator.contracts.observed_v3 import (
    AccountV3,
    BalanceV3,
    CardInvoiceItemV3,
    CardInvoiceV3,
    CardTransactionV3,
    CreditCardV3,
    CreditLimitV3,
    InvestmentBalanceV3,
    InvestmentTransactionV3,
    InvestmentV3,
    LoanBalanceV3,
    LoanPaymentV3,
    LoanV3,
    ObservationModelV3,
    TransactionV3,
)


class ObservationModelV4(ObservationModelV3):
    schema_version: Literal["1.4"] = "1.4"


class AccountV4(AccountV3):
    schema_version: Literal["1.4"] = "1.4"


class BalanceV4(BalanceV3):
    schema_version: Literal["1.4"] = "1.4"


class TransactionV4(TransactionV3):
    schema_version: Literal["1.4"] = "1.4"


class CreditCardV4(CreditCardV3):
    schema_version: Literal["1.4"] = "1.4"


class CreditLimitV4(CreditLimitV3):
    schema_version: Literal["1.4"] = "1.4"


class CardTransactionV4(CardTransactionV3):
    schema_version: Literal["1.4"] = "1.4"


class CardInvoiceV4(CardInvoiceV3):
    schema_version: Literal["1.4"] = "1.4"


class CardInvoiceItemV4(CardInvoiceItemV3):
    schema_version: Literal["1.4"] = "1.4"


class LoanV4(LoanV3):
    schema_version: Literal["1.4"] = "1.4"


class LoanPaymentV4(LoanPaymentV3):
    schema_version: Literal["1.4"] = "1.4"


class LoanBalanceV4(LoanBalanceV3):
    schema_version: Literal["1.4"] = "1.4"


class InvestmentV4(InvestmentV3):
    schema_version: Literal["1.4"] = "1.4"


class InvestmentTransactionV4(InvestmentTransactionV3):
    schema_version: Literal["1.4"] = "1.4"


class InvestmentBalanceV4(InvestmentBalanceV3):
    schema_version: Literal["1.4"] = "1.4"


__all__ = [
    "AccountV4",
    "BalanceV4",
    "CardInvoiceItemV4",
    "CardInvoiceV4",
    "CardTransactionV4",
    "CreditCardV4",
    "CreditLimitV4",
    "InvestmentBalanceV4",
    "InvestmentTransactionV4",
    "InvestmentV4",
    "LoanBalanceV4",
    "LoanPaymentV4",
    "LoanV4",
    "ObservationModelV4",
    "TransactionV4",
]
