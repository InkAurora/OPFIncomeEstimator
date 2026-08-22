"""Phase-5 engine for life events, seasonality, and labeled anomalies."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any
from uuid import UUID

from finances_simulator.config import config_sha256
from finances_simulator.config_v2 import InvestmentFlowRule
from finances_simulator.config_v4 import (
    AssetSaleAnomalySettings,
    BonusEventSettings,
    DependentAddedEventSettings,
    DependentRemovedEventSettings,
    DivorceEventSettings,
    InheritanceEventSettings,
    InvestmentRedemptionAnomalySettings,
    JobChangeEventSettings,
    JobLossEventSettings,
    LargePixTransferAnomalySettings,
    MarriageEventSettings,
    PromotionEventSettings,
    PropertyPurchaseEventSettings,
    RaiseEventSettings,
    RefundAnomalySettings,
    ScenarioConfigV4,
    VehiclePurchaseEventSettings,
)
from finances_simulator.domain.accounts import Account, Direction
from finances_simulator.domain.customer import CustomerTwinV3, CustomerTwinV4
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.domain.income import IncomeSource
from finances_simulator.domain.life_events import (
    AnomalyType,
    CustomerLifeState,
    EmploymentStatus,
    FinancialAnomaly,
    IncomeSourceState,
    LifeEventTransition,
    LifeEventType,
    MaritalStatus,
)
from finances_simulator.ledger.effects import LedgerEffect, PostingPriority, post_ledger_effects
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import (
    V4_PROFILE,
    deterministic_id,
    make_rng_stream,
    month_start,
    scheduled_date,
    simulation_namespace,
)
from finances_simulator.simulation.v3 import round_half_up_ratio, scale_minor_amount, simulate_v3
from finances_simulator.validation import (
    validate_account_ledgers,
    validate_card_simulation,
    validate_transfer_pairs,
)
from finances_simulator.validation.v2 import (
    validate_investment_simulation,
    validate_loan_simulation,
)
from finances_simulator.validation.v4 import validate_life_event_simulation


class LifeEventSimulationError(ValueError):
    """Raised when selected factory state cannot honor a configured Phase-5 event."""


_BASE_POSTING_PRIORITIES = {
    EconomicType.INCOME: PostingPriority.INCOME,
    EconomicType.LOAN_DISBURSEMENT: PostingPriority.LOAN_DISBURSEMENT,
    EconomicType.OWN_TRANSFER: PostingPriority.OWN_TRANSFER,
    EconomicType.INVESTMENT_CONTRIBUTION: PostingPriority.INVESTMENT_CONTRIBUTION,
    EconomicType.INVESTMENT_REDEMPTION: PostingPriority.INVESTMENT_REDEMPTION,
    EconomicType.CARD_PAYMENT: PostingPriority.CARD_PAYMENT,
    EconomicType.LOAN_PAYMENT: PostingPriority.LOAN_PAYMENT,
    EconomicType.EXPENSE: PostingPriority.EXPENSE,
}

_FINANCIAL_LIFE_EVENT_TYPES = {
    LifeEventType.PROPERTY_PURCHASE,
    LifeEventType.VEHICLE_PURCHASE,
    LifeEventType.BONUS,
    LifeEventType.INHERITANCE,
    LifeEventType.MEDICAL_EXPENSE,
    LifeEventType.VACATION,
}


def realize_v4_income_amount(
    base_amount_minor: int,
    source_seasonality_basis_points: int,
    scenario_seasonality_basis_points: int,
    volatility_shock_basis_points: int,
) -> int:
    """Apply source seasonality, scenario seasonality, and shock in one rounding step."""

    if base_amount_minor <= 0:
        raise ValueError("base_amount_minor must be positive")
    if source_seasonality_basis_points < 0 or scenario_seasonality_basis_points < 0:
        raise ValueError("seasonality multipliers must be non-negative")
    if volatility_shock_basis_points < -10_000:
        raise ValueError("volatility shock must not make the income factor negative")
    return round_half_up_ratio(
        base_amount_minor
        * source_seasonality_basis_points
        * scenario_seasonality_basis_points
        * (10_000 + volatility_shock_basis_points),
        1_000_000_000_000,
    )


def _month_index(start_date: date, value: date) -> int:
    return (value.year - start_date.year) * 12 + value.month - start_date.month


def _prepared_config(config: ScenarioConfigV4, months: int) -> ScenarioConfigV4:
    """Expand calendar-sensitive card and anomalous investment work before V3 reuse."""

    card_rules = []
    for rule in config.card_purchase_rules:
        for occurrence_index in range(rule.occurrences):
            occurrence_month_index = (
                rule.start_month_index + occurrence_index * rule.interval_months
            )
            if occurrence_month_index >= months:
                break
            current_month = month_start(config.scenario.start_date, occurrence_month_index)
            amount_minor = scale_minor_amount(
                rule.amount_minor,
                config.seasonality.expense_multipliers_basis_points[current_month.month - 1],
            )
            if amount_minor == 0:
                continue
            card_rules.append(
                rule.model_copy(
                    update={
                        "rule_id": f"{rule.rule_id}.occurrence-{occurrence_index:04d}",
                        "amount_minor": max(amount_minor, rule.installment_count),
                        "start_month_index": occurrence_month_index,
                        "interval_months": 1,
                        "occurrences": 1,
                    }
                )
            )

    redemption_rules = list(config.investment_redemption_rules)
    for item in config.anomalies:
        if not isinstance(item, InvestmentRedemptionAnomalySettings):
            continue
        month_index = _month_index(config.scenario.start_date, item.occurred_at)
        if not 0 <= month_index < months:
            continue
        redemption_rules.append(
            InvestmentFlowRule(
                rule_id=f"anomaly.{item.anomaly_ref}",
                investment_ref=item.investment_ref,
                account_ref=item.destination_account_ref,
                amount_minor=item.amount_minor,
                day_of_month=item.occurred_at.day,
                start_month_index=month_index,
                interval_months=1,
                occurrences=1,
                description=item.description,
            )
        )
    return config.model_copy(
        update={
            "card_purchase_rules": card_rules,
            "investment_redemption_rules": redemption_rules,
        }
    )


def _income_state(source: IncomeSource, *, active: bool = True) -> IncomeSourceState:
    return IncomeSourceState(
        income_source_id=source.income_source_id,
        source_ref=source.source_ref,
        active=active,
        base_amount_minor=source.base_amount_minor,
        payer=source.payer,
        description=source.description,
    )


def _ordered_source_states(
    source_states: dict[str, IncomeSourceState],
) -> tuple[IncomeSourceState, ...]:
    return tuple(source_states[key] for key in sorted(source_states))


def _initial_life_state(config: ScenarioConfigV4, twin: CustomerTwinV3) -> CustomerLifeState:
    employment_status = EmploymentStatus(twin.income_profile.value)
    salary_source = next(
        (source for source in twin.income_sources if source.income_kind.value == "SALARY"),
        None,
    )
    employer_source = salary_source or (twin.income_sources[0] if twin.income_sources else None)
    return CustomerLifeState(
        employment_status=employment_status,
        employer=employer_source.payer if employer_source is not None else None,
        job_title=config.initial_life_state.job_title,
        marital_status=config.initial_life_state.marital_status,
        dependent_count=config.initial_life_state.dependent_count,
        property_count=config.initial_life_state.property_count,
        vehicle_count=config.initial_life_state.vehicle_count,
    )


def _changed_income_amount(
    current: IncomeSourceState,
    *,
    new_base_amount_minor: int | None,
    amount_multiplier_basis_points: int | None,
) -> int:
    if new_base_amount_minor is not None:
        return new_base_amount_minor
    if amount_multiplier_basis_points is None:
        return current.base_amount_minor
    amount = scale_minor_amount(current.base_amount_minor, amount_multiplier_basis_points)
    if amount <= 0:
        raise LifeEventSimulationError("income transition produced a zero base amount")
    return amount


def _updated_life_state(
    state: CustomerLifeState,
    **updates: object,
) -> CustomerLifeState:
    return CustomerLifeState.model_validate({**state.model_dump(), **updates})


def _life_event_financial_effect(
    *,
    item: Any,
    life_event_id: str,
    customer_id: str,
    currency: str,
    accounts_by_ref: dict[str, Account],
    sources_by_ref: dict[str, IncomeSource],
    source_states: dict[str, IncomeSourceState],
    namespace: UUID,
) -> tuple[FinancialEvent, LedgerEffect] | None:
    if LifeEventType(item.event_type) not in _FINANCIAL_LIFE_EVENT_TYPES:
        return None
    financial_event_id = deterministic_id(
        namespace,
        "event",
        f"life-event:{item.life_event_ref}",
    )
    metadata: dict[str, str | int] = {
        "life_event_id": life_event_id,
        "life_event_ref": item.life_event_ref,
        "life_event_type": item.event_type,
    }
    if isinstance(item, BonusEventSettings):
        source = sources_by_ref.get(item.income_source_ref)
        state = source_states.get(item.income_source_ref)
        if source is None or state is None:
            raise LifeEventSimulationError(
                f"life event {item.life_event_ref!r} targets an unselected income source"
            )
        if not state.active:
            raise LifeEventSimulationError(
                f"bonus life event {item.life_event_ref!r} targets an inactive income source"
            )
        event = FinancialEvent(
            event_id=financial_event_id,
            customer_id=customer_id,
            occurred_at=item.effective_date,
            economic_type=EconomicType.INCOME,
            amount_minor=item.amount_minor,
            currency=currency,
            source_entity=state.payer,
            destination_entity=source.destination_account_id,
            income_source_id=source.income_source_id,
            description=item.description,
            metadata={
                **metadata,
                "income_kind": source.income_kind.value,
                "source_ref": source.source_ref,
            },
        )
        account_id = source.destination_account_id
        direction = Direction.CREDIT
        priority = PostingPriority.INCOME
    elif isinstance(item, InheritanceEventSettings):
        account_id = accounts_by_ref[item.destination_account_ref].account_id
        event = FinancialEvent(
            event_id=financial_event_id,
            customer_id=customer_id,
            occurred_at=item.effective_date,
            economic_type=EconomicType.GIFT,
            amount_minor=item.amount_minor,
            currency=currency,
            source_entity=item.source_entity,
            destination_entity=account_id,
            description=item.description,
            metadata=metadata,
        )
        direction = Direction.CREDIT
        priority = PostingPriority.EXTERNAL_CREDIT
    else:
        account_id = accounts_by_ref[item.source_account_ref].account_id
        payee = item.counterparty if hasattr(item, "counterparty") else item.payee
        event = FinancialEvent(
            event_id=financial_event_id,
            customer_id=customer_id,
            occurred_at=item.effective_date,
            economic_type=EconomicType.EXPENSE,
            amount_minor=item.amount_minor,
            currency=currency,
            source_entity=account_id,
            destination_entity=payee,
            description=item.description,
            metadata=metadata,
        )
        direction = Direction.DEBIT
        priority = PostingPriority.EXPENSE
    effect = LedgerEffect(
        event_id=financial_event_id,
        account_id=account_id,
        posted_at=item.effective_date,
        direction=direction,
        amount_minor=item.amount_minor,
        posting_priority=priority,
        entry_key=f"life-event:{item.life_event_ref}",
        description=item.description,
    )
    return event, effect


def _simulate_life_events(
    *,
    config: ScenarioConfigV4,
    twin: CustomerTwinV3,
    accounts_by_ref: dict[str, Account],
    namespace: UUID,
    end_date: date,
) -> tuple[
    CustomerLifeState,
    CustomerLifeState,
    tuple[LifeEventTransition, ...],
    tuple[FinancialEvent, ...],
    tuple[LedgerEffect, ...],
]:
    initial = _initial_life_state(config, twin)
    current_state = initial
    sources_by_ref = {source.source_ref: source for source in twin.income_sources}
    source_states = {key: _income_state(value) for key, value in sources_by_ref.items()}
    transitions: list[LifeEventTransition] = []
    financial_events: list[FinancialEvent] = []
    effects: list[LedgerEffect] = []

    ordered = sorted(
        enumerate(config.life_events),
        key=lambda pair: (pair[1].effective_date, pair[0]),
    )
    for _, item in ordered:
        if item.effective_date > end_date:
            continue
        event_type = LifeEventType(item.event_type)
        before_state = current_state
        before_sources = _ordered_source_states(source_states)
        target_ref = getattr(item, "income_source_ref", None)
        target = source_states.get(target_ref) if target_ref is not None else None
        if target_ref is not None and target is None:
            raise LifeEventSimulationError(
                f"life event {item.life_event_ref!r} targets unselected income source "
                f"{target_ref!r}"
            )

        if isinstance(item, RaiseEventSettings | PromotionEventSettings):
            assert target_ref is not None and target is not None
            source_states[target_ref] = target.model_copy(
                update={
                    "base_amount_minor": _changed_income_amount(
                        target,
                        new_base_amount_minor=item.new_base_amount_minor,
                        amount_multiplier_basis_points=item.amount_multiplier_basis_points,
                    )
                }
            )
            if isinstance(item, PromotionEventSettings):
                current_state = _updated_life_state(
                    current_state,
                    job_title=item.new_job_title,
                )
        elif isinstance(item, JobLossEventSettings):
            assert target_ref is not None and target is not None
            source_states[target_ref] = target.model_copy(update={"active": False})
            if not any(state.active for state in source_states.values()):
                current_state = _updated_life_state(
                    current_state,
                    employment_status=EmploymentStatus.UNEMPLOYED,
                    employer=None,
                    job_title=None,
                )
        elif isinstance(item, JobChangeEventSettings):
            assert target_ref is not None and target is not None
            source_states[target_ref] = target.model_copy(
                update={
                    "active": True,
                    "base_amount_minor": item.new_base_amount_minor,
                    "payer": item.new_payer,
                    "description": item.new_description,
                }
            )
            current_state = _updated_life_state(
                current_state,
                employment_status=EmploymentStatus.SALARIED,
                employer=item.new_payer,
                job_title=item.new_job_title or current_state.job_title,
            )
        elif isinstance(item, MarriageEventSettings):
            current_state = _updated_life_state(
                current_state,
                marital_status=MaritalStatus.MARRIED,
            )
        elif isinstance(item, DivorceEventSettings):
            current_state = _updated_life_state(
                current_state,
                marital_status=MaritalStatus.DIVORCED,
            )
        elif isinstance(item, DependentAddedEventSettings):
            current_state = _updated_life_state(
                current_state,
                dependent_count=current_state.dependent_count + item.count,
            )
        elif isinstance(item, DependentRemovedEventSettings):
            if item.count > current_state.dependent_count:
                raise LifeEventSimulationError(
                    f"life event {item.life_event_ref!r} removes more dependents than exist"
                )
            current_state = _updated_life_state(
                current_state,
                dependent_count=current_state.dependent_count - item.count,
            )
        elif isinstance(item, PropertyPurchaseEventSettings):
            current_state = _updated_life_state(
                current_state,
                property_count=current_state.property_count + 1,
            )
        elif isinstance(item, VehiclePurchaseEventSettings):
            current_state = _updated_life_state(
                current_state,
                vehicle_count=current_state.vehicle_count + 1,
            )

        life_event_id = deterministic_id(
            namespace,
            "life_event",
            item.life_event_ref,
        )
        financial = _life_event_financial_effect(
            item=item,
            life_event_id=life_event_id,
            customer_id=twin.customer_id,
            currency=twin.currency,
            accounts_by_ref=accounts_by_ref,
            sources_by_ref=sources_by_ref,
            source_states=source_states,
            namespace=namespace,
        )
        financial_event_id = None
        if financial is not None:
            financial_event, effect = financial
            financial_events.append(financial_event)
            effects.append(effect)
            financial_event_id = financial_event.event_id
        transitions.append(
            LifeEventTransition(
                life_event_id=life_event_id,
                life_event_ref=item.life_event_ref,
                customer_id=twin.customer_id,
                event_type=event_type,
                effective_date=item.effective_date,
                state_before=before_state,
                state_after=current_state,
                income_sources_before=before_sources,
                income_sources_after=_ordered_source_states(source_states),
                financial_event_id=financial_event_id,
            )
        )
    return initial, current_state, tuple(transitions), tuple(financial_events), tuple(effects)


def _source_state_at(
    source: IncomeSource,
    occurred_at: date,
    transitions: tuple[LifeEventTransition, ...],
) -> IncomeSourceState:
    state = _income_state(source)
    for transition in transitions:
        if transition.effective_date > occurred_at:
            break
        state = next(
            item
            for item in transition.income_sources_after
            if item.income_source_id == source.income_source_id
        )
    return state


def _income_events_and_effects(
    *,
    sources: tuple[IncomeSource, ...],
    customer_index: int,
    transitions: tuple[LifeEventTransition, ...],
    config: ScenarioConfigV4,
    seed: int,
    months: int,
    namespace: UUID,
) -> tuple[tuple[FinancialEvent, ...], tuple[LedgerEffect, ...]]:
    events: list[FinancialEvent] = []
    effects: list[LedgerEffect] = []
    for source in sources:
        for occurrence_index in range(source.occurrences):
            month_index = (
                source.start_month_index + occurrence_index * source.frequency.interval_months
            )
            if month_index >= months:
                break
            current_month = month_start(config.scenario.start_date, month_index)
            occurred_at = scheduled_date(current_month, source.day_of_month)
            state = _source_state_at(source, occurred_at, transitions)
            if not state.active:
                continue
            stream_key = (
                "income-generation-v1:"
                f"customer:{customer_index}:"
                f"bundle:{source.source_bundle_ref}:"
                f"source:{source.source_ref}:occurrence:{occurrence_index}"
            )
            payment_ticket = make_rng_stream(seed, f"{stream_key}:payment").randint(0, 9_999)
            if payment_ticket >= source.payment_probability_basis_points:
                continue
            shock = make_rng_stream(seed, f"{stream_key}:volatility").randint(
                -source.volatility_basis_points,
                source.volatility_basis_points,
            )
            scenario_multiplier = config.seasonality.income_multipliers_basis_points[
                current_month.month - 1
            ]
            amount_minor = realize_v4_income_amount(
                state.base_amount_minor,
                source.seasonality_basis_points[current_month.month - 1],
                scenario_multiplier,
                shock,
            )
            if amount_minor == 0:
                continue
            event = FinancialEvent(
                event_id=deterministic_id(
                    namespace,
                    "event",
                    (
                        f"income:{source.source_bundle_ref}:{source.source_ref}:"
                        f"{occurrence_index:04d}"
                    ),
                ),
                customer_id=source.customer_id,
                occurred_at=occurred_at,
                economic_type=EconomicType.INCOME,
                amount_minor=amount_minor,
                currency=source.currency,
                source_entity=state.payer,
                destination_entity=source.destination_account_id,
                income_source_id=source.income_source_id,
                description=state.description,
                metadata={
                    "effective_base_amount_minor": state.base_amount_minor,
                    "income_kind": source.income_kind.value,
                    "occurrence_index": occurrence_index,
                    "scenario_seasonality_basis_points": scenario_multiplier,
                    "schedule_month": current_month.strftime("%Y-%m"),
                    "source_ref": source.source_ref,
                },
            )
            events.append(event)
            effects.append(
                LedgerEffect(
                    event_id=event.event_id,
                    account_id=source.destination_account_id,
                    posted_at=event.occurred_at,
                    direction=Direction.CREDIT,
                    amount_minor=event.amount_minor,
                    posting_priority=PostingPriority.INCOME,
                    entry_key=f"income-credit:{source.source_ref}:{occurrence_index:04d}",
                    description=event.description,
                )
            )
    return tuple(events), tuple(effects)


def _seasonal_base_events(
    run: SimulationRun,
    config: ScenarioConfigV4,
) -> tuple[FinancialEvent, ...]:
    events: list[FinancialEvent] = []
    for event in run.events:
        if event.economic_type is EconomicType.INCOME:
            continue
        if event.economic_type is EconomicType.EXPENSE and event.metadata.get("expense_kind") in {
            "FIXED",
            "VARIABLE",
        }:
            multiplier = config.seasonality.expense_multipliers_basis_points[
                event.occurred_at.month - 1
            ]
            amount_minor = scale_minor_amount(event.amount_minor, multiplier)
            if amount_minor == 0:
                continue
            event = event.model_copy(
                update={
                    "amount_minor": amount_minor,
                    "metadata": {
                        **event.metadata,
                        "seasonal_expense_multiplier_basis_points": multiplier,
                    },
                }
            )
        events.append(event)
    return tuple(events)


def _base_effects(
    run: SimulationRun,
    events: tuple[FinancialEvent, ...],
) -> tuple[LedgerEffect, ...]:
    event_by_id = {event.event_id: event for event in events}
    effects: list[LedgerEffect] = []
    for entry in run.ledger_entries:
        event = event_by_id.get(entry.event_id)
        if event is None:
            continue
        effects.append(
            LedgerEffect(
                event_id=entry.event_id,
                account_id=entry.account_id,
                posted_at=entry.posted_at,
                direction=entry.direction,
                amount_minor=(
                    event.amount_minor
                    if event.economic_type is EconomicType.EXPENSE
                    and event.metadata.get("expense_kind") in {"FIXED", "VARIABLE"}
                    else entry.amount_minor
                ),
                posting_priority=_BASE_POSTING_PRIORITIES[event.economic_type],
                entry_key=f"v4-repost:{entry.entry_id}",
                transfer_group_id=entry.transfer_group_id,
                description=entry.description,
            )
        )
    return tuple(effects)


def _simulate_direct_anomalies(
    *,
    config: ScenarioConfigV4,
    run: SimulationRun,
    accounts_by_ref: dict[str, Account],
    namespace: UUID,
) -> tuple[tuple[FinancialAnomaly, ...], tuple[FinancialEvent, ...], tuple[LedgerEffect, ...]]:
    anomalies: list[FinancialAnomaly] = []
    events: list[FinancialEvent] = []
    effects: list[LedgerEffect] = []
    base_event_by_rule = {
        str(event.metadata.get("rule_id")): event
        for event in run.events
        if event.economic_type is EconomicType.INVESTMENT_REDEMPTION
    }
    for item in config.anomalies:
        if item.occurred_at > run.end_date:
            continue
        anomaly_id = deterministic_id(namespace, "anomaly", item.anomaly_ref)
        if isinstance(item, InvestmentRedemptionAnomalySettings):
            event = base_event_by_rule.get(f"anomaly.{item.anomaly_ref}")
            if event is None:
                raise LifeEventSimulationError(
                    f"investment redemption anomaly {item.anomaly_ref!r} was not accepted"
                )
            economic_type = EconomicType.INVESTMENT_REDEMPTION
            financial_event_id = event.event_id
        else:
            financial_event_id = deterministic_id(
                namespace,
                "event",
                f"anomaly:{item.anomaly_ref}",
            )
            metadata = {
                "anomaly_id": anomaly_id,
                "anomaly_ref": item.anomaly_ref,
                "anomaly_type": item.anomaly_type,
            }
            if isinstance(item, LargePixTransferAnomalySettings):
                source = accounts_by_ref[item.source_account_ref]
                destination = accounts_by_ref[item.destination_account_ref]
                transfer_group_id = deterministic_id(
                    namespace,
                    "transfer_group",
                    f"anomaly:{item.anomaly_ref}",
                )
                economic_type = EconomicType.OWN_TRANSFER
                event = FinancialEvent(
                    event_id=financial_event_id,
                    customer_id=run.customer_twin.customer_id,
                    occurred_at=item.occurred_at,
                    economic_type=economic_type,
                    amount_minor=item.amount_minor,
                    currency=run.customer_twin.currency,
                    source_entity=source.account_id,
                    destination_entity=destination.account_id,
                    description="LARGE PIX OWN-ACCOUNT TRANSFER",
                    metadata={**metadata, "transfer_group_id": transfer_group_id},
                )
                effects.extend(
                    (
                        LedgerEffect(
                            event_id=financial_event_id,
                            account_id=source.account_id,
                            posted_at=item.occurred_at,
                            direction=Direction.DEBIT,
                            amount_minor=item.amount_minor,
                            posting_priority=PostingPriority.OWN_TRANSFER,
                            entry_key="anomaly-pix-debit",
                            transfer_group_id=transfer_group_id,
                            description=item.outgoing_description,
                        ),
                        LedgerEffect(
                            event_id=financial_event_id,
                            account_id=destination.account_id,
                            posted_at=item.occurred_at,
                            direction=Direction.CREDIT,
                            amount_minor=item.amount_minor,
                            posting_priority=PostingPriority.OWN_TRANSFER,
                            entry_key="anomaly-pix-credit",
                            transfer_group_id=transfer_group_id,
                            description=item.incoming_description,
                        ),
                    )
                )
            else:
                destination = accounts_by_ref[item.destination_account_ref]
                if isinstance(item, RefundAnomalySettings):
                    economic_type = EconomicType.REFUND
                    source_entity = item.source_entity
                    description = item.description
                elif isinstance(item, AssetSaleAnomalySettings):
                    economic_type = EconomicType.ASSET_SALE
                    source_entity = item.buyer
                    description = item.description
                    metadata["asset_type"] = item.asset_type
                else:  # pragma: no cover - discriminator makes this unreachable
                    raise TypeError(f"unsupported anomaly: {type(item).__name__}")
                event = FinancialEvent(
                    event_id=financial_event_id,
                    customer_id=run.customer_twin.customer_id,
                    occurred_at=item.occurred_at,
                    economic_type=economic_type,
                    amount_minor=item.amount_minor,
                    currency=run.customer_twin.currency,
                    source_entity=source_entity,
                    destination_entity=destination.account_id,
                    description=description,
                    metadata=metadata,
                )
                effects.append(
                    LedgerEffect(
                        event_id=financial_event_id,
                        account_id=destination.account_id,
                        posted_at=item.occurred_at,
                        direction=Direction.CREDIT,
                        amount_minor=item.amount_minor,
                        posting_priority=PostingPriority.EXTERNAL_CREDIT,
                        entry_key=f"anomaly-credit:{item.anomaly_ref}",
                        description=description,
                    )
                )
            events.append(event)
        anomalies.append(
            FinancialAnomaly(
                anomaly_id=anomaly_id,
                anomaly_ref=item.anomaly_ref,
                customer_id=run.customer_twin.customer_id,
                anomaly_type=AnomalyType(item.anomaly_type),
                occurred_at=item.occurred_at,
                financial_event_id=financial_event_id,
                economic_type=economic_type,
            )
        )
    return tuple(anomalies), tuple(events), tuple(effects)


def simulate_v4(
    config: ScenarioConfigV4,
    *,
    seed: int,
    months: int | None = None,
) -> SimulationRun:
    """Create a schema-1.4 world with effective-dated state and anomaly truth."""

    simulation_months = config.scenario.default_months if months is None else months
    if not 1 <= simulation_months <= 1_200:
        raise ValueError("months must be between 1 and 1200")
    fingerprint = config_sha256(config)
    effective_config = _prepared_config(config, simulation_months)
    base = simulate_v3(
        effective_config,
        seed=seed,
        months=simulation_months,
        _profile=V4_PROFILE,
        _config_fingerprint=fingerprint,
    )
    base_twin = base.customer_twin
    if not isinstance(base_twin, CustomerTwinV3):  # pragma: no cover - internal contract
        raise TypeError("V4 requires a V3 customer twin")
    namespace = simulation_namespace(
        fingerprint,
        seed,
        simulator_version=V4_PROFILE.simulator_version,
    )
    account_by_id = {account.account_id: account for account in base_twin.accounts}
    accounts_by_ref = {
        item.account_ref: account_by_id[deterministic_id(namespace, "account", item.account_ref)]
        for item in config.accounts
    }
    initial_state, final_state, transitions, life_events, life_effects = _simulate_life_events(
        config=config,
        twin=base_twin,
        accounts_by_ref=accounts_by_ref,
        namespace=namespace,
        end_date=base.end_date,
    )
    income_events, income_effects = _income_events_and_effects(
        sources=base.income_sources,
        customer_index=base.factory_member.customer_index if base.factory_member else 0,
        transitions=transitions,
        config=config,
        seed=seed,
        months=base.months,
        namespace=namespace,
    )
    anomalies, anomaly_events, anomaly_effects = _simulate_direct_anomalies(
        config=config,
        run=base,
        accounts_by_ref=accounts_by_ref,
        namespace=namespace,
    )
    base_events = _seasonal_base_events(base, config)
    events = tuple(
        sorted(
            (*base_events, *income_events, *life_events, *anomaly_events),
            key=lambda event: (event.occurred_at, event.event_id),
        )
    )
    twin = CustomerTwinV4.model_validate(
        {
            **base_twin.model_dump(),
            "initial_life_state": initial_state,
            "final_life_state": final_state,
        }
    )
    ledger_entries = post_ledger_effects(
        twin.accounts,
        (
            *_base_effects(base, base_events),
            *income_effects,
            *life_effects,
            *anomaly_effects,
        ),
        namespace,
    )

    validate_account_ledgers(twin.accounts, ledger_entries)
    validate_transfer_pairs(events, ledger_entries)
    validate_card_simulation(
        cards=base.cards,
        purchases=base.card_purchases,
        installments=base.card_installments,
        invoices=base.card_invoices,
        snapshots=base.credit_limit_snapshots,
        events=events,
        entries=ledger_entries,
        start_date=base.start_date,
        end_date=base.end_date,
        months=base.months,
    )
    validate_loan_simulation(
        loans=base.loans,
        payments=base.loan_payments,
        snapshots=base.loan_balance_snapshots,
        events=events,
        entries=ledger_entries,
        start_date=base.start_date,
        end_date=base.end_date,
        months=base.months,
    )
    validate_investment_simulation(
        investments=base.investments,
        transactions=base.investment_transactions,
        snapshots=base.investment_balance_snapshots,
        events=events,
        entries=ledger_entries,
        start_date=base.start_date,
        months=base.months,
    )
    validate_life_event_simulation(
        twin=twin,
        sources=base.income_sources,
        transitions=transitions,
        anomalies=anomalies,
        events=events,
        entries=ledger_entries,
    )

    return replace(
        base,
        customer_twin=twin,
        events=events,
        ledger_entries=ledger_entries,
        life_event_transitions=transitions,
        anomalies=anomalies,
        income_seasonality_basis_points=config.seasonality.income_multipliers_basis_points,
    )


__all__ = ["LifeEventSimulationError", "realize_v4_income_amount", "simulate_v4"]
