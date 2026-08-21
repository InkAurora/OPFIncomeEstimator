"""Phase-4 engine for sampled customers and diverse income sources."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from uuid import UUID

from finances_simulator.config import config_sha256
from finances_simulator.config_v3 import ScenarioConfigV3
from finances_simulator.domain.accounts import Account, Direction
from finances_simulator.domain.customer import CustomerTwinV3
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.domain.income import CustomerFactoryMember, IncomeSource
from finances_simulator.factory import CustomerFactory
from finances_simulator.ledger.effects import (
    LedgerEffect,
    PostingPriority,
    post_ledger_effects,
)
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import (
    V3_PROFILE,
    VersionProfile,
    deterministic_id,
    make_rng_stream,
    month_start,
    scheduled_date,
    simulation_namespace,
)
from finances_simulator.simulation.v2 import simulate_v2
from finances_simulator.validation import (
    validate_account_ledgers,
    validate_card_simulation,
    validate_income_simulation,
    validate_transfer_pairs,
)
from finances_simulator.validation.v2 import (
    validate_investment_simulation,
    validate_loan_simulation,
)

_CASH_POSTING_PRIORITIES = {
    EconomicType.INCOME: PostingPriority.INCOME,
    EconomicType.LOAN_DISBURSEMENT: PostingPriority.LOAN_DISBURSEMENT,
    EconomicType.OWN_TRANSFER: PostingPriority.OWN_TRANSFER,
    EconomicType.INVESTMENT_CONTRIBUTION: PostingPriority.INVESTMENT_CONTRIBUTION,
    EconomicType.INVESTMENT_REDEMPTION: PostingPriority.INVESTMENT_REDEMPTION,
    EconomicType.CARD_PAYMENT: PostingPriority.CARD_PAYMENT,
    EconomicType.LOAN_PAYMENT: PostingPriority.LOAN_PAYMENT,
    EconomicType.EXPENSE: PostingPriority.EXPENSE,
}


def round_half_up_ratio(numerator: int, denominator: int) -> int:
    """Round one non-negative rational value to the nearest integer, ties up."""

    if isinstance(numerator, bool) or not isinstance(numerator, int):
        raise TypeError("numerator must be an integer")
    if isinstance(denominator, bool) or not isinstance(denominator, int):
        raise TypeError("denominator must be an integer")
    if numerator < 0:
        raise ValueError("numerator must be non-negative")
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    return (numerator + denominator // 2) // denominator


def scale_minor_amount(amount_minor: int, multiplier_basis_points: int) -> int:
    """Scale a non-negative minor-unit amount with one half-up rounding step."""

    if amount_minor < 0:
        raise ValueError("amount_minor must be non-negative")
    if multiplier_basis_points < 0:
        raise ValueError("multiplier_basis_points must be non-negative")
    return round_half_up_ratio(amount_minor * multiplier_basis_points, 10_000)


def realize_income_amount(
    base_amount_minor: int,
    seasonality_basis_points: int,
    volatility_shock_basis_points: int,
) -> int:
    """Apply seasonality and volatility together, with no intermediate rounding."""

    if base_amount_minor <= 0:
        raise ValueError("base_amount_minor must be positive")
    if seasonality_basis_points < 0:
        raise ValueError("seasonality_basis_points must be non-negative")
    if volatility_shock_basis_points < -10_000:
        raise ValueError("volatility shock must not make the income factor negative")
    return round_half_up_ratio(
        base_amount_minor * seasonality_basis_points * (10_000 + volatility_shock_basis_points),
        100_000_000,
    )


def _scaled_config(
    config: ScenarioConfigV3,
    member: CustomerFactoryMember,
) -> ScenarioConfigV3:
    """Apply sampled behavior and wealth without mutating the source contract."""

    def scaled_or_none(item: object, multiplier: int, *, minimum: int = 0) -> object | None:
        amount = scale_minor_amount(getattr(item, "amount_minor"), multiplier)
        if amount == 0:
            return None
        return item.model_copy(update={"amount_minor": max(amount, minimum)})

    spending = member.spending_multiplier_basis_points
    saving = member.saving_multiplier_basis_points
    deposit_wealth = member.deposit_balance_multiplier_basis_points
    investment_wealth = member.investment_balance_multiplier_basis_points

    accounts = [
        item.model_copy(
            update={
                "opening_balance_minor": scale_minor_amount(
                    item.opening_balance_minor,
                    deposit_wealth,
                )
            }
        )
        for item in config.accounts
    ]
    fixed_expenses = [
        scaled
        for item in config.fixed_expenses
        for scaled in (scaled_or_none(item, spending),)
        if scaled is not None
    ]
    variable_updates = (
        {"count_min": 0, "count_max": 0}
        if spending == 0
        else {
            "amount_min_minor": max(
                1,
                scale_minor_amount(config.variable_expenses.amount_min_minor, spending),
            ),
            "amount_max_minor": max(
                1,
                scale_minor_amount(config.variable_expenses.amount_max_minor, spending),
            ),
        }
    )
    variable_expenses = config.variable_expenses.model_copy(update=variable_updates)
    own_transfers = [
        scaled
        for item in config.own_transfers
        for scaled in (scaled_or_none(item, saving),)
        if scaled is not None
    ]
    card_purchase_rules = [
        scaled
        for item in config.card_purchase_rules
        for scaled in (scaled_or_none(item, spending, minimum=item.installment_count),)
        if scaled is not None
    ]
    investments = [
        item.model_copy(
            update={
                "opening_balance_minor": scale_minor_amount(
                    item.opening_balance_minor,
                    investment_wealth,
                )
            }
        )
        for item in config.investments
    ]
    contributions = [
        scaled
        for item in config.investment_contribution_rules
        for scaled in (scaled_or_none(item, saving),)
        if scaled is not None
    ]
    return config.model_copy(
        update={
            "accounts": accounts,
            "fixed_expenses": fixed_expenses,
            "variable_expenses": variable_expenses,
            "own_transfers": own_transfers,
            "card_purchase_rules": card_purchase_rules,
            "investments": investments,
            "investment_contribution_rules": contributions,
        }
    )


def _materialize_income_sources(
    *,
    member: CustomerFactoryMember,
    customer_id: str,
    currency: str,
    accounts_by_ref: dict[str, Account],
    namespace: UUID,
) -> tuple[IncomeSource, ...]:
    return tuple(
        IncomeSource(
            income_source_id=deterministic_id(
                namespace,
                "income_source",
                f"{member.source_bundle_ref}:{sampled.source_ref}",
            ),
            customer_id=customer_id,
            source_ref=sampled.source_ref,
            source_bundle_ref=member.source_bundle_ref,
            income_kind=sampled.income_kind,
            currency=currency,
            payer=sampled.payer,
            description=sampled.description,
            destination_account_id=accounts_by_ref[sampled.destination_account_ref].account_id,
            base_amount_minor=sampled.base_amount_minor,
            day_of_month=sampled.day_of_month,
            frequency=sampled.frequency,
            start_month_index=sampled.start_month_index,
            occurrences=sampled.occurrences,
            payment_probability_basis_points=(sampled.payment_probability_basis_points),
            volatility_basis_points=sampled.volatility_basis_points,
            seasonality_basis_points=sampled.seasonality_basis_points,
        )
        for sampled in member.income_sources
    )


def _income_events_and_effects(
    *,
    sources: tuple[IncomeSource, ...],
    member: CustomerFactoryMember,
    seed: int,
    start_date: date,
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
            stream_key = (
                "income-generation-v1:"
                f"customer:{member.customer_index}:"
                f"bundle:{source.source_bundle_ref}:"
                f"source:{source.source_ref}:occurrence:{occurrence_index}"
            )
            payment_ticket = make_rng_stream(seed, f"{stream_key}:payment").randint(
                0,
                9_999,
            )
            if payment_ticket >= source.payment_probability_basis_points:
                continue
            shock = make_rng_stream(seed, f"{stream_key}:volatility").randint(
                -source.volatility_basis_points,
                source.volatility_basis_points,
            )
            current_month = month_start(start_date, month_index)
            amount_minor = realize_income_amount(
                source.base_amount_minor,
                source.seasonality_basis_points[current_month.month - 1],
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
                occurred_at=scheduled_date(current_month, source.day_of_month),
                economic_type=EconomicType.INCOME,
                amount_minor=amount_minor,
                currency=source.currency,
                source_entity=source.payer,
                destination_entity=source.destination_account_id,
                income_source_id=source.income_source_id,
                description=source.description,
                metadata={
                    "income_kind": source.income_kind.value,
                    "occurrence_index": occurrence_index,
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
                    amount_minor=amount_minor,
                    posting_priority=PostingPriority.INCOME,
                    entry_key=f"income-credit:{source.source_ref}:{occurrence_index:04d}",
                    description=source.description,
                )
            )
    return tuple(events), tuple(effects)


def _existing_cash_effects(run: SimulationRun) -> tuple[LedgerEffect, ...]:
    event_by_id = {event.event_id: event for event in run.events}
    return tuple(
        LedgerEffect(
            event_id=entry.event_id,
            account_id=entry.account_id,
            posted_at=entry.posted_at,
            direction=entry.direction,
            amount_minor=entry.amount_minor,
            posting_priority=_CASH_POSTING_PRIORITIES[event_by_id[entry.event_id].economic_type],
            entry_key=f"v3-repost:{entry.entry_id}",
            transfer_group_id=entry.transfer_group_id,
            description=entry.description,
        )
        for entry in run.ledger_entries
    )


def simulate_v3(
    config: ScenarioConfigV3,
    *,
    seed: int,
    months: int | None = None,
    _profile: VersionProfile = V3_PROFILE,
    _config_fingerprint: str | None = None,
) -> SimulationRun:
    """Sample one customer and create a schema-1.3 reconciled financial world."""

    fingerprint = config_sha256(config) if _config_fingerprint is None else _config_fingerprint
    member = CustomerFactory(config.customer_factory, seed=seed).sample_one()
    effective_config = _scaled_config(config, member)
    base = simulate_v2(
        effective_config,
        seed=seed,
        months=months,
        _profile=_profile,
        _include_salary=False,
        _config_fingerprint=fingerprint,
    )
    namespace = simulation_namespace(
        fingerprint,
        seed,
        simulator_version=_profile.simulator_version,
    )
    account_by_id = {account.account_id: account for account in base.customer_twin.accounts}
    accounts_by_ref = {
        item.account_ref: account_by_id[deterministic_id(namespace, "account", item.account_ref)]
        for item in config.accounts
    }
    sources = _materialize_income_sources(
        member=member,
        customer_id=base.customer_twin.customer_id,
        currency=config.customer.currency,
        accounts_by_ref=accounts_by_ref,
        namespace=namespace,
    )
    twin = CustomerTwinV3(
        customer_id=base.customer_twin.customer_id,
        scenario_name=config.scenario.name,
        currency=config.customer.currency,
        income_profile=member.income_profile,
        source_bundle_ref=member.source_bundle_ref,
        behavior_profile=member.behavior_profile,
        wealth_band=member.wealth_band,
        spending_multiplier_basis_points=member.spending_multiplier_basis_points,
        saving_multiplier_basis_points=member.saving_multiplier_basis_points,
        deposit_balance_multiplier_basis_points=(member.deposit_balance_multiplier_basis_points),
        investment_balance_multiplier_basis_points=(
            member.investment_balance_multiplier_basis_points
        ),
        income_sources=sources,
        primary_account=accounts_by_ref[config.customer.primary_account_ref],
        additional_accounts=tuple(
            account
            for item in config.accounts
            for account in (accounts_by_ref[item.account_ref],)
            if item.account_ref != config.customer.primary_account_ref
        ),
    )
    income_events, income_effects = _income_events_and_effects(
        sources=sources,
        member=member,
        seed=seed,
        start_date=base.start_date,
        months=base.months,
        namespace=namespace,
    )
    events = tuple(
        sorted(
            (*base.events, *income_events),
            key=lambda event: (event.occurred_at, event.event_id),
        )
    )
    ledger_entries = post_ledger_effects(
        twin.accounts,
        (*_existing_cash_effects(base), *income_effects),
        namespace,
    )

    validate_account_ledgers(twin.accounts, ledger_entries)
    validate_transfer_pairs(events, ledger_entries)
    validate_income_simulation(
        sources=sources,
        events=events,
        entries=ledger_entries,
    )
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
    return replace(
        base,
        customer_twin=twin,
        events=events,
        ledger_entries=ledger_entries,
        factory_member=member,
        income_sources=sources,
    )


__all__ = [
    "realize_income_amount",
    "round_half_up_ratio",
    "scale_minor_amount",
    "simulate_v3",
]
