"""Versioned deterministic estimator pipelines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from income_estimator.contracts import (
    ESTIMATOR_CONTRACT_VERSION,
    ArtifactMetadata,
    EstimationAudit,
    IncomeEstimateV1,
    MonthlyReconstructionAudit,
    validate_estimator_input,
)
from income_estimator.income_streams import detect_income_streams
from income_estimator.models import (
    GradientBoostedTransactionClassifier,
    reconstruct_monthly_income,
    reconstruct_recurring_income,
)
from income_estimator.models.transaction_classifier import MODEL_FEATURE_VERSION
from income_estimator.transaction_intelligence import (
    FEATURE_VERSION,
    IncomeRuleClassifier,
    ModelIncomeClassifier,
    RuleConfig,
    extract_transaction_features,
)

ESTIMATOR_VERSION = "rule-based-0.1.0"
RECURRING_ESTIMATOR_VERSION = "recurring-streams-0.2.0"
RECURRING_FEATURE_VERSION = "income-stream-features-1.0.0"
SUPERVISED_ESTIMATOR_VERSION = "supervised-transactions-0.3.0"


def _baseline_reconstruction_audit(
    request: Any,
    decisions: tuple[Any, ...],
    monthly: tuple[Any, ...],
) -> tuple[MonthlyReconstructionAudit, ...]:
    amount_by_id = {item.transaction_id: item.amount_minor for item in request.transactions}
    result: list[MonthlyReconstructionAudit] = []
    for estimate in monthly:
        observed = sum(
            amount_by_id[transaction_id]
            for transaction_id in estimate.contributing_transaction_ids
        )
        adjustment = estimate.estimated_income_minor - observed
        reasons = ["OBSERVED_INCOME"] if observed else ["NO_INCOME_EVIDENCE"]
        if adjustment:
            reasons.append("COVERAGE_SCALING_APPLIED")
        result.append(
            MonthlyReconstructionAudit(
                month=estimate.month,
                observed_income_minor=observed,
                imputed_income_minor=0,
                coverage_adjustment_minor=adjustment,
                estimated_income_minor=estimate.estimated_income_minor,
                contributing_transaction_ids=estimate.contributing_transaction_ids,
                reason_codes=tuple(reasons),
            )
        )
    return tuple(result)


class RuleBasedIncomeEstimator:
    """Observation-only deterministic estimator compatible with simulator contract 1.0."""

    estimator_version = ESTIMATOR_VERSION
    feature_version = FEATURE_VERSION
    model_versions: tuple[str, ...] = ()

    def __init__(self, rule_config: RuleConfig | None = None) -> None:
        self.classifier = IncomeRuleClassifier(rule_config)

    def estimate(self, request: Any) -> IncomeEstimateV1:
        """Return shared boundary output without exposing internal audit extensions."""

        return self.explain(request).estimate

    def explain(self, request: Any) -> EstimationAudit:
        """Return estimate plus every transaction decision and detected stream."""

        validated = validate_estimator_input(request)
        features = extract_transaction_features(validated)
        decisions = tuple(self.classifier.classify(item) for item in features)
        posted_at_by_id = {
            transaction.transaction_id: transaction.posted_at
            for transaction in validated.transactions
        }
        account_id_by_id = {
            transaction.transaction_id: transaction.account_id
            for transaction in validated.transactions
        }
        streams = detect_income_streams(decisions, posted_at_by_id, account_id_by_id)
        monthly, monthly_audits = self._reconstruct(validated, decisions, streams)
        estimate = IncomeEstimateV1(
            estimator_version=self.estimator_version,
            run_id=validated.run_id,
            customer_id=validated.customer_id,
            currency=validated.currency,
            monthly_estimates=monthly,
        )
        return EstimationAudit(
            metadata=ArtifactMetadata(
                estimator_version=self.estimator_version,
                feature_version=self.feature_version,
                input_contract_version=validated.schema_version,
                output_contract_version=ESTIMATOR_CONTRACT_VERSION,
                model_versions=self.model_versions,
            ),
            estimate=estimate,
            transaction_decisions=decisions,
            income_streams=streams,
            monthly_reconstructions=monthly_audits,
        )

    @staticmethod
    def _reconstruct(request, decisions, streams):
        monthly = reconstruct_monthly_income(request, decisions)
        return monthly, _baseline_reconstruction_audit(request, decisions, monthly)


class RecurringIncomeEstimator(RuleBasedIncomeEstimator):
    """Estimator 0.2: reconstruct stable stream gaps under incomplete coverage."""

    estimator_version = RECURRING_ESTIMATOR_VERSION
    feature_version = RECURRING_FEATURE_VERSION

    @staticmethod
    def _reconstruct(request, decisions, streams):
        return reconstruct_recurring_income(request, decisions, streams)


class SupervisedIncomeEstimator(RecurringIncomeEstimator):
    """Estimator 0.3 candidate; safety exclusions remain deterministic."""

    estimator_version = SUPERVISED_ESTIMATOR_VERSION
    feature_version = MODEL_FEATURE_VERSION

    def __init__(self, model_path: Path, rule_config: RuleConfig | None = None) -> None:
        model = GradientBoostedTransactionClassifier.from_path(model_path)
        self.classifier = ModelIncomeClassifier(
            model,
            IncomeRuleClassifier(rule_config),
        )
        self.model_versions = (model.artifact.model_version,)


__all__ = [
    "ESTIMATOR_VERSION",
    "RECURRING_ESTIMATOR_VERSION",
    "SUPERVISED_ESTIMATOR_VERSION",
    "RecurringIncomeEstimator",
    "RuleBasedIncomeEstimator",
    "SupervisedIncomeEstimator",
]
