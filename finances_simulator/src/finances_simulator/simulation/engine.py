"""Causal V0 simulation engine."""

from dataclasses import dataclass
from datetime import date

from finances_simulator.config import ScenarioConfig, config_sha256
from finances_simulator.domain.accounts import Account, LedgerEntry
from finances_simulator.domain.cards import (
    CardInstallment,
    CardInvoice,
    CardPurchase,
    CreditCard,
    CreditLimitSnapshot,
)
from finances_simulator.domain.customer import CustomerTwin, CustomerTwinV3, CustomerTwinV4
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.domain.income import CustomerFactoryMember, IncomeSource
from finances_simulator.domain.investments import (
    Investment,
    InvestmentBalanceSnapshot,
    InvestmentTransaction,
)
from finances_simulator.domain.life_events import FinancialAnomaly, LifeEventTransition
from finances_simulator.domain.loans import Loan, LoanBalanceSnapshot, LoanPayment
from finances_simulator.ledger import post_events
from finances_simulator.simulation.primitives import (
    V0_PROFILE,
    VersionProfile,
    deterministic_id,
    make_rng,
    make_run_id,
    month_end,
    month_start,
    scheduled_date,
    simulation_namespace,
)
from finances_simulator.validation.invariants import validate_reconciliation


@dataclass(frozen=True, slots=True)
class SimulationRun:
    """Complete hidden result before truth and observation projections."""

    run_id: str
    seed: int
    months: int
    start_date: date
    end_date: date
    config_sha256: str
    customer_twin: CustomerTwin | CustomerTwinV3 | CustomerTwinV4
    events: tuple[FinancialEvent, ...]
    ledger_entries: tuple[LedgerEntry, ...]
    profile: VersionProfile = V0_PROFILE
    cards: tuple[CreditCard, ...] = ()
    card_purchases: tuple[CardPurchase, ...] = ()
    card_installments: tuple[CardInstallment, ...] = ()
    card_invoices: tuple[CardInvoice, ...] = ()
    credit_limit_snapshots: tuple[CreditLimitSnapshot, ...] = ()
    loans: tuple[Loan, ...] = ()
    loan_payments: tuple[LoanPayment, ...] = ()
    loan_balance_snapshots: tuple[LoanBalanceSnapshot, ...] = ()
    investments: tuple[Investment, ...] = ()
    investment_transactions: tuple[InvestmentTransaction, ...] = ()
    investment_balance_snapshots: tuple[InvestmentBalanceSnapshot, ...] = ()
    factory_member: CustomerFactoryMember | None = None
    income_sources: tuple[IncomeSource, ...] = ()
    life_event_transitions: tuple[LifeEventTransition, ...] = ()
    anomalies: tuple[FinancialAnomaly, ...] = ()
    world_config_sha256: str | None = None
    world_simulator_version: str | None = None
    income_seasonality_basis_points: tuple[int, ...] = ()


