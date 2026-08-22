"""Estimator 0.1 runtime pipeline."""

from __future__ import annotations

from typing import Any

from income_estimator.contracts import (
    ESTIMATOR_CONTRACT_VERSION,
    ArtifactMetadata,
    EstimationAudit,
    IncomeEstimateV1,
    validate_estimator_input,
)
from income_estimator.income_streams import detect_income_streams
from income_estimator.models import reconstruct_monthly_income
from income_estimator.transaction_intelligence import (
    FEATURE_VERSION,
    IncomeRuleClassifier,
    RuleConfig,
    extract_transaction_features,
)

ESTIMATOR_VERSION = "rule-based-0.1.0"


class RuleBasedIncomeEstimator:
    """Observation-only deterministic estimator compatible with simulator contract 1.0."""

    estimator_version = ESTIMATOR_VERSION

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
        streams = detect_income_streams(decisions, posted_at_by_id)
        monthly = reconstruct_monthly_income(validated, decisions)
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
                feature_version=FEATURE_VERSION,
                input_contract_version=ESTIMATOR_CONTRACT_VERSION,
                output_contract_version=ESTIMATOR_CONTRACT_VERSION,
            ),
            estimate=estimate,
            transaction_decisions=decisions,
            income_streams=streams,
        )


__all__ = ["ESTIMATOR_VERSION", "RuleBasedIncomeEstimator"]
