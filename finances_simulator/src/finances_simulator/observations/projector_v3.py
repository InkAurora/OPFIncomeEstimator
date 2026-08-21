"""Project schema-1.3 observations without exposing factory truth."""

from dataclasses import dataclass
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from finances_simulator.contracts.observed_v3 import (
    AccountV3,
    BalanceV3,
    CardInvoiceItemV3,
    CardInvoiceV3,
    CardTransactionV3,
    CreditCardV3,
    CreditLimitV3,
    InvestmentBalanceV3,
    InvestmentTransactionV3,
    InvestmentV3,
    LoanBalanceV3,
    LoanPaymentV3,
    LoanV3,
    TransactionV3,
)
from finances_simulator.observations.projector_v2 import project_observations_v2
from finances_simulator.simulation.engine import SimulationRun

_RecordV3 = TypeVar("_RecordV3", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ObservationBundleV3:
    """Complete estimator-safe schema-1.3 datasets."""

    accounts: tuple[AccountV3, ...]
    balances: tuple[BalanceV3, ...]
    transactions: tuple[TransactionV3, ...]
    credit_cards: tuple[CreditCardV3, ...]
    credit_limits: tuple[CreditLimitV3, ...]
    credit_card_transactions: tuple[CardTransactionV3, ...]
    credit_card_invoices: tuple[CardInvoiceV3, ...]
    credit_card_invoice_items: tuple[CardInvoiceItemV3, ...]
    loans: tuple[LoanV3, ...]
    loan_payments: tuple[LoanPaymentV3, ...]
    loan_balances: tuple[LoanBalanceV3, ...]
    investments: tuple[InvestmentV3, ...]
    investment_transactions: tuple[InvestmentTransactionV3, ...]
    investment_balances: tuple[InvestmentBalanceV3, ...]


def _upgrade(record: BaseModel, model: type[_RecordV3]) -> _RecordV3:
    """Copy a field-compatible schema-1.2 record into schema 1.3."""

    return model.model_validate(record.model_dump(exclude={"schema_version"}))


def project_observations_v3(
    run: SimulationRun,
    *,
    namespace: UUID,
) -> ObservationBundleV3:
    """Upgrade V2 financial observations without adding latent factory fields."""

    base = project_observations_v2(run, namespace=namespace)
    return ObservationBundleV3(
        accounts=tuple(_upgrade(item, AccountV3) for item in base.accounts),
        balances=tuple(_upgrade(item, BalanceV3) for item in base.balances),
        transactions=tuple(_upgrade(item, TransactionV3) for item in base.transactions),
        credit_cards=tuple(_upgrade(item, CreditCardV3) for item in base.credit_cards),
        credit_limits=tuple(_upgrade(item, CreditLimitV3) for item in base.credit_limits),
        credit_card_transactions=tuple(
            _upgrade(item, CardTransactionV3) for item in base.credit_card_transactions
        ),
        credit_card_invoices=tuple(
            _upgrade(item, CardInvoiceV3) for item in base.credit_card_invoices
        ),
        credit_card_invoice_items=tuple(
            _upgrade(item, CardInvoiceItemV3) for item in base.credit_card_invoice_items
        ),
        loans=tuple(_upgrade(item, LoanV3) for item in base.loans),
        loan_payments=tuple(_upgrade(item, LoanPaymentV3) for item in base.loan_payments),
        loan_balances=tuple(_upgrade(item, LoanBalanceV3) for item in base.loan_balances),
        investments=tuple(_upgrade(item, InvestmentV3) for item in base.investments),
        investment_transactions=tuple(
            _upgrade(item, InvestmentTransactionV3) for item in base.investment_transactions
        ),
        investment_balances=tuple(
            _upgrade(item, InvestmentBalanceV3) for item in base.investment_balances
        ),
    )


__all__ = ["ObservationBundleV3", "project_observations_v3"]
