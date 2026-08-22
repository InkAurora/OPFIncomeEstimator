"""Supervised classifier composed with non-overridable safety rules."""

from __future__ import annotations

from income_estimator.contracts.audit import TransactionDecision
from income_estimator.models.transaction_classifier import (
    GradientBoostedTransactionClassifier,
)
from income_estimator.transaction_intelligence.features import TransactionFeatures
from income_estimator.transaction_intelligence.rules import IncomeRuleClassifier


class ModelIncomeClassifier:
    """Apply model only after deterministic exclusions have run."""

    def __init__(
        self,
        model: GradientBoostedTransactionClassifier,
        rules: IncomeRuleClassifier | None = None,
    ) -> None:
        self.model = model
        self.rules = rules or IncomeRuleClassifier()

    def classify(self, features: TransactionFeatures) -> TransactionDecision:
        rule_decision = self.rules.classify(features)
        if rule_decision.classification == "EXCLUDED":
            return rule_decision

        probability = self.model.predict_income_basis_points(features)
        threshold = self.model.artifact.decision_threshold_basis_points
        if probability >= threshold:
            classification = "INCOME"
            model_reason = "MODEL_INCOME_PROBABILITY"
        else:
            classification = "AMBIGUOUS"
            model_reason = "MODEL_NON_INCOME_PROBABILITY"
        return rule_decision.model_copy(
            update={
                "classification": classification,
                "income_probability_basis_points": probability,
                "reason_codes": (model_reason, *rule_decision.reason_codes),
            }
        )


__all__ = ["ModelIncomeClassifier"]
