"""Schema 1.6 private contracts; corrected re-posts never alter truth.

ADR 0004 keeps the reversal an observation artifact, so schema 1.6 changes the observed layer only
and versions private truth without touching a field.
"""

from typing import Literal

from finances_simulator.contracts.ground_truth_v5 import (
    AnomalyGroundTruthV5,
    BalanceSheetGroundTruthV5,
    CardTransactionGroundTruthV5,
    CustomerGroundTruthV5,
    CustomerMonthGroundTruthV5,
    GroundTruthModelV5,
    IncomeSourceGroundTruthV5,
    InvestmentTransactionGroundTruthV5,
    LifeEventGroundTruthV5,
    LoanPaymentGroundTruthV5,
    TransactionGroundTruthV5,
)


class GroundTruthModelV6(GroundTruthModelV5):
    schema_version: Literal["1.6"] = "1.6"


class CustomerGroundTruthV6(CustomerGroundTruthV5):
    schema_version: Literal["1.6"] = "1.6"


class CustomerMonthGroundTruthV6(CustomerMonthGroundTruthV5):
    schema_version: Literal["1.6"] = "1.6"


class TransactionGroundTruthV6(TransactionGroundTruthV5):
    schema_version: Literal["1.6"] = "1.6"


class CardTransactionGroundTruthV6(CardTransactionGroundTruthV5):
    schema_version: Literal["1.6"] = "1.6"


class LoanPaymentGroundTruthV6(LoanPaymentGroundTruthV5):
    schema_version: Literal["1.6"] = "1.6"


class InvestmentTransactionGroundTruthV6(InvestmentTransactionGroundTruthV5):
    schema_version: Literal["1.6"] = "1.6"


class BalanceSheetGroundTruthV6(BalanceSheetGroundTruthV5):
    schema_version: Literal["1.6"] = "1.6"


class IncomeSourceGroundTruthV6(IncomeSourceGroundTruthV5):
    schema_version: Literal["1.6"] = "1.6"


class LifeEventGroundTruthV6(LifeEventGroundTruthV5):
    schema_version: Literal["1.6"] = "1.6"


class AnomalyGroundTruthV6(AnomalyGroundTruthV5):
    schema_version: Literal["1.6"] = "1.6"


__all__ = [
    "AnomalyGroundTruthV6",
    "BalanceSheetGroundTruthV6",
    "CardTransactionGroundTruthV6",
    "CustomerGroundTruthV6",
    "CustomerMonthGroundTruthV6",
    "GroundTruthModelV6",
    "IncomeSourceGroundTruthV6",
    "InvestmentTransactionGroundTruthV6",
    "LifeEventGroundTruthV6",
    "LoanPaymentGroundTruthV6",
    "TransactionGroundTruthV6",
]
