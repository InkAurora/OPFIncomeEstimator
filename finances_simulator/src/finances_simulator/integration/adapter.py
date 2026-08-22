"""Normalize versioned simulator observations into estimator contract 1.0."""

from __future__ import annotations

from finances_simulator.generation import GeneratedScenario
from finances_simulator.integration.contracts import (
    EstimatorAccountV1,
    EstimatorAccountV11,
    EstimatorAccountV12,
    EstimatorBalanceV11,
    EstimatorBalanceV12,
    EstimatorCardInvoiceV12,
    EstimatorCardTransactionV12,
    EstimatorCoverageV1,
    EstimatorCoverageV11,
    EstimatorCoverageV12,
    EstimatorCreditCardV12,
    EstimatorCreditLimitV12,
    EstimatorInputV1,
    EstimatorInputV11,
    EstimatorInputV12,
    EstimatorInvestmentBalanceV12,
    EstimatorInvestmentTransactionV1,
    EstimatorInvestmentTransactionV11,
    EstimatorInvestmentTransactionV12,
    EstimatorInvestmentV12,
    EstimatorLoanBalanceV12,
    EstimatorLoanPaymentV12,
    EstimatorLoanV1,
    EstimatorLoanV11,
    EstimatorLoanV12,
    EstimatorTransactionV1,
    EstimatorTransactionV11,
    EstimatorTransactionV12,
)
from finances_simulator.validation.v7 import validate_generated_boundary


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def build_estimator_input(generated: GeneratedScenario) -> EstimatorInputV1:
    """Build an explicit allow-listed view; no private bundle is traversed."""

    validate_generated_boundary(generated)
    observations = generated.observations
    simulation = generated.simulation
    customer_id = simulation.customer_twin.customer_id
    currency = simulation.customer_twin.currency

    accounts = tuple(
        EstimatorAccountV1(
            customer_id=item.customer_id,
            account_id=item.account_id,
            institution_id=item.institution_id,
            currency=item.currency,
        )
        for item in observations.accounts
    )
    transactions = tuple(
        EstimatorTransactionV1(
            transaction_id=item.transaction_id,
            customer_id=item.customer_id,
            account_id=item.account_id,
            posted_at=item.posted_at,
            observed_at=getattr(item, "observed_at", item.posted_at),
            direction=_enum_value(item.direction),
            amount_minor=item.amount_minor,
            currency=item.currency,
            description=item.description,
            duplicate_of_transaction_id=getattr(
                item, "duplicate_of_transaction_id", None
            ),
            reversal_of_transaction_id=getattr(
                item, "reversal_of_transaction_id", None
            ),
            repost_of_transaction_id=getattr(item, "repost_of_transaction_id", None),
        )
        for item in observations.transactions
    )
    loans = tuple(
        EstimatorLoanV1(
            customer_id=item.customer_id,
            loan_id=item.loan_id,
            disbursement_transaction_id=item.disbursement_transaction_id,
        )
        for item in getattr(observations, "loans", ())
    )
    investment_transactions = tuple(
        EstimatorInvestmentTransactionV1(
            customer_id=item.customer_id,
            investment_transaction_id=item.investment_transaction_id,
            transaction_type=item.transaction_type,
            related_account_transaction_id=item.related_account_transaction_id,
        )
        for item in getattr(observations, "investment_transactions", ())
    )
    coverage_records = getattr(observations, "observation_coverage", ())
    if coverage_records:
        coverage = tuple(
            EstimatorCoverageV1(
                customer_id=item.customer_id,
                account_id=item.account_id,
                configured_coverage_percent=item.configured_coverage_percent,
                eligible_record_count=item.eligible_record_count,
                observed_original_record_count=item.observed_original_record_count,
                effective_coverage_basis_points=item.effective_coverage_basis_points,
            )
            for item in coverage_records
        )
    else:
        coverage = tuple(
            EstimatorCoverageV1(
                customer_id=customer_id,
                account_id=item.account_id,
                configured_coverage_percent=100,
                eligible_record_count=0,
                observed_original_record_count=0,
                effective_coverage_basis_points=10_000,
            )
            for item in accounts
        )

    return EstimatorInputV1(
        source_contract_schema_version=simulation.profile.contract_schema_version,
        run_id=simulation.run_id,
        customer_id=customer_id,
        currency=currency,
        window_start=simulation.start_date.isoformat(),
        window_end=simulation.end_date.isoformat(),
        months=simulation.months,
        accounts=accounts,
        transactions=transactions,
        loans=loans,
        investment_transactions=investment_transactions,
        coverage=coverage,
    )


