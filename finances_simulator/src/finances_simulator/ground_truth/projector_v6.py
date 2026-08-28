"""Project schema-1.6 truth as a field-identical upgrade from V5."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel

from finances_simulator.contracts.ground_truth_v6 import (
    AnomalyGroundTruthV6,
    BalanceSheetGroundTruthV6,
    CardTransactionGroundTruthV6,
    CustomerGroundTruthV6,
    CustomerMonthGroundTruthV6,
    IncomeSourceGroundTruthV6,
    InvestmentTransactionGroundTruthV6,
    LifeEventGroundTruthV6,
    LoanPaymentGroundTruthV6,
    TransactionGroundTruthV6,
)
from finances_simulator.ground_truth.projector_v5 import project_ground_truth_v5
from finances_simulator.simulation.engine import SimulationRun


@dataclass(frozen=True, slots=True)
class GroundTruthBundleV6:
    """Complete private schema-1.6 datasets."""

    customers: tuple[CustomerGroundTruthV6, ...]
    customer_months: tuple[CustomerMonthGroundTruthV6, ...]
    transactions: tuple[TransactionGroundTruthV6, ...]
    credit_card_transactions: tuple[CardTransactionGroundTruthV6, ...]
    loan_payments: tuple[LoanPaymentGroundTruthV6, ...]
    investment_transactions: tuple[InvestmentTransactionGroundTruthV6, ...]
    balance_sheets: tuple[BalanceSheetGroundTruthV6, ...]
    income_sources: tuple[IncomeSourceGroundTruthV6, ...]
    life_events: tuple[LifeEventGroundTruthV6, ...]
    anomalies: tuple[AnomalyGroundTruthV6, ...]


def _upgrade[RecordV6: BaseModel](record: BaseModel, model: type[RecordV6]) -> RecordV6:
    return model.model_validate(record.model_dump(exclude={"schema_version"}))


def project_ground_truth_v6(
    run: SimulationRun,
    *,
    namespace: UUID,
) -> GroundTruthBundleV6:
    """Version frozen V5 truth; corrected re-posts are an observed-layer concern only."""

    base = project_ground_truth_v5(run, namespace=namespace)
    return GroundTruthBundleV6(
        customers=tuple(_upgrade(item, CustomerGroundTruthV6) for item in base.customers),
        customer_months=tuple(
            _upgrade(item, CustomerMonthGroundTruthV6) for item in base.customer_months
        ),
        transactions=tuple(
            _upgrade(item, TransactionGroundTruthV6) for item in base.transactions
        ),
        credit_card_transactions=tuple(
            _upgrade(item, CardTransactionGroundTruthV6)
            for item in base.credit_card_transactions
        ),
        loan_payments=tuple(
            _upgrade(item, LoanPaymentGroundTruthV6) for item in base.loan_payments
        ),
        investment_transactions=tuple(
            _upgrade(item, InvestmentTransactionGroundTruthV6)
            for item in base.investment_transactions
        ),
        balance_sheets=tuple(
            _upgrade(item, BalanceSheetGroundTruthV6) for item in base.balance_sheets
        ),
        income_sources=tuple(
            _upgrade(item, IncomeSourceGroundTruthV6) for item in base.income_sources
        ),
        life_events=tuple(
            _upgrade(item, LifeEventGroundTruthV6) for item in base.life_events
        ),
        anomalies=tuple(_upgrade(item, AnomalyGroundTruthV6) for item in base.anomalies),
    )


__all__ = ["GroundTruthBundleV6", "project_ground_truth_v6"]
