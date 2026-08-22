"""Deterministic schema-1.6 projection adding a corrected re-post to every reversal.

Contract `1.5` injected an artifact reversal and stopped, so the observed ledger lost the reversed
amount permanently while private truth kept it. ADR 0004 records the repair: the reversal is still
an observation artifact, and a bank that posts an erroneous reversal follows it with a correction.
Schema `1.6` emits that correction, so the observed feed reconverges to truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from finances_simulator.config_v6 import ScenarioConfigV6
from finances_simulator.contracts.observed_v6 import (
    AccountV6,
    BalanceV6,
    CardInvoiceItemV6,
    CardInvoiceV6,
    CardTransactionV6,
    CreditCardV6,
    CreditLimitV6,
    InvestmentBalanceV6,
    InvestmentTransactionV6,
    InvestmentV6,
    LoanBalanceV6,
    LoanPaymentV6,
    LoanV6,
    ObservationCoverageV6,
    TransactionV6,
)
from finances_simulator.observations.projector_v5 import (
    _delay_days,
    _upgrade,
    project_observations_v5,
)
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import deterministic_id

_OutputRecord = TypeVar("_OutputRecord", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ObservationBundleV6:
    """Complete estimator-safe schema-1.6 datasets and coverage metrics."""

    accounts: tuple[AccountV6, ...]
    balances: tuple[BalanceV6, ...]
    transactions: tuple[TransactionV6, ...]
    credit_cards: tuple[CreditCardV6, ...]
    credit_limits: tuple[CreditLimitV6, ...]
    credit_card_transactions: tuple[CardTransactionV6, ...]
    credit_card_invoices: tuple[CardInvoiceV6, ...]
    credit_card_invoice_items: tuple[CardInvoiceItemV6, ...]
    loans: tuple[LoanV6, ...]
    loan_payments: tuple[LoanPaymentV6, ...]
    loan_balances: tuple[LoanBalanceV6, ...]
    investments: tuple[InvestmentV6, ...]
    investment_transactions: tuple[InvestmentTransactionV6, ...]
    investment_balances: tuple[InvestmentBalanceV6, ...]
    observation_coverage: tuple[ObservationCoverageV6, ...]


def project_observations_v6(
    run: SimulationRun,
    config: ScenarioConfigV6,
    *,
    world_namespace: UUID,
    observation_namespace: UUID,
) -> ObservationBundleV6:
    """Version V5 observations and correct every artifact reversal with a re-post."""

    base = project_observations_v5(
        run,
        config,
        world_namespace=world_namespace,
        observation_namespace=observation_namespace,
    )
    maximum_delay = config.observation_degradation.maximum_reversal_delay_days

    transactions = [_upgrade(item, TransactionV6) for item in base.transactions]
    original_by_id = {
        item.transaction_id: item
        for item in transactions
        if item.duplicate_of_transaction_id is None
        and item.reversal_of_transaction_id is None
    }

    reposts: list[TransactionV6] = []
    repost_count_by_account: dict[str, int] = {}
    for item in transactions:
        original_id = item.reversal_of_transaction_id
        if original_id is None:
            continue
        original = original_by_id[original_id]
        # The correction arrives after the reversal it repairs, never before it.
        repost_date = date.fromisoformat(item.observed_at) + timedelta(
            days=_delay_days(
                observation_namespace,
                "repost-days",
                original_id,
                maximum_delay,
            )
        )
        # The description is the original's, so the re-post never reads as a reversal to a
        # description rule and the correction can actually land.
        reposts.append(
            original.model_copy(
                update={
                    "transaction_id": deterministic_id(
                        observation_namespace,
                        "observation_repost",
                        original_id,
                    ),
                    "observed_at": repost_date.isoformat(),
                    "repost_of_transaction_id": original_id,
                }
            )
        )
        repost_count_by_account[original.account_id] = (
            repost_count_by_account.get(original.account_id, 0) + 1
        )

    transactions.extend(reposts)
    transactions.sort(key=lambda item: (item.observed_at, item.transaction_id))

    coverage = tuple(
        _upgrade(
            item,
            ObservationCoverageV6,
            repost_record_count=repost_count_by_account.get(item.account_id, 0),
        )
        for item in base.observation_coverage
    )

    return ObservationBundleV6(
        accounts=tuple(_upgrade(item, AccountV6) for item in base.accounts),
        balances=tuple(_upgrade(item, BalanceV6) for item in base.balances),
        transactions=tuple(transactions),
        credit_cards=tuple(_upgrade(item, CreditCardV6) for item in base.credit_cards),
        credit_limits=tuple(_upgrade(item, CreditLimitV6) for item in base.credit_limits),
        credit_card_transactions=tuple(
            _upgrade(item, CardTransactionV6) for item in base.credit_card_transactions
        ),
        credit_card_invoices=tuple(
            _upgrade(item, CardInvoiceV6) for item in base.credit_card_invoices
        ),
        credit_card_invoice_items=tuple(
            _upgrade(item, CardInvoiceItemV6) for item in base.credit_card_invoice_items
        ),
        loans=tuple(_upgrade(item, LoanV6) for item in base.loans),
        loan_payments=tuple(_upgrade(item, LoanPaymentV6) for item in base.loan_payments),
        loan_balances=tuple(_upgrade(item, LoanBalanceV6) for item in base.loan_balances),
        investments=tuple(_upgrade(item, InvestmentV6) for item in base.investments),
        investment_transactions=tuple(
            _upgrade(item, InvestmentTransactionV6) for item in base.investment_transactions
        ),
        investment_balances=tuple(
            _upgrade(item, InvestmentBalanceV6) for item in base.investment_balances
        ),
        observation_coverage=coverage,
    )


__all__ = ["ObservationBundleV6", "project_observations_v6"]
