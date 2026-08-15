"""Project schema-1.2 estimator observations from reconciled hidden state."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel

from finances_simulator.contracts.observed_v2 import (
    AccountV2,
    BalanceV2,
    CardInvoiceItemV2,
    CardInvoiceV2,
    CardTransactionV2,
    CreditCardV2,
    CreditLimitV2,
    InvestmentBalanceV2,
    InvestmentTransactionV2,
    InvestmentV2,
    LoanBalanceV2,
    LoanPaymentV2,
    LoanV2,
    TransactionV2,
)
from finances_simulator.domain.loans import LoanPaymentStatus
from finances_simulator.observations.projector_v1 import project_observations_v1
from finances_simulator.simulation.engine import SimulationRun


@dataclass(frozen=True, slots=True)
class ObservationBundleV2:
    """Complete estimator-safe schema-1.2 datasets."""

    accounts: tuple[AccountV2, ...]
    balances: tuple[BalanceV2, ...]
    transactions: tuple[TransactionV2, ...]
    credit_cards: tuple[CreditCardV2, ...]
    credit_limits: tuple[CreditLimitV2, ...]
    credit_card_transactions: tuple[CardTransactionV2, ...]
    credit_card_invoices: tuple[CardInvoiceV2, ...]
    credit_card_invoice_items: tuple[CardInvoiceItemV2, ...]
    loans: tuple[LoanV2, ...]
    loan_payments: tuple[LoanPaymentV2, ...]
    loan_balances: tuple[LoanBalanceV2, ...]
    investments: tuple[InvestmentV2, ...]
    investment_transactions: tuple[InvestmentTransactionV2, ...]
    investment_balances: tuple[InvestmentBalanceV2, ...]


def _upgrade(record: BaseModel, model: type[BaseModel]) -> BaseModel:
    """Copy a frozen 1.1 record into its field-compatible 1.2 contract."""

    return model.model_validate(record.model_dump(exclude={"schema_version"}))


def project_observations_v2(
    run: SimulationRun,
    *,
    namespace: UUID,
) -> ObservationBundleV2:
    """Build public deposit, card, loan, and investment observations."""

    base = project_observations_v1(
        accounts=run.customer_twin.accounts,
        ledger_entries=run.ledger_entries,
        cards=run.cards,
        card_purchases=run.card_purchases,
        card_installments=run.card_installments,
        card_invoices=run.card_invoices,
        credit_limit_snapshots=run.credit_limit_snapshots,
        start_date=run.start_date,
        end_date=run.end_date,
        months=run.months,
        namespace=namespace,
    )
    entry_by_event = {entry.event_id: entry for entry in run.ledger_entries}
    final_loan_balance = {
        snapshot.loan_id: snapshot.remaining_principal_minor
        for snapshot in run.loan_balance_snapshots
    }

    loans = tuple(
        LoanV2(
            loan_id=loan.loan_id,
            customer_id=loan.customer_id,
            institution_id=loan.institution_id,
            institution_name=loan.institution_name,
            loan_label=loan.loan_label,
            loan_type=loan.loan_type,
            currency=loan.currency,
            originated_at=loan.originated_at.isoformat(),
            original_principal_minor=loan.principal_minor,
            annual_interest_basis_points=loan.annual_interest_basis_points,
            term_months=loan.term_months,
            amortization_system=loan.amortization_system,
            status=("PAID_OFF" if final_loan_balance.get(loan.loan_id) == 0 else "ACTIVE"),
            disbursement_transaction_id=entry_by_event[loan.disbursement_event_id].entry_id,
        )
        for loan in sorted(run.loans, key=lambda item: item.loan_id)
    )
    loan_payments = tuple(
        LoanPaymentV2(
            loan_payment_id=payment.payment_id,
            customer_id=payment.customer_id,
            loan_id=payment.loan_id,
            installment_number=payment.installment_number,
            installment_count=payment.installment_count,
            due_date=payment.due_date.isoformat(),
            principal_amount_minor=payment.principal_minor,
            interest_amount_minor=payment.interest_minor,
            total_amount_minor=payment.payment_minor,
            paid_amount_minor=payment.payment_minor,
            remaining_principal_after_minor=payment.remaining_principal_minor,
            currency=next(loan.currency for loan in run.loans if loan.loan_id == payment.loan_id),
            paid_at=payment.due_date.isoformat(),
            payment_transaction_id=entry_by_event[payment.payment_event_id].entry_id,
        )
        for payment in sorted(
            run.loan_payments,
            key=lambda item: (item.due_date, item.loan_id, item.installment_number),
        )
        if payment.status is LoanPaymentStatus.PAID and payment.payment_event_id is not None
    )
    loan_balances = tuple(
        LoanBalanceV2(
            loan_balance_id=snapshot.snapshot_id,
            customer_id=snapshot.customer_id,
            loan_id=snapshot.loan_id,
            reference_date=snapshot.reference_date.isoformat(),
            remaining_principal_minor=snapshot.remaining_principal_minor,
            currency=snapshot.currency,
        )
        for snapshot in sorted(
            run.loan_balance_snapshots,
            key=lambda item: (item.reference_date, item.loan_id),
        )
    )
    investments = tuple(
        InvestmentV2(
            investment_id=investment.investment_id,
            customer_id=investment.customer_id,
            institution_id=investment.institution_id,
            institution_name=investment.institution_name,
            investment_label=investment.investment_label,
            investment_type=investment.investment_type,
            currency=investment.currency,
            opened_on=investment.opened_on.isoformat(),
        )
        for investment in sorted(run.investments, key=lambda item: item.investment_id)
    )
    investment_transactions = tuple(
        InvestmentTransactionV2(
            investment_transaction_id=transaction.transaction_id,
            customer_id=transaction.customer_id,
            investment_id=transaction.investment_id,
            occurred_at=transaction.occurred_at.isoformat(),
            transaction_type=transaction.transaction_type,
            amount_minor=transaction.amount_minor,
            currency=transaction.currency,
            description=transaction.description,
            balance_after_minor=transaction.balance_after_minor,
            related_account_transaction_id=(
                entry_by_event[transaction.event_id].entry_id
                if transaction.account_id is not None
                else None
            ),
        )
        for transaction in run.investment_transactions
    )
    investment_balances = tuple(
        InvestmentBalanceV2(
            investment_balance_id=snapshot.snapshot_id,
            customer_id=snapshot.customer_id,
            investment_id=snapshot.investment_id,
            reference_date=snapshot.reference_date.isoformat(),
            balance_minor=snapshot.balance_minor,
            currency=snapshot.currency,
        )
        for snapshot in sorted(
            run.investment_balance_snapshots,
            key=lambda item: (item.reference_date, item.investment_id),
        )
    )

    return ObservationBundleV2(
        accounts=tuple(_upgrade(item, AccountV2) for item in base.accounts),
        balances=tuple(_upgrade(item, BalanceV2) for item in base.balances),
        transactions=tuple(_upgrade(item, TransactionV2) for item in base.transactions),
        credit_cards=tuple(_upgrade(item, CreditCardV2) for item in base.credit_cards),
        credit_limits=tuple(_upgrade(item, CreditLimitV2) for item in base.credit_limits),
        credit_card_transactions=tuple(
            _upgrade(item, CardTransactionV2) for item in base.credit_card_transactions
        ),
        credit_card_invoices=tuple(
            _upgrade(item, CardInvoiceV2) for item in base.credit_card_invoices
        ),
        credit_card_invoice_items=tuple(
            _upgrade(item, CardInvoiceItemV2) for item in base.credit_card_invoice_items
        ),
        loans=loans,
        loan_payments=loan_payments,
        loan_balances=loan_balances,
        investments=investments,
        investment_transactions=investment_transactions,
        investment_balances=investment_balances,
    )


__all__ = ["ObservationBundleV2", "project_observations_v2"]