def simulate_v0(
    config: ScenarioConfig,
    *,
    seed: int,
    months: int | None = None,
) -> SimulationRun:
    """Create hidden state, economic events, and ledger entries."""

    simulation_months = config.scenario.default_months if months is None else months
    if not 1 <= simulation_months <= 1_200:
        raise ValueError("months must be between 1 and 1200")

    fingerprint = config_sha256(config)
    namespace = simulation_namespace(
        fingerprint,
        seed,
        simulator_version=V0_PROFILE.simulator_version,
    )
    run_id = make_run_id(
        fingerprint,
        seed,
        simulation_months,
        simulator_version=V0_PROFILE.simulator_version,
    )
    customer_id = deterministic_id(namespace, "customer", "primary")
    account_id = deterministic_id(namespace, "account", "checking-primary")
    income_source_id = deterministic_id(namespace, "income_source", "salary-primary")

    account = Account(
        account_id=account_id,
        customer_id=customer_id,
        institution_id=config.customer.institution_id,
        institution_name=config.customer.institution_name,
        account_label=config.customer.account_label,
        currency=config.customer.currency,
        opened_on=config.scenario.start_date,
        opening_balance_minor=config.customer.opening_balance_minor,
    )
    twin = CustomerTwin(
        customer_id=customer_id,
        scenario_name=config.scenario.name,
        currency=config.customer.currency,
        true_monthly_salary_minor=config.salary.amount_minor,
        income_source_id=income_source_id,
        primary_account=account,
    )

    rng = make_rng(seed)
    events: list[FinancialEvent] = []
    for month_index in range(simulation_months):
        current_month = month_start(config.scenario.start_date, month_index)
        month_key = current_month.strftime("%Y-%m")

        events.append(
            FinancialEvent(
                event_id=deterministic_id(namespace, "event", f"salary:{month_key}"),
                customer_id=customer_id,
                occurred_at=scheduled_date(current_month, config.salary.day_of_month),
                economic_type=EconomicType.INCOME,
                amount_minor=config.salary.amount_minor,
                currency=config.customer.currency,
                source_entity=config.salary.payer,
                destination_entity=account_id,
                income_source_id=income_source_id,
                description=config.salary.description,
                metadata={"income_kind": "SALARY", "schedule_month": month_key},
            )
        )

        for rule in config.fixed_expenses:
            events.append(
                FinancialEvent(
                    event_id=deterministic_id(
                        namespace, "event", f"fixed:{rule.rule_id}:{month_key}"
                    ),
                    customer_id=customer_id,
                    occurred_at=scheduled_date(current_month, rule.day_of_month),
                    economic_type=EconomicType.EXPENSE,
                    amount_minor=rule.amount_minor,
                    currency=config.customer.currency,
                    source_entity=account_id,
                    destination_entity=rule.payee,
                    description=rule.description,
                    metadata={
                        "expense_kind": "FIXED",
                        "expense_category": rule.category,
                        "rule_id": rule.rule_id,
                    },
                )
            )

        variable_rule = config.variable_expenses
        variable_count = rng.randint(variable_rule.count_min, variable_rule.count_max)
        for transaction_index in range(variable_count):
            merchant = rng.choice(variable_rule.merchants)
            day = rng.randint(variable_rule.day_min, variable_rule.day_max)
            amount_minor = rng.randint(
                variable_rule.amount_min_minor,
                variable_rule.amount_max_minor,
            )
            events.append(
                FinancialEvent(
                    event_id=deterministic_id(
                        namespace,
                        "event",
                        f"variable:{month_key}:{transaction_index:04d}",
                    ),
                    customer_id=customer_id,
                    occurred_at=scheduled_date(current_month, day),
                    economic_type=EconomicType.EXPENSE,
                    amount_minor=amount_minor,
                    currency=config.customer.currency,
                    source_entity=account_id,
                    destination_entity=merchant.entity,
                    description=merchant.description,
                    metadata={
                        "expense_kind": "VARIABLE",
                        "transaction_index": transaction_index,
                    },
                )
            )

    ordered_events = tuple(sorted(events, key=lambda event: (event.occurred_at, event.event_id)))
    ledger_entries = post_events(account, ordered_events, namespace)
    validate_reconciliation(account, ledger_entries)

    return SimulationRun(
        run_id=run_id,
        seed=seed,
        months=simulation_months,
        start_date=config.scenario.start_date,
        end_date=month_end(month_start(config.scenario.start_date, simulation_months - 1)),
        config_sha256=fingerprint,
        customer_twin=twin,
        events=ordered_events,
        ledger_entries=ledger_entries,
    )


def simulate(
    config: ScenarioConfig,
    *,
    seed: int,
    months: int | None = None,
) -> SimulationRun:
    """Dispatch a validated configuration to its versioned simulation engine."""

    from finances_simulator.config_v1 import ScenarioConfigV1
    from finances_simulator.config_v2 import ScenarioConfigV2
    from finances_simulator.config_v3 import ScenarioConfigV3
    from finances_simulator.config_v4 import ScenarioConfigV4
    from finances_simulator.config_v5 import ScenarioConfigV5
    from finances_simulator.config_v6 import ScenarioConfigV6

    if isinstance(config, ScenarioConfigV6):
        from finances_simulator.simulation.v6 import simulate_v6

        return simulate_v6(config, seed=seed, months=months)

    if isinstance(config, ScenarioConfigV5):
        from finances_simulator.simulation.v5 import simulate_v5

        return simulate_v5(config, seed=seed, months=months)

    if isinstance(config, ScenarioConfigV4):
        from finances_simulator.simulation.v4 import simulate_v4

        return simulate_v4(config, seed=seed, months=months)

    if isinstance(config, ScenarioConfigV3):
        from finances_simulator.simulation.v3 import simulate_v3

        return simulate_v3(config, seed=seed, months=months)

    if isinstance(config, ScenarioConfigV2):
        from finances_simulator.simulation.v2 import simulate_v2

        return simulate_v2(config, seed=seed, months=months)

    if isinstance(config, ScenarioConfigV1):
        from finances_simulator.simulation.v1 import simulate_v1

        return simulate_v1(config, seed=seed, months=months)
    return simulate_v0(config, seed=seed, months=months)
