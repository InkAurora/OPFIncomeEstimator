"""Normalize versioned simulator observations into estimator contract 1.0."""

from __future__ import annotations

from finances_simulator.generation import GeneratedScenario
from finances_simulator.integration.contracts import (
    EstimatorAccountV1,
    EstimatorCoverageV1,
    EstimatorInputV1,
    EstimatorInvestmentTransactionV1,
    EstimatorLoanV1,
    EstimatorTransactionV1,
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


__all__ = ["build_estimator_input"]
