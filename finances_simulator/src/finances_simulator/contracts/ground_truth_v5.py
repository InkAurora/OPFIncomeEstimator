"""Schema 1.5 private contracts; Phase-6 degradation never alters truth."""

from typing import Literal

from finances_simulator.contracts.ground_truth_v4 import (
    AnomalyGroundTruthV4,
    BalanceSheetGroundTruthV4,
    CardTransactionGroundTruthV4,
    CustomerGroundTruthV4,
    CustomerMonthGroundTruthV4,
    GroundTruthModelV4,
    IncomeSourceGroundTruthV4,
    InvestmentTransactionGroundTruthV4,
    LifeEventGroundTruthV4,
    LoanPaymentGroundTruthV4,
    TransactionGroundTruthV4,
)


class GroundTruthModelV5(GroundTruthModelV4):
    schema_version: Literal["1.5"] = "1.5"


class CustomerGroundTruthV5(CustomerGroundTruthV4):
    schema_version: Literal["1.5"] = "1.5"


class CustomerMonthGroundTruthV5(CustomerMonthGroundTruthV4):
    schema_version: Literal["1.5"] = "1.5"


class TransactionGroundTruthV5(TransactionGroundTruthV4):
    schema_version: Literal["1.5"] = "1.5"


class CardTransactionGroundTruthV5(CardTransactionGroundTruthV4):
    schema_version: Literal["1.5"] = "1.5"


class LoanPaymentGroundTruthV5(LoanPaymentGroundTruthV4):
    schema_version: Literal["1.5"] = "1.5"


class InvestmentTransactionGroundTruthV5(InvestmentTransactionGroundTruthV4):
    schema_version: Literal["1.5"] = "1.5"


class BalanceSheetGroundTruthV5(BalanceSheetGroundTruthV4):
    schema_version: Literal["1.5"] = "1.5"


class IncomeSourceGroundTruthV5(IncomeSourceGroundTruthV4):
    schema_version: Literal["1.5"] = "1.5"


class LifeEventGroundTruthV5(LifeEventGroundTruthV4):
    schema_version: Literal["1.5"] = "1.5"


class AnomalyGroundTruthV5(AnomalyGroundTruthV4):
    schema_version: Literal["1.5"] = "1.5"


__all__ = [
    "AnomalyGroundTruthV5",
    "BalanceSheetGroundTruthV5",
    "CardTransactionGroundTruthV5",
    "CustomerGroundTruthV5",
    "CustomerMonthGroundTruthV5",
    "GroundTruthModelV5",
    "IncomeSourceGroundTruthV5",
    "InvestmentTransactionGroundTruthV5",
    "LifeEventGroundTruthV5",
    "LoanPaymentGroundTruthV5",
    "TransactionGroundTruthV5",
]
