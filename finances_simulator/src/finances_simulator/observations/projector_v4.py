"""Project schema-1.4 observations without private transition or anomaly labels."""

from dataclasses import dataclass
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from finances_simulator.contracts.observed_v4 import (
    AccountV4,
    BalanceV4,
    CardInvoiceItemV4,
    CardInvoiceV4,
    CardTransactionV4,
    CreditCardV4,
    CreditLimitV4,
    InvestmentBalanceV4,
    InvestmentTransactionV4,
    InvestmentV4,
    LoanBalanceV4,
    LoanPaymentV4,
    LoanV4,
    TransactionV4,
)
from finances_simulator.observations.projector_v3 import project_observations_v3
from finances_simulator.simulation.engine import SimulationRun

_RecordV4 = TypeVar("_RecordV4", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ObservationBundleV4:
    """Complete estimator-safe schema-1.4 datasets."""

    accounts: tuple[AccountV4, ...]
    balances: tuple[BalanceV4, ...]
    transactions: tuple[TransactionV4, ...]
    credit_cards: tuple[CreditCardV4, ...]
    credit_limits: tuple[CreditLimitV4, ...]
    credit_card_transactions: tuple[CardTransactionV4, ...]
    credit_card_invoices: tuple[CardInvoiceV4, ...]
    credit_card_invoice_items: tuple[CardInvoiceItemV4, ...]
    loans: tuple[LoanV4, ...]
    loan_payments: tuple[LoanPaymentV4, ...]
    loan_balances: tuple[LoanBalanceV4, ...]
    investments: tuple[InvestmentV4, ...]
    investment_transactions: tuple[InvestmentTransactionV4, ...]
    investment_balances: tuple[InvestmentBalanceV4, ...]


def _upgrade(record: BaseModel, model: type[_RecordV4]) -> _RecordV4:
    return model.model_validate(record.model_dump(exclude={"schema_version"}))


def project_observations_v4(
    run: SimulationRun,
    *,
    namespace: UUID,
) -> ObservationBundleV4:
    """Upgrade complete observations while exposing no private Phase-5 labels."""

    base = project_observations_v3(run, namespace=namespace)
    return ObservationBundleV4(
        accounts=tuple(_upgrade(item, AccountV4) for item in base.accounts),
        balances=tuple(_upgrade(item, BalanceV4) for item in base.balances),
        transactions=tuple(_upgrade(item, TransactionV4) for item in base.transactions),
        credit_cards=tuple(_upgrade(item, CreditCardV4) for item in base.credit_cards),
        credit_limits=tuple(_upgrade(item, CreditLimitV4) for item in base.credit_limits),
        credit_card_transactions=tuple(
            _upgrade(item, CardTransactionV4) for item in base.credit_card_transactions
        ),
        credit_card_invoices=tuple(
            _upgrade(item, CardInvoiceV4) for item in base.credit_card_invoices
        ),
        credit_card_invoice_items=tuple(
            _upgrade(item, CardInvoiceItemV4) for item in base.credit_card_invoice_items
        ),
        loans=tuple(_upgrade(item, LoanV4) for item in base.loans),
        loan_payments=tuple(_upgrade(item, LoanPaymentV4) for item in base.loan_payments),
        loan_balances=tuple(_upgrade(item, LoanBalanceV4) for item in base.loan_balances),
        investments=tuple(_upgrade(item, InvestmentV4) for item in base.investments),
        investment_transactions=tuple(
            _upgrade(item, InvestmentTransactionV4) for item in base.investment_transactions
        ),
        investment_balances=tuple(
            _upgrade(item, InvestmentBalanceV4) for item in base.investment_balances
        ),
    )


__all__ = ["ObservationBundleV4", "project_observations_v4"]
