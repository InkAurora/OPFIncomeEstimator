"""Small auditable estimator used for Phase-7 end-to-end evaluation."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from finances_simulator.integration.contracts import (
    EstimatorInputV1,
    IncomeEstimateV1,
    MonthlyIncomeEstimateV1,
)
from finances_simulator.simulation.primitives import month_start


class BaselineIncomeEstimator:
    """Estimate observed monthly credits after conservative known exclusions."""

    estimator_version = "baseline-1.0.0"

    def estimate(self, request: EstimatorInputV1) -> IncomeEstimateV1:
        excluded = self._excluded_transaction_ids(request)
        credits_by_month: dict[str, list[object]] = defaultdict(list)
        for transaction in request.transactions:
            if transaction.direction == "CREDIT" and transaction.transaction_id not in excluded:
                credits_by_month[transaction.posted_at[:7]].append(transaction)

        effective_coverage = self._effective_coverage_basis_points(request)
        start = date.fromisoformat(request.window_start)
        monthly_estimates: list[MonthlyIncomeEstimateV1] = []
        for index in range(request.months):
            month = month_start(start, index).strftime("%Y-%m")
            contributors = sorted(
                credits_by_month.get(month, ()),
                key=lambda item: item.transaction_id,
            )
            observed_amount = sum(item.amount_minor for item in contributors)
            estimate = (
                (observed_amount * 10_000 + effective_coverage // 2)
                // effective_coverage
                if effective_coverage
                else observed_amount
            )
            uncertainty_basis_points = max(1_000, 10_000 - effective_coverage)
            uncertainty = (
                estimate * uncertainty_basis_points + 5_000
            ) // 10_000
            monthly_estimates.append(
                MonthlyIncomeEstimateV1(
                    month=month,
                    estimated_income_minor=estimate,
                    confidence_lower_minor=max(0, estimate - uncertainty),
                    confidence_upper_minor=estimate + uncertainty,
                    contributing_transaction_ids=tuple(
                        item.transaction_id for item in contributors
                    ),
                )
            )

        return IncomeEstimateV1(
            estimator_version=self.estimator_version,
            run_id=request.run_id,
            customer_id=request.customer_id,
            currency=request.currency,
            monthly_estimates=tuple(monthly_estimates),
        )

    @staticmethod
    def _effective_coverage_basis_points(request: EstimatorInputV1) -> int:
        eligible = sum(item.eligible_record_count for item in request.coverage)
        observed = sum(item.observed_original_record_count for item in request.coverage)
        if eligible:
            return max(1, (observed * 10_000 + eligible // 2) // eligible)
        if request.coverage:
            return max(
                1,
                sum(item.effective_coverage_basis_points for item in request.coverage)
                // len(request.coverage),
            )
        return 10_000

    @staticmethod
    def _excluded_transaction_ids(request: EstimatorInputV1) -> set[str]:
        excluded: set[str] = set()
        for transaction in request.transactions:
            if transaction.duplicate_of_transaction_id is not None:
                excluded.add(transaction.transaction_id)
            if transaction.reversal_of_transaction_id is not None:
                excluded.add(transaction.transaction_id)
                excluded.add(transaction.reversal_of_transaction_id)

        excluded.update(item.disbursement_transaction_id for item in request.loans)
        excluded.update(
            item.related_account_transaction_id
            for item in request.investment_transactions
            if item.transaction_type == "REDEMPTION"
            and item.related_account_transaction_id is not None
        )

        # Detect visible own-account transfer pairs without using private transfer labels.
        debits_by_key: dict[tuple[str, int], list[object]] = defaultdict(list)
        for transaction in request.transactions:
            if transaction.direction == "DEBIT":
                debits_by_key[(transaction.posted_at, transaction.amount_minor)].append(
                    transaction
                )
        for transaction in request.transactions:
            if transaction.direction != "CREDIT":
                continue
            candidates = debits_by_key.get(
                (transaction.posted_at, transaction.amount_minor), ()
            )
            if any(candidate.account_id != transaction.account_id for candidate in candidates):
                excluded.add(transaction.transaction_id)

        keywords = (
            "LOAN DISBURSEMENT",
            "INVESTMENT REDEMPTION",
            "TRANSFER FROM",
            "OWN TRANSFER",
        )
        for transaction in request.transactions:
            normalized = transaction.description.upper()
            if any(keyword in normalized for keyword in keywords):
                excluded.add(transaction.transaction_id)
        return excluded


__all__ = ["BaselineIncomeEstimator"]