def build_estimator_input_v1_1(generated: GeneratedScenario) -> EstimatorInputV11:
    """Build contract 1.1 solely from allow-listed observed records."""

    validate_generated_boundary(generated)
    observations = generated.observations
    simulation = generated.simulation
    customer_id = simulation.customer_twin.customer_id
    currency = simulation.customer_twin.currency
    accounts = tuple(
        EstimatorAccountV11(
            customer_id=item.customer_id,
            account_id=item.account_id,
            institution_id=item.institution_id,
            currency=item.currency,
        )
        for item in observations.accounts
    )
    transactions = tuple(
        EstimatorTransactionV11(
            transaction_id=item.transaction_id,
            customer_id=item.customer_id,
            account_id=item.account_id,
            posted_at=item.posted_at,
            observed_at=getattr(item, "observed_at", item.posted_at),
            direction=_enum_value(item.direction),
            amount_minor=item.amount_minor,
            currency=item.currency,
            description=item.description,
            duplicate_of_transaction_id=getattr(
                item, "duplicate_of_transaction_id", None
            ),
            reversal_of_transaction_id=getattr(
                item, "reversal_of_transaction_id", None
            ),
            repost_of_transaction_id=getattr(item, "repost_of_transaction_id", None),
            balance_after_minor=getattr(item, "balance_after_minor", None),
        )
        for item in observations.transactions
    )
    balances = tuple(
        EstimatorBalanceV11(
            balance_id=item.balance_id,
            customer_id=item.customer_id,
            account_id=item.account_id,
            reference_date=item.reference_date,
            balance_minor=item.balance_minor,
            currency=item.currency,
        )
        for item in observations.balances
    )
    loans = tuple(
        EstimatorLoanV11(
            customer_id=item.customer_id,
            loan_id=item.loan_id,
            disbursement_transaction_id=item.disbursement_transaction_id,
        )
        for item in getattr(observations, "loans", ())
    )
    investment_transactions = tuple(
        EstimatorInvestmentTransactionV11(
            customer_id=item.customer_id,
            investment_transaction_id=item.investment_transaction_id,
            transaction_type=item.transaction_type,
            related_account_transaction_id=item.related_account_transaction_id,
        )
        for item in getattr(observations, "investment_transactions", ())
    )
    coverage_records = getattr(observations, "observation_coverage", ())
    if coverage_records:
        coverage = tuple(
            EstimatorCoverageV11(
                customer_id=item.customer_id,
                account_id=item.account_id,
                configured_coverage_percent=item.configured_coverage_percent,
                eligible_record_count=item.eligible_record_count,
                observed_original_record_count=item.observed_original_record_count,
                effective_coverage_basis_points=item.effective_coverage_basis_points,
            )
            for item in coverage_records
        )
    else:
        coverage = tuple(
            EstimatorCoverageV11(
                customer_id=customer_id,
                account_id=item.account_id,
                configured_coverage_percent=100,
                eligible_record_count=0,
                observed_original_record_count=0,
                effective_coverage_basis_points=10_000,
            )
            for item in accounts
        )
    return EstimatorInputV11(
        source_contract_schema_version=simulation.profile.contract_schema_version,
        run_id=simulation.run_id,
        customer_id=customer_id,
        currency=currency,
        window_start=simulation.start_date.isoformat(),
        window_end=simulation.end_date.isoformat(),
        months=simulation.months,
        accounts=accounts,
        transactions=transactions,
        balances=balances,
        loans=loans,
        investment_transactions=investment_transactions,
        coverage=coverage,
    )


def _upgrade_records(records: tuple, model: type):
    return tuple(
        model.model_validate(item.model_dump(exclude={"schema_version"})) for item in records
    )


