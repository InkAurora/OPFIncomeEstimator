"""Phase-3 engine joining loans and investments to the Phase-2 world."""

from dataclasses import replace

from finances_simulator.config_v2 import ScenarioConfigV2
from finances_simulator.domain.accounts import Account
from finances_simulator.domain.events import EconomicType
from finances_simulator.domain.investments import Investment
from finances_simulator.domain.loans import Loan
from finances_simulator.ledger.effects import (
    LedgerEffect,
    PostingPriority,
    post_ledger_effects,
)
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.investments import simulate_investments
from finances_simulator.simulation.loans import simulate_loans
from finances_simulator.simulation.primitives import (
    V2_PROFILE,
    VersionProfile,
    deterministic_id,
    month_start,
    scheduled_date,
    simulation_namespace,
)
from finances_simulator.simulation.v1 import simulate_v1
from finances_simulator.validation import (
    validate_account_ledgers,
    validate_card_simulation,
    validate_transfer_pairs,
)
from finances_simulator.validation.v2 import (
    validate_investment_simulation,
    validate_loan_simulation,
)

_BASE_POSTING_PRIORITIES = {
    EconomicType.INCOME: PostingPriority.INCOME,
    EconomicType.OWN_TRANSFER: PostingPriority.OWN_TRANSFER,
    EconomicType.CARD_PAYMENT: PostingPriority.CARD_PAYMENT,
    EconomicType.EXPENSE: PostingPriority.EXPENSE,
}


def _phase_two_effects(run: SimulationRun) -> list[LedgerEffect]:
    """Recover explicit Phase-2 cash effects for one combined posting pass."""

    event_by_id = {event.event_id: event for event in run.events}
    return [
        LedgerEffect(
            event_id=entry.event_id,
            account_id=entry.account_id,
            posted_at=entry.posted_at,
            direction=entry.direction,
            amount_minor=entry.amount_minor,
            posting_priority=_BASE_POSTING_PRIORITIES[event_by_id[entry.event_id].economic_type],
            entry_key=f"phase-two:{entry.entry_id}",
            transfer_group_id=entry.transfer_group_id,
            description=entry.description,
        )
        for entry in run.ledger_entries
    ]


def simulate_v2(
    config: ScenarioConfigV2,
    *,
    seed: int,
    months: int | None = None,
    _profile: VersionProfile = V2_PROFILE,
    _include_salary: bool = True,
    _config_fingerprint: str | None = None,
) -> SimulationRun:
    """Create schema-1.2 hidden state and one reconciled deposit ledger."""

    base = simulate_v1(
        config,
        seed=seed,
        months=months,
        _profile=_profile,
        _include_salary=_include_salary,
        _config_fingerprint=_config_fingerprint,
    )
    namespace = simulation_namespace(
        base.config_sha256,
        seed,
        simulator_version=_profile.simulator_version,
    )
    account_by_id = {account.account_id: account for account in base.customer_twin.accounts}
    accounts_by_ref: dict[str, Account] = {
        item.account_ref: account_by_id[deterministic_id(namespace, "account", item.account_ref)]
        for item in config.accounts
    }
    institution_by_ref = {
        institution.institution_ref: institution for institution in config.institutions
    }

    loans_by_ref: dict[str, Loan] = {}
    for item in config.loans:
        originated_at = scheduled_date(
            month_start(config.scenario.start_date, item.disbursement_month_index),
            item.disbursement_day_of_month,
        )
        institution = institution_by_ref[item.institution_ref]
        loan_id = deterministic_id(namespace, "loan", item.loan_ref)
        loans_by_ref[item.loan_ref] = Loan(
            loan_id=loan_id,
            customer_id=base.customer_twin.customer_id,
            institution_id=institution.institution_id,
            institution_name=institution.institution_name,
            loan_label=item.loan_label,
            loan_type=item.loan_type,
            currency=config.customer.currency,
            originated_at=originated_at,
            principal_minor=item.principal_minor,
            annual_interest_basis_points=item.annual_interest_basis_points,
            term_months=item.term_months,
            amortization_system=item.amortization_system,
            disbursement_account_id=accounts_by_ref[item.disbursement_account_ref].account_id,
            payment_account_id=accounts_by_ref[item.payment_account_ref].account_id,
            disbursement_event_id=deterministic_id(
                namespace,
                "event",
                f"loan-disbursement:{item.loan_ref}",
            ),
            disbursement_description=item.disbursement_description,
            payment_description=item.payment_description,
        )

    investments_by_ref: dict[str, Investment] = {}
    for item in config.investments:
        institution = institution_by_ref[item.institution_ref]
        investments_by_ref[item.investment_ref] = Investment(
            investment_id=deterministic_id(
                namespace,
                "investment",
                item.investment_ref,
            ),
            customer_id=base.customer_twin.customer_id,
            institution_id=institution.institution_id,
            institution_name=institution.institution_name,
            investment_label=item.investment_label,
            investment_type=item.investment_type,
            currency=config.customer.currency,
            opened_on=config.scenario.start_date,
            opening_balance_minor=item.opening_balance_minor,
            monthly_return_basis_points=item.monthly_return_basis_points,
            return_description=item.return_description,
        )

    loan_simulation = simulate_loans(
        config=config,
        loans_by_ref=loans_by_ref,
        customer_id=base.customer_twin.customer_id,
        start_date=base.start_date,
        end_date=base.end_date,
        months=base.months,
        namespace=namespace,
    )
    investment_simulation = simulate_investments(
        config=config,
        investments_by_ref=investments_by_ref,
        accounts_by_ref=accounts_by_ref,
        customer_id=base.customer_twin.customer_id,
        start_date=base.start_date,
        end_date=base.end_date,
        months=base.months,
        namespace=namespace,
    )

    events = tuple(
        sorted(
            (*base.events, *loan_simulation.events, *investment_simulation.events),
            key=lambda event: (event.occurred_at, event.event_id),
        )
    )
    effects = [
        *_phase_two_effects(base),
        *loan_simulation.effects,
        *investment_simulation.effects,
    ]
    ledger_entries = post_ledger_effects(
        base.customer_twin.accounts,
        effects,
        namespace,
    )
    originated_loan_ids = {
        loan.loan_id for loan in loan_simulation.loans if loan.originated_at <= base.end_date
    }
    originated_loans = tuple(
        loan for loan in loan_simulation.loans if loan.loan_id in originated_loan_ids
    )
    originated_payments = tuple(
        payment for payment in loan_simulation.payments if payment.loan_id in originated_loan_ids
    )

    validate_account_ledgers(base.customer_twin.accounts, ledger_entries)
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
        loans=originated_loans,
        payments=originated_payments,
        snapshots=loan_simulation.balance_snapshots,
        events=events,
        entries=ledger_entries,
        start_date=base.start_date,
        end_date=base.end_date,
        months=base.months,
    )
    validate_investment_simulation(
        investments=investment_simulation.investments,
        transactions=investment_simulation.transactions,
        snapshots=investment_simulation.balance_snapshots,
        events=events,
        entries=ledger_entries,
        start_date=base.start_date,
        months=base.months,
    )

    return replace(
        base,
        events=events,
        ledger_entries=ledger_entries,
        loans=originated_loans,
        loan_payments=originated_payments,
        loan_balance_snapshots=loan_simulation.balance_snapshots,
        investments=investment_simulation.investments,
        investment_transactions=investment_simulation.transactions,
        investment_balance_snapshots=investment_simulation.balance_snapshots,
    )


__all__ = ["simulate_v2"]
