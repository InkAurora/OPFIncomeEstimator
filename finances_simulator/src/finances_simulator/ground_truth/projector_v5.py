"""Project schema-1.5 truth as a field-identical upgrade from V4."""

from dataclasses import dataclass
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from finances_simulator.contracts.ground_truth_v5 import (
    AnomalyGroundTruthV5,
    BalanceSheetGroundTruthV5,
    CardTransactionGroundTruthV5,
    CustomerGroundTruthV5,
    CustomerMonthGroundTruthV5,
    IncomeSourceGroundTruthV5,
    InvestmentTransactionGroundTruthV5,
    LifeEventGroundTruthV5,
    LoanPaymentGroundTruthV5,
    TransactionGroundTruthV5,
)
from finances_simulator.ground_truth.projector_v4 import project_ground_truth_v4
from finances_simulator.simulation.engine import SimulationRun

_RecordV5 = TypeVar("_RecordV5", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class GroundTruthBundleV5:
    """Complete private schema-1.5 datasets."""

    customers: tuple[CustomerGroundTruthV5, ...]
    customer_months: tuple[CustomerMonthGroundTruthV5, ...]
    transactions: tuple[TransactionGroundTruthV5, ...]
    credit_card_transactions: tuple[CardTransactionGroundTruthV5, ...]
    loan_payments: tuple[LoanPaymentGroundTruthV5, ...]
    investment_transactions: tuple[InvestmentTransactionGroundTruthV5, ...]
    balance_sheets: tuple[BalanceSheetGroundTruthV5, ...]
    income_sources: tuple[IncomeSourceGroundTruthV5, ...]
    life_events: tuple[LifeEventGroundTruthV5, ...]
    anomalies: tuple[AnomalyGroundTruthV5, ...]


def _upgrade(record: BaseModel, model: type[_RecordV5]) -> _RecordV5:
    return model.model_validate(record.model_dump(exclude={"schema_version"}))


def project_ground_truth_v5(
    run: SimulationRun,
    *,
    namespace: UUID,
) -> GroundTruthBundleV5:
    """Version frozen V4 truth without applying any observation degradation."""

    base = project_ground_truth_v4(run, namespace=namespace)
    return GroundTruthBundleV5(
        customers=tuple(_upgrade(item, CustomerGroundTruthV5) for item in base.customers),
        customer_months=tuple(
            _upgrade(item, CustomerMonthGroundTruthV5) for item in base.customer_months
        ),
        transactions=tuple(
            _upgrade(item, TransactionGroundTruthV5) for item in base.transactions
        ),
        credit_card_transactions=tuple(
            _upgrade(item, CardTransactionGroundTruthV5)
            for item in base.credit_card_transactions
        ),
        loan_payments=tuple(
            _upgrade(item, LoanPaymentGroundTruthV5) for item in base.loan_payments
        ),
        investment_transactions=tuple(
            _upgrade(item, InvestmentTransactionGroundTruthV5)
            for item in base.investment_transactions
        ),
        balance_sheets=tuple(
            _upgrade(item, BalanceSheetGroundTruthV5) for item in base.balance_sheets
        ),
        income_sources=tuple(
            _upgrade(item, IncomeSourceGroundTruthV5) for item in base.income_sources
        ),
        life_events=tuple(
            _upgrade(item, LifeEventGroundTruthV5) for item in base.life_events
        ),
        anomalies=tuple(_upgrade(item, AnomalyGroundTruthV5) for item in base.anomalies),
    )


__all__ = ["GroundTruthBundleV5", "project_ground_truth_v5"]