def build_estimator_input_v1_2(generated: GeneratedScenario) -> EstimatorInputV12:
    """Add observed card, loan, and investment product data to the contract 1.1 view.

    Every collection is optional. A scenario whose contract predates a product domain contributes
    no records, and the estimator reports that domain as unobserved rather than as zero. No private
    field is read: institution and product labels and interest terms are left out because the
    estimator does not need them to model capacity.
    """

    base = build_estimator_input_v1_1(generated)
    observations = generated.observations

    credit_cards = tuple(
        EstimatorCreditCardV12(
            customer_id=item.customer_id,
            currency=item.currency,
            card_id=item.card_id,
            institution_id=item.institution_id,
            opened_on=item.opened_on,
            status=item.status,
        )
        for item in getattr(observations, "credit_cards", ())
    )
    credit_limits = tuple(
        EstimatorCreditLimitV12(
            customer_id=item.customer_id,
            currency=item.currency,
            credit_limit_id=item.credit_limit_id,
            card_id=item.card_id,
            reference_date=item.reference_date,
            total_limit_minor=item.total_limit_minor,
            used_limit_minor=item.used_limit_minor,
            available_limit_minor=item.available_limit_minor,
        )
        for item in getattr(observations, "credit_limits", ())
    )
    card_transactions = tuple(
        EstimatorCardTransactionV12(
            customer_id=item.customer_id,
            currency=item.currency,
            card_transaction_id=item.card_transaction_id,
            card_id=item.card_id,
            occurred_at=item.occurred_at,
            amount_minor=item.amount_minor,
            description=item.description,
            installment_count=item.installment_count,
        )
        for item in getattr(observations, "credit_card_transactions", ())
    )
    card_invoices = tuple(
        EstimatorCardInvoiceV12(
            customer_id=item.customer_id,
            currency=item.currency,
            invoice_id=item.invoice_id,
            card_id=item.card_id,
            statement_close_date=item.statement_close_date,
            due_date=item.due_date,
            amount_due_minor=item.amount_due_minor,
            paid_amount_minor=item.paid_amount_minor,
            status=item.status,
            paid_at=item.paid_at,
        )
        for item in getattr(observations, "credit_card_invoices", ())
    )
    loan_payments = tuple(
        EstimatorLoanPaymentV12(
            customer_id=item.customer_id,
            currency=item.currency,
            loan_payment_id=item.loan_payment_id,
            loan_id=item.loan_id,
            installment_number=item.installment_number,
            installment_count=item.installment_count,
            due_date=item.due_date,
            principal_amount_minor=item.principal_amount_minor,
            interest_amount_minor=item.interest_amount_minor,
            total_amount_minor=item.total_amount_minor,
            remaining_principal_after_minor=item.remaining_principal_after_minor,
            paid_at=item.paid_at,
            payment_transaction_id=item.payment_transaction_id,
        )
        for item in getattr(observations, "loan_payments", ())
    )
    loan_balances = tuple(
        EstimatorLoanBalanceV12(
            customer_id=item.customer_id,
            currency=item.currency,
            loan_balance_id=item.loan_balance_id,
            loan_id=item.loan_id,
            reference_date=item.reference_date,
            remaining_principal_minor=item.remaining_principal_minor,
        )
        for item in getattr(observations, "loan_balances", ())
    )
    investments = tuple(
        EstimatorInvestmentV12(
            customer_id=item.customer_id,
            currency=item.currency,
            investment_id=item.investment_id,
            institution_id=item.institution_id,
            opened_on=item.opened_on,
            status=item.status,
        )
        for item in getattr(observations, "investments", ())
    )
    investment_balances = tuple(
        EstimatorInvestmentBalanceV12(
            customer_id=item.customer_id,
            currency=item.currency,
            investment_balance_id=item.investment_balance_id,
            investment_id=item.investment_id,
            reference_date=item.reference_date,
            balance_minor=item.balance_minor,
        )
        for item in getattr(observations, "investment_balances", ())
    )

    return EstimatorInputV12(
        source_contract_schema_version=base.source_contract_schema_version,
        run_id=base.run_id,
        customer_id=base.customer_id,
        currency=base.currency,
        window_start=base.window_start,
        window_end=base.window_end,
        months=base.months,
        accounts=_upgrade_records(base.accounts, EstimatorAccountV12),
        transactions=_upgrade_records(base.transactions, EstimatorTransactionV12),
        balances=_upgrade_records(base.balances, EstimatorBalanceV12),
        loans=_upgrade_records(base.loans, EstimatorLoanV12),
        investment_transactions=_upgrade_records(
            base.investment_transactions,
            EstimatorInvestmentTransactionV12,
        ),
        coverage=_upgrade_records(base.coverage, EstimatorCoverageV12),
        credit_cards=credit_cards,
        credit_limits=credit_limits,
        card_transactions=card_transactions,
        card_invoices=card_invoices,
        loan_payments=loan_payments,
        loan_balances=loan_balances,
        investments=investments,
        investment_balances=investment_balances,
    )


__all__ = [
    "build_estimator_input",
    "build_estimator_input_v1_1",
    "build_estimator_input_v1_2",
]
