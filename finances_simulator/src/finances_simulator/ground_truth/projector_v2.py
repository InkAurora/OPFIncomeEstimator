"""Project schema-1.2 private evaluation truth and balance sheets."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from finances_simulator.contracts.ground_truth_v2 import (
    BalanceSheetGroundTruthV2,
    CardTransactionGroundTruthV2,
    CustomerGroundTruthV2,
    CustomerMonthGroundTruthV2,
    InvestmentTransactionGroundTruthV2,
    LoanPaymentGroundTruthV2,
    TransactionGroundTruthV2,
)
from finances_simulator.domain.investments import InvestmentTransactionType
from finances_simulator.domain.loans import LoanPaymentStatus
from finances_simulator.ground_truth.projector_v1 import project_ground_truth_v1
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import deterministic_id, month_end


@dataclass(frozen=True, slots=True)
class GroundTruthBundleV2:
    """Complete private schema-1.2 datasets."""

    customers: tuple[CustomerGroundTruthV2, ...]
    customer_months: tuple[CustomerMonthGroundTruthV2, ...]
    transactions: tuple[TransactionGroundTruthV2, ...]
    credit_card_transactions: tuple[CardTransactionGroundTruthV2, ...]
    loan_payments: tuple[LoanPaymentGroundTruthV2, ...]
    investment_transactions: tuple[InvestmentTransactionGroundTruthV2, ...]
    balance_sheets: tuple[BalanceSheetGroundTruthV2, ...]


def project_ground_truth_v2(
    run: SimulationRun,
    *,
    namespace: UUID,
) -> GroundTruthBundleV2:
    """Build private causal labels and reconciled monthly net worth."""

    base = project_ground_truth_v1(run)
    event_by_id = {event.event_id: event for event in run.events}
    customer = base.customers[0]
    upgraded_customer = CustomerGroundTruthV2.model_validate(
        {
            **customer.model_dump(exclude={"schema_version"}),
            "loan_ids": tuple(loan.loan_id for loan in run.loans),
            "investment_ids": tuple(investment.investment_id for investment in run.investments),
            "total_opening_investment_balance_minor": sum(
                investment.opening_balance_minor for investment in run.investments
            ),
            "total_opening_loan_principal_minor": sum(
                loan.principal_minor for loan in run.loans if loan.originated_at < run.start_date
            ),
        }
    )

    interest_by_month: dict[str, int] = {}
    for payment in run.loan_payments:
        if payment.status is LoanPaymentStatus.PAID:
            month_key = payment.due_date.strftime("%Y-%m")
            interest_by_month[month_key] = (
                interest_by_month.get(month_key, 0) + payment.interest_minor
            )
    return_by_month: dict[str, int] = {}
    for transaction in run.investment_transactions:
        if transaction.transaction_type is InvestmentTransactionType.RETURN:
            month_key = transaction.occurred_at.strftime("%Y-%m")
            return_by_month[month_key] = (
                return_by_month.get(month_key, 0) + transaction.amount_minor
            )
    customer_months = tuple(
        CustomerMonthGroundTruthV2.model_validate(
            {
                **month.model_dump(exclude={"schema_version"}),
                "loan_interest_paid_minor": interest_by_month.get(month.month, 0),
                "investment_return_minor": return_by_month.get(month.month, 0),
            }
        )
        for month in base.customer_months
    )

    transactions = tuple(
        TransactionGroundTruthV2.model_validate(item.model_dump(exclude={"schema_version"}))
        for item in base.transactions
    )
    card_transactions = tuple(
        CardTransactionGroundTruthV2.model_validate(item.model_dump(exclude={"schema_version"}))
        for item in base.credit_card_transactions
    )
    loan_payments = tuple(
        LoanPaymentGroundTruthV2(
            event_id=event.event_id,
            loan_payment_id=payment.payment_id,
            customer_id=payment.customer_id,
            loan_id=payment.loan_id,
            occurred_at=payment.due_date.isoformat(),
            economic_type=event.economic_type,
            installment_number=payment.installment_number,
            installment_count=payment.installment_count,
            opening_principal_minor=payment.opening_principal_minor,
            principal_amount_minor=payment.principal_minor,
            interest_amount_minor=payment.interest_minor,
            total_amount_minor=payment.payment_minor,
            remaining_principal_after_minor=payment.remaining_principal_minor,
            currency=event.currency,
            source_entity=event.source_entity,
            destination_entity=event.destination_entity,
            description=event.description,
            metadata=event.metadata,
        )
        for payment in sorted(
            run.loan_payments,
            key=lambda item: (item.due_date, item.loan_id, item.installment_number),
        )
        if payment.status is LoanPaymentStatus.PAID and payment.payment_event_id is not None
        for event in (event_by_id[payment.payment_event_id],)
    )
    investment_transactions = tuple(
        InvestmentTransactionGroundTruthV2(
            event_id=event.event_id,
            investment_transaction_id=transaction.transaction_id,
            customer_id=transaction.customer_id,
            investment_id=transaction.investment_id,
            occurred_at=transaction.occurred_at.isoformat(),
            economic_type=event.economic_type,
            transaction_type=transaction.transaction_type,
            amount_minor=transaction.amount_minor,
            currency=transaction.currency,
            source_entity=event.source_entity,
            destination_entity=event.destination_entity,
            description=transaction.description,
            balance_after_minor=transaction.balance_after_minor,
            rule_id=transaction.rule_id,
            occurrence_index=transaction.occurrence_index,
            metadata=event.metadata,
        )
        for transaction in run.investment_transactions
        for event in (event_by_id[transaction.event_id],)
    )

    investment_close_by_month = {
        snapshot.reference_date.strftime("%Y-%m"): 0
        for snapshot in run.investment_balance_snapshots
    }
    for snapshot in run.investment_balance_snapshots:
        month_key = snapshot.reference_date.strftime("%Y-%m")
        investment_close_by_month[month_key] += snapshot.balance_minor
    loan_close_by_month = {
        snapshot.reference_date.strftime("%Y-%m"): 0 for snapshot in run.loan_balance_snapshots
    }
    for snapshot in run.loan_balance_snapshots:
        month_key = snapshot.reference_date.strftime("%Y-%m")
        loan_close_by_month[month_key] += snapshot.remaining_principal_minor

    opening_investments = sum(investment.opening_balance_minor for investment in run.investments)
    opening_loans = sum(
        loan.principal_minor for loan in run.loans if loan.originated_at < run.start_date
    )
    balance_sheets: list[BalanceSheetGroundTruthV2] = []
    for month in customer_months:
        closing_investments = investment_close_by_month.get(month.month, 0)
        closing_loans = loan_close_by_month.get(month.month, 0)
        opening_assets = month.total_deposit_opening_balance_minor + opening_investments
        opening_liabilities = month.total_card_outstanding_opening_minor + opening_loans
        closing_assets = month.total_deposit_closing_balance_minor + closing_investments
        closing_liabilities = month.total_card_outstanding_closing_minor + closing_loans
        balance_sheets.append(
            BalanceSheetGroundTruthV2(
                balance_sheet_id=deterministic_id(
                    namespace,
                    "balance_sheet",
                    month.month,
                ),
                customer_id=month.customer_id,
                month=month.month,
                reference_date=month_end(date.fromisoformat(f"{month.month}-01")).isoformat(),
                currency=month.currency,
                opening_total_deposit_balance_minor=(month.total_deposit_opening_balance_minor),
                opening_total_investment_balance_minor=opening_investments,
                opening_total_assets_minor=opening_assets,
                opening_total_card_outstanding_minor=(month.total_card_outstanding_opening_minor),
                opening_total_loan_principal_minor=opening_loans,
                opening_total_liabilities_minor=opening_liabilities,
                opening_net_worth_minor=opening_assets - opening_liabilities,
                total_deposit_balance_minor=month.total_deposit_closing_balance_minor,
                total_investment_balance_minor=closing_investments,
                total_assets_minor=closing_assets,
                total_card_outstanding_minor=(month.total_card_outstanding_closing_minor),
                total_loan_principal_minor=closing_loans,
                total_liabilities_minor=closing_liabilities,
                net_worth_minor=closing_assets - closing_liabilities,
            )
        )
        opening_investments = closing_investments
        opening_loans = closing_loans

    return GroundTruthBundleV2(
        customers=(upgraded_customer,),
        customer_months=customer_months,
        transactions=transactions,
        credit_card_transactions=card_transactions,
        loan_payments=loan_payments,
        investment_transactions=investment_transactions,
        balance_sheets=tuple(balance_sheets),
    )


__all__ = ["GroundTruthBundleV2", "project_ground_truth_v2"]
