"""Versioned deterministic estimator pipelines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from income_estimator.contracts import (
    ESTIMATOR_CONTRACT_VERSION,
    ESTIMATOR_OUTPUT_CONTRACT_VERSION,
    ArtifactMetadata,
    EstimationAudit,
    IncomeEstimateV1,
    MonthlyReconstructionAudit,
    validate_estimator_input,
)
from income_estimator.contracts.explanation_v1 import EstimationExplanationV1
from income_estimator.contracts.output_v1_1 import (
    IncomeEstimateV11,
    IncomeStreamSummaryV11,
    MonthlyIncomeEstimateV11,
)
from income_estimator.income_streams import detect_income_streams
from income_estimator.models import (
    GradientBoostedTransactionClassifier,
    reconstruct_monthly_income,
    reconstruct_recurring_income,
)
from income_estimator.models.capacity import GradientBoostedCapacityModel
from income_estimator.models.ensemble import ENSEMBLE_VERSION, combine_month
from income_estimator.models.quantiles import ConformalIntervalModel
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
ENSEMBLE_ESTIMATOR_VERSION = "ensemble-0.6.0"


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


class EnsembleIncomeEstimator(RecurringIncomeEstimator):
    """Estimator 0.6: route realized and sustainable targets and publish output 1.1.

    Realized income keeps the promoted `0.2` reconstruction; the frozen `0.1` baseline stays
    visible as a component with zero weight so the choice remains auditable. Sustainable income is
    routed between the capacity model and the deterministic baselines. The capacity artifact is
    optional: without it the estimator still answers, using the recurring-stream component, and
    says so in the routing reasons.
    """

    estimator_version = ENSEMBLE_ESTIMATOR_VERSION

    def __init__(
        self,
        capacity_model_path: Path | None = None,
        rule_config: RuleConfig | None = None,
        calibration_path: Path | None = None,
    ) -> None:
        super().__init__(rule_config)
        self.rule_config = rule_config
        self.capacity = (
            GradientBoostedCapacityModel.from_path(capacity_model_path)
            if capacity_model_path is not None
            else None
        )
        self.intervals = (
            ConformalIntervalModel.from_path(calibration_path)
            if calibration_path is not None
            else None
        )
        versions: list[str] = []
        if self.capacity is not None:
            versions.append(self.capacity.artifact.model_version)
        if self.intervals is not None:
            versions.append(self.intervals.artifact.calibration_version)
        self.model_versions = tuple(versions)

    def explain_estimate(self, request: Any) -> EstimationExplanationV1:
        """Return the production-facing explanation for a routed estimate."""

        from income_estimator.explainability import build_explanation

        validated = validate_estimator_input(request)
        return build_explanation(
            self.estimate_v1_1(validated),
            self.explain(validated),
            features_by_month=self._features_by_month(validated),
            capacity=self.capacity,
        )

    def _features_by_month(self, request: Any) -> dict[str, dict[str, float | int | None]]:
        from income_estimator.features import build_customer_month_features

        return {
            row.reference_month: row.to_mapping()
            for row in build_customer_month_features(request, self).rows
        }

    def estimate_v1_1(self, request: Any) -> IncomeEstimateV11:
        """Return realized and sustainable estimates with components and confidence."""

        validated = validate_estimator_input(request)
        audit = self.explain(validated)
        baseline = RuleBasedIncomeEstimator(self.rule_config)
        baseline_by_month = {
            item.month: item.estimated_income_minor
            for item in baseline.estimate(validated).monthly_estimates
        }
        features_by_month = self._features_by_month(validated)
        excluded_by_month: dict[str, list[str]] = {}
        for decision in audit.transaction_decisions:
            if decision.classification == "EXCLUDED" and decision.direction == "CREDIT":
                excluded_by_month.setdefault(decision.posted_month, []).append(
                    decision.transaction_id
                )

        monthly: list[MonthlyIncomeEstimateV11] = []
        for estimate in audit.estimate.monthly_estimates:
            features = features_by_month.get(estimate.month, {})
            result = combine_month(
                estimate.estimated_income_minor,
                features,
                self.capacity,
                realized_components={
                    "cashflow_baseline_0_1": baseline_by_month.get(estimate.month, 0),
                    "recurring_streams_0_2": estimate.estimated_income_minor,
                },
                realized_selected="recurring_streams_0_2",
                intervals=self.intervals,
            )
            monthly.append(
                MonthlyIncomeEstimateV11(
                    month=estimate.month,
                    estimated_income_minor=estimate.estimated_income_minor,
                    realized_income_estimate_minor=result.realized_income_minor,
                    confidence_lower_minor=estimate.confidence_lower_minor,
                    confidence_upper_minor=estimate.confidence_upper_minor,
                    contributing_transaction_ids=estimate.contributing_transaction_ids,
                    excluded_transaction_ids=tuple(
                        sorted(
                            set(excluded_by_month.get(estimate.month, ()))
                            - set(estimate.contributing_transaction_ids)
                        )
                    ),
                    sustainable_income_p10_minor=result.sustainable_lower_minor,
                    sustainable_income_p50_minor=result.sustainable_income_minor,
                    sustainable_income_p90_minor=result.sustainable_upper_minor,
                    quantile_unavailable_reason=result.quantile_unavailable_reason,
                    component_estimates=result.components,
                    component_disagreement_basis_points=result.disagreement_basis_points,
                    confidence_score_basis_points=result.confidence_score_basis_points,
                    confidence_components=result.confidence_components,
                    routing_reason_codes=result.routing_reason_codes,
                )
            )

        return IncomeEstimateV11(
            estimator_version=self.estimator_version,
            run_id=validated.run_id,
            customer_id=validated.customer_id,
            currency=validated.currency,
            monthly_estimates=tuple(monthly),
            feature_version=self.feature_version,
            input_contract_version=validated.schema_version,
            model_versions=self.model_versions,
            component_versions=(
                ESTIMATOR_VERSION,
                RECURRING_ESTIMATOR_VERSION,
                ENSEMBLE_VERSION,
            ),
            income_streams=tuple(
                IncomeStreamSummaryV11(
                    stream_id=stream.stream_id,
                    counterparty_cluster=stream.counterparty_cluster,
                    first_seen=stream.first_seen,
                    last_seen=stream.last_seen,
                    frequency=stream.frequency,
                    median_amount_minor=stream.median_amount_minor,
                    recurrence_score_basis_points=stream.recurrence_score_basis_points,
                    pattern=stream.pattern,
                    transaction_ids=stream.transaction_ids,
                )
                for stream in audit.income_streams
            ),
        )


__all__ = [
    "ENSEMBLE_ESTIMATOR_VERSION",
    "ESTIMATOR_CONTRACT_VERSION",
    "ESTIMATOR_OUTPUT_CONTRACT_VERSION",
    "ESTIMATOR_VERSION",
    "RECURRING_ESTIMATOR_VERSION",
    "SUPERVISED_ESTIMATOR_VERSION",
    "EnsembleIncomeEstimator",
    "RecurringIncomeEstimator",
    "RuleBasedIncomeEstimator",
    "SupervisedIncomeEstimator",
]
