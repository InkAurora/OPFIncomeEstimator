"""Phase-2 engine for multiple accounts, own transfers, and credit cards."""

from finances_simulator.config import config_sha256
from finances_simulator.config_v1 import ScenarioConfigV1
from finances_simulator.domain.accounts import Account, Direction
from finances_simulator.domain.cards import CreditCard
from finances_simulator.domain.customer import CustomerTwin
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.ledger.effects import (
    LedgerEffect,
    PostingPriority,
    post_ledger_effects,
)
from finances_simulator.simulation.cards import simulate_cards
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import (
    V1_PROFILE,
    VersionProfile,
    deterministic_id,
    make_rng_stream,
    make_run_id,
    month_end,
    month_start,
    scheduled_date,
    simulation_namespace,
)
from finances_simulator.validation import (
    validate_account_ledgers,
    validate_card_simulation,
    validate_transfer_pairs,
)


def simulate_v1(
    config: ScenarioConfigV1,
    *,
    seed: int,
    months: int | None = None,
    _profile: VersionProfile = V1_PROFILE,
) -> SimulationRun:
    """Create schema-1.1 hidden state, events, and reconciled deposit ledgers."""

    simulation_months = config.scenario.default_months if months is None else months
    if not 1 <= simulation_months <= 1_200:
        raise ValueError("months must be between 1 and 1200")

    fingerprint = config_sha256(config)
    namespace = simulation_namespace(
        fingerprint,
        seed,
        simulator_version=_profile.simulator_version,
    )
    run_id = make_run_id(
        fingerprint,
        seed,
        simulation_months,
        simulator_version=_profile.simulator_version,
    )
    customer_id = deterministic_id(namespace, "customer", "primary")
    income_source_id = deterministic_id(namespace, "income_source", "salary-primary")
    institution_by_ref = {
        institution.institution_ref: institution for institution in config.institutions
    }

    accounts_by_ref: dict[str, Account] = {}
    for account_config in config.accounts:
        institution = institution_by_ref[account_config.institution_ref]
        accounts_by_ref[account_config.account_ref] = Account(
            account_id=deterministic_id(namespace, "account", account_config.account_ref),
            customer_id=customer_id,
            institution_id=institution.institution_id,
            institution_name=institution.institution_name,
            account_label=account_config.account_label,
            account_type=account_config.account_type,
            currency=config.customer.currency,
            opened_on=config.scenario.start_date,
            opening_balance_minor=account_config.opening_balance_minor,
        )
    primary_account = accounts_by_ref[config.customer.primary_account_ref]

    cards_by_ref: dict[str, CreditCard] = {}
    for card_config in config.credit_cards:
        institution = institution_by_ref[card_config.institution_ref]
        cards_by_ref[card_config.card_ref] = CreditCard(
            card_id=deterministic_id(namespace, "card", card_config.card_ref),
            customer_id=customer_id,
            institution_id=institution.institution_id,
            institution_name=institution.institution_name,
            card_label=card_config.card_label,
            currency=config.customer.currency,
            opened_on=config.scenario.start_date,
            payment_account_id=accounts_by_ref[card_config.payment_account_ref].account_id,
            credit_limit_minor=card_config.credit_limit_minor,
            maximum_utilization_basis_points=(card_config.utilization_policy.maximum_basis_points),
            statement_close_day=card_config.statement_close_day,
            payment_due_day=card_config.payment_due_day,
            payment_description=card_config.payment_description,
        )

    twin = CustomerTwin(
        customer_id=customer_id,
        scenario_name=config.scenario.name,
        currency=config.customer.currency,
        true_monthly_salary_minor=config.salary.amount_minor,
        income_source_id=income_source_id,
        primary_account=primary_account,
        additional_accounts=tuple(
            account
            for account_ref, account in accounts_by_ref.items()
            if account_ref != config.customer.primary_account_ref
        ),
    )

    events: list[FinancialEvent] = []
    effects: list[LedgerEffect] = []
    expense_rng = make_rng_stream(seed, "deposit-variable-expenses-v1")
    for month_index in range(simulation_months):
        current_month = month_start(config.scenario.start_date, month_index)
        month_key = current_month.strftime("%Y-%m")

        salary_account = accounts_by_ref[config.salary.destination_account_ref]
        salary_event = FinancialEvent(
            event_id=deterministic_id(namespace, "event", f"salary:{month_key}"),
            customer_id=customer_id,
            occurred_at=scheduled_date(current_month, config.salary.day_of_month),
            economic_type=EconomicType.INCOME,
            amount_minor=config.salary.amount_minor,
            currency=config.customer.currency,
            source_entity=config.salary.payer,
            destination_entity=salary_account.account_id,
            income_source_id=income_source_id,
            description=config.salary.description,
            metadata={"income_kind": "SALARY", "schedule_month": month_key},
        )
        events.append(salary_event)
        effects.append(
            LedgerEffect(
                event_id=salary_event.event_id,
                account_id=salary_account.account_id,
                posted_at=salary_event.occurred_at,
                direction=Direction.CREDIT,
                amount_minor=salary_event.amount_minor,
                posting_priority=PostingPriority.INCOME,
                entry_key="salary-credit",
                description=salary_event.description,
            )
        )

        for rule in config.fixed_expenses:
            source_account = accounts_by_ref[rule.source_account_ref]
            event = FinancialEvent(
                event_id=deterministic_id(namespace, "event", f"fixed:{rule.rule_id}:{month_key}"),
                customer_id=customer_id,
                occurred_at=scheduled_date(current_month, rule.day_of_month),
                economic_type=EconomicType.EXPENSE,
                amount_minor=rule.amount_minor,
                currency=config.customer.currency,
                source_entity=source_account.account_id,
                destination_entity=rule.payee,
                description=rule.description,
                metadata={
                    "expense_kind": "FIXED",
                    "expense_category": rule.category,
                    "rule_id": rule.rule_id,
                },
            )
            events.append(event)
            effects.append(
                LedgerEffect(
                    event_id=event.event_id,
                    account_id=source_account.account_id,
                    posted_at=event.occurred_at,
                    direction=Direction.DEBIT,
                    amount_minor=event.amount_minor,
                    posting_priority=PostingPriority.EXPENSE,
                    entry_key=f"fixed-debit:{rule.rule_id}",
                    description=event.description,
                )
            )

        variable_rule = config.variable_expenses
        variable_account = accounts_by_ref[variable_rule.source_account_ref]
        variable_count = expense_rng.randint(variable_rule.count_min, variable_rule.count_max)
        for transaction_index in range(variable_count):
            merchant = expense_rng.choice(variable_rule.merchants)
            day = expense_rng.randint(variable_rule.day_min, variable_rule.day_max)
            amount_minor = expense_rng.randint(
                variable_rule.amount_min_minor,
                variable_rule.amount_max_minor,
            )
            event = FinancialEvent(
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
                source_entity=variable_account.account_id,
                destination_entity=merchant.entity,
                description=merchant.description,
                metadata={
                    "expense_kind": "VARIABLE",
                    "transaction_index": transaction_index,
                },
            )
            events.append(event)
            effects.append(
                LedgerEffect(
                    event_id=event.event_id,
                    account_id=variable_account.account_id,
                    posted_at=event.occurred_at,
                    direction=Direction.DEBIT,
                    amount_minor=event.amount_minor,
                    posting_priority=PostingPriority.EXPENSE,
                    entry_key=f"variable-debit:{transaction_index:04d}",
                    description=event.description,
                )
            )

        for rule in config.own_transfers:
            source_account = accounts_by_ref[rule.source_account_ref]
            destination_account = accounts_by_ref[rule.destination_account_ref]
            transfer_group_id = deterministic_id(
                namespace,
                "transfer_group",
                f"{rule.rule_id}:{month_key}",
            )
            event = FinancialEvent(
                event_id=deterministic_id(
                    namespace, "event", f"own-transfer:{rule.rule_id}:{month_key}"
                ),
                customer_id=customer_id,
                occurred_at=scheduled_date(current_month, rule.day_of_month),
                economic_type=EconomicType.OWN_TRANSFER,
                amount_minor=rule.amount_minor,
                currency=config.customer.currency,
                source_entity=source_account.account_id,
                destination_entity=destination_account.account_id,
                description="OWN ACCOUNT TRANSFER",
                metadata={"rule_id": rule.rule_id, "transfer_group_id": transfer_group_id},
            )
            events.append(event)
            effects.extend(
                (
                    LedgerEffect(
                        event_id=event.event_id,
                        account_id=source_account.account_id,
                        posted_at=event.occurred_at,
                        direction=Direction.DEBIT,
                        amount_minor=event.amount_minor,
                        posting_priority=PostingPriority.OWN_TRANSFER,
                        entry_key=f"transfer-debit:{rule.source_account_ref}",
                        transfer_group_id=transfer_group_id,
                        description=rule.outgoing_description,
                    ),
                    LedgerEffect(
                        event_id=event.event_id,
                        account_id=destination_account.account_id,
                        posted_at=event.occurred_at,
                        direction=Direction.CREDIT,
                        amount_minor=event.amount_minor,
                        posting_priority=PostingPriority.OWN_TRANSFER,
                        entry_key=f"transfer-credit:{rule.destination_account_ref}",
                        transfer_group_id=transfer_group_id,
                        description=rule.incoming_description,
                    ),
                )
            )

    end_date = month_end(month_start(config.scenario.start_date, simulation_months - 1))
    card_simulation = simulate_cards(
        config=config,
        cards_by_ref=cards_by_ref,
        customer_id=customer_id,
        start_date=config.scenario.start_date,
        end_date=end_date,
        months=simulation_months,
        namespace=namespace,
    )
    events.extend(card_simulation.events)
    effects.extend(card_simulation.payment_effects)

    ordered_events = tuple(sorted(events, key=lambda event: (event.occurred_at, event.event_id)))
    ledger_entries = post_ledger_effects(twin.accounts, effects, namespace)
    validate_account_ledgers(twin.accounts, ledger_entries)
    validate_transfer_pairs(ordered_events, ledger_entries)
    validate_card_simulation(
        cards=cards_by_ref.values(),
        purchases=card_simulation.purchases,
        installments=card_simulation.installments,
        invoices=card_simulation.invoices,
        snapshots=card_simulation.limit_snapshots,
        events=ordered_events,
        entries=ledger_entries,
        start_date=config.scenario.start_date,
        end_date=end_date,
        months=simulation_months,
    )

    return SimulationRun(
        run_id=run_id,
        seed=seed,
        months=simulation_months,
        start_date=config.scenario.start_date,
        end_date=end_date,
        config_sha256=fingerprint,
        customer_twin=twin,
        events=ordered_events,
        ledger_entries=ledger_entries,
        profile=_profile,
        cards=tuple(sorted(cards_by_ref.values(), key=lambda card: card.card_id)),
        card_purchases=card_simulation.purchases,
        card_installments=card_simulation.installments,
        card_invoices=card_simulation.invoices,
        credit_limit_snapshots=card_simulation.limit_snapshots,
    )


__all__ = ["simulate_v1"]
