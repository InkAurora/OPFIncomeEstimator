"""Deterministic fixed-income investment simulation."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from finances_simulator.config_v2 import InvestmentFlowRule, ScenarioConfigV2
from finances_simulator.domain.accounts import Account, Direction
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.domain.investments import (
    Investment,
    InvestmentBalanceSnapshot,
    InvestmentTransaction,
    InvestmentTransactionType,
)
from finances_simulator.ledger.effects import LedgerEffect, PostingPriority
from finances_simulator.simulation.primitives import (
    deterministic_id,
    month_end,
    month_start,
    scheduled_date,
)


class InvestmentSimulationError(ValueError):
    """Raised when configured investment state cannot be simulated consistently."""


@dataclass(frozen=True, slots=True)
class InvestmentSimulation:
    """Accepted investment movements, valuations, and deposit-account effects."""

    investments: tuple[Investment, ...]
    transactions: tuple[InvestmentTransaction, ...]
    balance_snapshots: tuple[InvestmentBalanceSnapshot, ...]
    events: tuple[FinancialEvent, ...]
    effects: tuple[LedgerEffect, ...]


def monthly_return_minor(balance_minor: int, monthly_return_basis_points: int) -> int:
    """Return one month's investment gain using integer half-up rounding."""

    if balance_minor < 0:
        raise ValueError("balance_minor must be non-negative")
    if monthly_return_basis_points < 0:
        raise ValueError("monthly_return_basis_points must be non-negative")
    numerator = balance_minor * monthly_return_basis_points
    quotient, remainder = divmod(numerator, 10_000)
    return quotient + (1 if remainder * 2 >= 10_000 else 0)


def _flow_candidates(
    *,
    rules: tuple[InvestmentFlowRule, ...] | list[InvestmentFlowRule],
    transaction_type: InvestmentTransactionType,
    start_date: date,
    months: int,
) -> list[tuple[date, int, str, int, InvestmentTransactionType, InvestmentFlowRule]]:
    priority = 0 if transaction_type is InvestmentTransactionType.CONTRIBUTION else 1
    candidates: list[tuple[date, int, str, int, InvestmentTransactionType, InvestmentFlowRule]] = []
    for rule in rules:
        if rule.start_month_index >= months:
            continue
        occurrences_in_window = min(
            rule.occurrences,
            (months - 1 - rule.start_month_index) // rule.interval_months + 1,
        )
        for occurrence_index in range(occurrences_in_window):
            month_index = rule.start_month_index + occurrence_index * rule.interval_months
            occurred_at = scheduled_date(
                month_start(start_date, month_index),
                rule.day_of_month,
            )
            candidates.append(
                (
                    occurred_at,
                    priority,
                    rule.rule_id,
                    occurrence_index,
                    transaction_type,
                    rule,
                )
            )
    return candidates


