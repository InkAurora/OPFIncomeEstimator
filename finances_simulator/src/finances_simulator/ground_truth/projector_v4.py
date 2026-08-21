"""Project schema-1.4 life-event and anomaly ground truth."""

from dataclasses import dataclass
from datetime import date
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from finances_simulator.contracts.ground_truth_v4 import (
    AnomalyGroundTruthV4,
    BalanceSheetGroundTruthV4,
    CardTransactionGroundTruthV4,
    CustomerGroundTruthV4,
    CustomerMonthGroundTruthV4,
    IncomeSourceGroundTruthV4,
    InvestmentTransactionGroundTruthV4,
    LifeEventGroundTruthV4,
    LoanPaymentGroundTruthV4,
    TransactionGroundTruthV4,
)
from finances_simulator.domain.customer import CustomerTwinV4
from finances_simulator.domain.events import EconomicType
from finances_simulator.domain.life_events import IncomeSourceState
from finances_simulator.ground_truth.projector_v3 import project_ground_truth_v3
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import month_end

_RecordV4 = TypeVar("_RecordV4", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class GroundTruthBundleV4:
    """Complete private schema-1.4 datasets."""

    customers: tuple[CustomerGroundTruthV4, ...]
    customer_months: tuple[CustomerMonthGroundTruthV4, ...]
    transactions: tuple[TransactionGroundTruthV4, ...]
    credit_card_transactions: tuple[CardTransactionGroundTruthV4, ...]
    loan_payments: tuple[LoanPaymentGroundTruthV4, ...]
    investment_transactions: tuple[InvestmentTransactionGroundTruthV4, ...]
    balance_sheets: tuple[BalanceSheetGroundTruthV4, ...]
    income_sources: tuple[IncomeSourceGroundTruthV4, ...]
    life_events: tuple[LifeEventGroundTruthV4, ...]
    anomalies: tuple[AnomalyGroundTruthV4, ...]


def _upgrade(record: BaseModel, model: type[_RecordV4]) -> _RecordV4:
    return model.model_validate(record.model_dump(exclude={"schema_version"}))


def _annualized_income(
    states: tuple[IncomeSourceState, ...],
    interval_by_id: dict[str, int],
) -> int:
    return sum(
        state.base_amount_minor * 12 // interval_by_id[state.income_source_id]
        for state in states
        if state.active
    )


def project_ground_truth_v4(
    run: SimulationRun,
    *,
    namespace: UUID,
) -> GroundTruthBundleV4:
    """Build effective-dated private truth without changing observed contracts."""

    twin = run.customer_twin
    if not isinstance(twin, CustomerTwinV4):
        raise TypeError("schema-1.4 projection requires CustomerTwinV4")
    base = project_ground_truth_v3(run, namespace=namespace)
    base_customer = base.customers[0]
    customer = CustomerGroundTruthV4.model_validate(
        {
            **base_customer.model_dump(exclude={"schema_version"}),
            "initial_life_state": twin.initial_life_state,
            "final_life_state": twin.final_life_state,
        }
    )

    event_by_month: dict[str, list] = {}
    for event in run.events:
        event_by_month.setdefault(event.occurred_at.strftime("%Y-%m"), []).append(event)
    transitions = run.life_event_transitions
    anomalies = run.anomalies
    initial_source_states = tuple(
        IncomeSourceState(
            income_source_id=source.income_source_id,
            source_ref=source.source_ref,
            active=True,
            base_amount_minor=source.base_amount_minor,
            payer=source.payer,
            description=source.description,
        )
        for source in sorted(run.income_sources, key=lambda item: item.source_ref)
    )
    customer_months: list[CustomerMonthGroundTruthV4] = []
    for base_month in base.customer_months:
        reference_date = month_end(date.fromisoformat(f"{base_month.month}-01"))
        current_state = twin.initial_life_state
        current_sources = initial_source_states
        for transition in transitions:
            if transition.effective_date > reference_date:
                break
            current_state = transition.state_after
            current_sources = transition.income_sources_after
        month_events = event_by_month.get(base_month.month, [])
        customer_months.append(
            CustomerMonthGroundTruthV4.model_validate(
                {
                    **base_month.model_dump(exclude={"schema_version"}),
                    "external_inflows_minor": sum(
                        event.amount_minor
                        for event in month_events
                        if event.economic_type
                        in {EconomicType.GIFT, EconomicType.REFUND, EconomicType.ASSET_SALE}
                    ),
                    "life_event_count": sum(
                        transition.effective_date.strftime("%Y-%m") == base_month.month
                        for transition in transitions
                    ),
                    "anomaly_count": sum(
                        anomaly.occurred_at.strftime("%Y-%m") == base_month.month
                        for anomaly in anomalies
                    ),
                    "employment_status": current_state.employment_status,
                    "active_income_source_ids": tuple(
                        state.income_source_id for state in current_sources if state.active
                    ),
                    "marital_status": current_state.marital_status,
                    "dependent_count": current_state.dependent_count,
                    "property_count": current_state.property_count,
                    "vehicle_count": current_state.vehicle_count,
                }
            )
        )

    interval_by_id = {
        source.income_source_id: source.frequency.interval_months for source in run.income_sources
    }
    life_events = tuple(
        LifeEventGroundTruthV4(
            life_event_id=item.life_event_id,
            life_event_ref=item.life_event_ref,
            customer_id=item.customer_id,
            event_type=item.event_type,
            effective_date=item.effective_date.isoformat(),
            state_before=item.state_before,
            state_after=item.state_after,
            income_sources_before=item.income_sources_before,
            income_sources_after=item.income_sources_after,
            annualized_base_income_before_minor=_annualized_income(
                item.income_sources_before,
                interval_by_id,
            ),
            annualized_base_income_after_minor=_annualized_income(
                item.income_sources_after,
                interval_by_id,
            ),
            financial_event_id=item.financial_event_id,
        )
        for item in transitions
    )
    anomaly_truth = tuple(
        AnomalyGroundTruthV4(
            anomaly_id=item.anomaly_id,
            anomaly_ref=item.anomaly_ref,
            customer_id=item.customer_id,
            anomaly_type=item.anomaly_type,
            occurred_at=item.occurred_at.isoformat(),
            financial_event_id=item.financial_event_id,
            economic_type=item.economic_type,
        )
        for item in sorted(anomalies, key=lambda value: (value.occurred_at, value.anomaly_ref))
    )
    return GroundTruthBundleV4(
        customers=(customer,),
        customer_months=tuple(customer_months),
        transactions=tuple(_upgrade(item, TransactionGroundTruthV4) for item in base.transactions),
        credit_card_transactions=tuple(
            _upgrade(item, CardTransactionGroundTruthV4) for item in base.credit_card_transactions
        ),
        loan_payments=tuple(
            _upgrade(item, LoanPaymentGroundTruthV4) for item in base.loan_payments
        ),
        investment_transactions=tuple(
            _upgrade(item, InvestmentTransactionGroundTruthV4)
            for item in base.investment_transactions
        ),
        balance_sheets=tuple(
            _upgrade(item, BalanceSheetGroundTruthV4) for item in base.balance_sheets
        ),
        income_sources=tuple(
            _upgrade(item, IncomeSourceGroundTruthV4) for item in base.income_sources
        ),
        life_events=life_events,
        anomalies=anomaly_truth,
    )


__all__ = ["GroundTruthBundleV4", "project_ground_truth_v4"]
