"""Versioned deterministic estimator pipelines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from income_estimator.assessment import assess_month, recurring_unrecognized_months
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
)
from income_estimator.contracts.output_v1_2 import (
    ASSESSMENT_SUPPORTED,
    IncomeEstimateV12,
    MonthlyIncomeEstimateV12,
)
from income_estimator.income_streams import detect_income_streams
from income_estimator.models import (
    GradientBoostedTransactionClassifier,
    reconstruct_monthly_income,
    reconstruct_recurring_income,
)
from income_estimator.models.capacity import GradientBoostedCapacityModel
from income_estimator.models.ensemble import ENSEMBLE_VERSION, combine_month
from income_estimator.models.quantiles import (
    ConformalIntervalModel,
    require_capacity_binding,
    require_routing_binding,
)
from income_estimator.models.transaction_classifier import MODEL_FEATURE_VERSION
from income_estimator.transaction_intelligence import (
    FEATURE_VERSION,
    IncomeRuleClassifier,
    ModelIncomeClassifier,
    RuleConfig,
    extract_transaction_features,
)

ESTIMATOR_VERSION = "rule-based-0.1.1"
RECURRING_ESTIMATOR_VERSION = "recurring-streams-0.3.0"
RECURRING_FEATURE_VERSION = "income-stream-features-1.0.0"
SUPERVISED_ESTIMATOR_VERSION = "supervised-transactions-0.3.0"
ENSEMBLE_ESTIMATOR_VERSION = "ensemble-0.8.0"


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
        # The 0.1 baseline reports observed income as is; since 0.1.1 nothing scales it, so the
        # coverage adjustment is always zero and stays in the audit only for schema stability.
        reasons = ["OBSERVED_INCOME"] if observed else ["NO_INCOME_EVIDENCE"]
        result.append(
            MonthlyReconstructionAudit(
                month=estimate.month,
                observed_income_minor=observed,
                imputed_income_minor=0,
                coverage_adjustment_minor=0,
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
        # The two artifacts are loaded independently but are not independent. Intervals are offsets
        # on the residual of the capacity-routed estimate, so a calibration paired with capacity
        # bytes it was never fitted against publishes a `p10`/`p90` label over an unmeasured
        # quantity. Checked here, at construction, because every later caller has already lost the
        # paths.
        if self.intervals is not None:
            require_capacity_binding(
                self.intervals.artifact,
                capacity_model_version=(
                    self.capacity.artifact.model_version if self.capacity is not None else None
                ),
                capacity_artifact_sha256=(
                    self.capacity.artifact_sha256 if self.capacity is not None else None
                ),
            )
            # Routing is the other half of what the residuals were taken around, and until schema
            # 1.6 nothing recorded it.
            require_routing_binding(self.intervals.artifact, ensemble_version=ENSEMBLE_VERSION)
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
        """Return the `1.2` assessment downgraded to `1.1` for consumers that have not moved.

        There is one code path. `1.1` is produced by dropping what `1.2` added, through the adapter
        on the record itself, so the two versions cannot drift apart or disagree about a number.
        """

        return self.estimate_v1_2(request).to_v1_1()

    def estimate_v1_2(self, request: Any) -> IncomeEstimateV12:
        """Return realized and sustainable estimates, each month with its evidence assessed."""

        validated = validate_estimator_input(request)
        audit = self.explain(validated)
        baseline = RuleBasedIncomeEstimator(self.rule_config)
        baseline_by_month = {
            item.month: item.estimated_income_minor
            for item in baseline.estimate(validated).monthly_estimates
        }
        features_by_month = self._features_by_month(validated)
        excluded_by_month: dict[str, list[str]] = {}
        unclassified_by_month: dict[str, int] = {}
        for decision in audit.transaction_decisions:
            if decision.direction != "CREDIT":
                continue
            if decision.classification == "EXCLUDED":
                excluded_by_month.setdefault(decision.posted_month, []).append(
                    decision.transaction_id
                )
            elif decision.classification == "AMBIGUOUS":
                unclassified_by_month[decision.posted_month] = (
                    unclassified_by_month.get(decision.posted_month, 0)
                    + decision.amount_minor
                )

        # Window-level facts, read once. A month in which nothing was due is not a month short of
        # evidence; a window in which nothing was ever established as income is.
        observed_in_window = any(
            decision.classification != "EXCLUDED"
            or "OUTSIDE_ESTIMATION_WINDOW" not in decision.reason_codes
            for decision in audit.transaction_decisions
        )
        established_income = any(
            decision.classification == "INCOME"
            for decision in audit.transaction_decisions
        )
        unrecognized_months = recurring_unrecognized_months(audit.transaction_decisions)

        # An output month with no feature row was scored on `{}` and still published a sustainable
        # income, so a request whose `months` ran past `window_end` produced an estimate for a month
        # it never observed. The contract now refuses that request; this refuses to score the row
        # regardless, because an empty feature mapping is indistinguishable from a real one here.
        missing = [
            estimate.month
            for estimate in audit.estimate.monthly_estimates
            if estimate.month not in features_by_month
        ]
        if missing:
            raise ValueError(
                "no feature row was built for output month(s) "
                f"{', '.join(missing)}; every reconstructed month must be inside the observation "
                "window that features are built from"
            )

        monthly: list[MonthlyIncomeEstimateV12] = []
        for estimate in audit.estimate.monthly_estimates:
            features = features_by_month[estimate.month]
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
            status, reasons = assess_month(
                sustainable_income_minor=result.sustainable_income_minor,
                quantile_unavailable_reason=result.quantile_unavailable_reason,
                counted_income_minor=estimate.estimated_income_minor,
                unclassified_credit_minor=unclassified_by_month.get(estimate.month, 0),
                window_has_observed_transactions=observed_in_window,
                window_has_established_income=established_income,
                has_recurring_unrecognized_source=estimate.month in unrecognized_months,
            )
            monthly.append(
                MonthlyIncomeEstimateV12(
                    month=estimate.month,
                    assessment_status=status,
                    assessment_reason_codes=reasons,
                    sustainable_income_point_minor=result.sustainable_income_minor,
                    qualified_sustainable_income_minor=(
                        result.sustainable_income_minor
                        if status == ASSESSMENT_SUPPORTED
                        else None
                    ),
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

        return IncomeEstimateV12(
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