def simulate_investments(
    *,
    config: ScenarioConfigV2,
    investments_by_ref: dict[str, Investment],
    accounts_by_ref: dict[str, Account],
    customer_id: str,
    start_date: date,
    end_date: date,
    months: int,
    namespace: UUID,
) -> InvestmentSimulation:
    """Apply external flows by date, then credit each month-end return."""

    del end_date  # The simulation window is represented exactly by start_date and months.
    configured_refs = {settings.investment_ref for settings in config.investments}
    if set(investments_by_ref) != configured_refs:
        raise InvestmentSimulationError(
            "investments_by_ref must match every configured investment exactly"
        )
    if months <= 0:
        raise InvestmentSimulationError("months must be positive")

    investments = tuple(sorted(investments_by_ref.values(), key=lambda item: item.investment_id))
    for investment in investments:
        if investment.customer_id != customer_id:
            raise InvestmentSimulationError(
                f"Investment {investment.investment_id} does not belong to customer {customer_id}."
            )

    candidates = _flow_candidates(
        rules=config.investment_contribution_rules,
        transaction_type=InvestmentTransactionType.CONTRIBUTION,
        start_date=start_date,
        months=months,
    )
    candidates.extend(
        _flow_candidates(
            rules=config.investment_redemption_rules,
            transaction_type=InvestmentTransactionType.REDEMPTION,
            start_date=start_date,
            months=months,
        )
    )
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    balances = {
        investment.investment_id: investment.opening_balance_minor for investment in investments
    }
    transactions: list[InvestmentTransaction] = []
    snapshots: list[InvestmentBalanceSnapshot] = []
    events: list[FinancialEvent] = []
    effects: list[LedgerEffect] = []
    candidate_index = 0

    for month_index in range(months):
        current_month = month_start(start_date, month_index)
        reference_date = month_end(current_month)
        while (
            candidate_index < len(candidates) and candidates[candidate_index][0] <= reference_date
        ):
            (
                occurred_at,
                _,
                _,
                occurrence_index,
                transaction_type,
                rule,
            ) = candidates[candidate_index]
            candidate_index += 1
            investment = investments_by_ref[rule.investment_ref]
            account = accounts_by_ref.get(rule.account_ref)
            if account is None:
                raise InvestmentSimulationError(
                    f"Investment flow {rule.rule_id!r} references missing account "
                    f"{rule.account_ref!r}."
                )
            balance = balances[investment.investment_id]
            if (
                transaction_type is InvestmentTransactionType.REDEMPTION
                and rule.amount_minor > balance
            ):
                continue

            is_contribution = transaction_type is InvestmentTransactionType.CONTRIBUTION
            balance = (
                balance + rule.amount_minor if is_contribution else balance - rule.amount_minor
            )
            balances[investment.investment_id] = balance
            transaction_key = transaction_type.value.lower()
            transaction_id = deterministic_id(
                namespace,
                "investment_transaction",
                (f"{investment.investment_id}:{transaction_key}:{rule.rule_id}:{occurrence_index}"),
            )
            event_id = deterministic_id(
                namespace,
                "event",
                f"investment-{transaction_key}:{transaction_id}",
            )
            transactions.append(
                InvestmentTransaction(
                    transaction_id=transaction_id,
                    event_id=event_id,
                    customer_id=customer_id,
                    investment_id=investment.investment_id,
                    occurred_at=occurred_at,
                    transaction_type=transaction_type,
                    amount_minor=rule.amount_minor,
                    currency=investment.currency,
                    description=rule.description,
                    balance_after_minor=balance,
                    account_id=account.account_id,
                    rule_id=rule.rule_id,
                    occurrence_index=occurrence_index,
                )
            )
            economic_type = (
                EconomicType.INVESTMENT_CONTRIBUTION
                if is_contribution
                else EconomicType.INVESTMENT_REDEMPTION
            )
            events.append(
                FinancialEvent(
                    event_id=event_id,
                    customer_id=customer_id,
                    occurred_at=occurred_at,
                    economic_type=economic_type,
                    amount_minor=rule.amount_minor,
                    currency=investment.currency,
                    source_entity=(
                        account.account_id if is_contribution else investment.investment_id
                    ),
                    destination_entity=(
                        investment.investment_id if is_contribution else account.account_id
                    ),
                    description=rule.description,
                    metadata={
                        "investment_id": investment.investment_id,
                        "investment_transaction_id": transaction_id,
                        "rule_id": rule.rule_id,
                        "occurrence_index": occurrence_index,
                    },
                )
            )
            effects.append(
                LedgerEffect(
                    event_id=event_id,
                    account_id=account.account_id,
                    posted_at=occurred_at,
                    direction=Direction.DEBIT if is_contribution else Direction.CREDIT,
                    amount_minor=rule.amount_minor,
                    posting_priority=(
                        PostingPriority.INVESTMENT_CONTRIBUTION
                        if is_contribution
                        else PostingPriority.INVESTMENT_REDEMPTION
                    ),
                    entry_key=f"investment-{transaction_key}:{transaction_id}",
                    description=rule.description,
                )
            )

        for investment in investments:
            balance = balances[investment.investment_id]
            return_minor = monthly_return_minor(
                balance,
                investment.monthly_return_basis_points,
            )
            if return_minor > 0:
                balance += return_minor
                balances[investment.investment_id] = balance
                month_key = current_month.strftime("%Y-%m")
                transaction_id = deterministic_id(
                    namespace,
                    "investment_transaction",
                    f"{investment.investment_id}:return:{month_key}",
                )
                event_id = deterministic_id(
                    namespace,
                    "event",
                    f"investment-return:{transaction_id}",
                )
                transactions.append(
                    InvestmentTransaction(
                        transaction_id=transaction_id,
                        event_id=event_id,
                        customer_id=customer_id,
                        investment_id=investment.investment_id,
                        occurred_at=reference_date,
                        transaction_type=InvestmentTransactionType.RETURN,
                        amount_minor=return_minor,
                        currency=investment.currency,
                        description=investment.return_description,
                        balance_after_minor=balance,
                    )
                )
                events.append(
                    FinancialEvent(
                        event_id=event_id,
                        customer_id=customer_id,
                        occurred_at=reference_date,
                        economic_type=EconomicType.INVESTMENT_RETURN,
                        amount_minor=return_minor,
                        currency=investment.currency,
                        source_entity=investment.institution_id,
                        destination_entity=investment.investment_id,
                        description=investment.return_description,
                        metadata={
                            "investment_id": investment.investment_id,
                            "investment_transaction_id": transaction_id,
                            "monthly_return_basis_points": (investment.monthly_return_basis_points),
                        },
                    )
                )

            snapshots.append(
                InvestmentBalanceSnapshot(
                    snapshot_id=deterministic_id(
                        namespace,
                        "investment_balance",
                        f"{investment.investment_id}:{reference_date.isoformat()}",
                    ),
                    customer_id=customer_id,
                    investment_id=investment.investment_id,
                    reference_date=reference_date,
                    balance_minor=balance,
                    currency=investment.currency,
                )
            )

    return InvestmentSimulation(
        investments=investments,
        transactions=tuple(transactions),
        balance_snapshots=tuple(snapshots),
        events=tuple(sorted(events, key=lambda item: (item.occurred_at, item.event_id))),
        effects=tuple(
            sorted(
                effects,
                key=lambda item: (item.posted_at, item.posting_priority, item.event_id),
            )
        ),
    )


__all__ = [
    "InvestmentSimulation",
    "InvestmentSimulationError",
    "monthly_return_minor",
    "simulate_investments",
]
