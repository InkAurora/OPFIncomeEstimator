"""Assemble the estimator explanation from evidence the pipeline already produced.

Nothing here re-decides anything. Every value is read from the audit, the routed output, or the
capacity model's own additive decomposition, so an explanation can never disagree with the estimate
it explains.
"""

from __future__ import annotations

from collections.abc import Mapping

from income_estimator.contracts.audit import EstimationAudit, TransactionDecision
from income_estimator.contracts.explanation_v1 import (
    ESTIMATOR_EXPLANATION_CONTRACT_VERSION,
    CapacityExplanationV1,
    EstimationExplanationV1,
    FeatureContributionV1,
    MonthlyExplanationV1,
    TransactionExplanationV1,
)
from income_estimator.contracts.output_v1_1 import (
    ESTIMATOR_OUTPUT_CONTRACT_VERSION,
    IncomeEstimateV11,
)
from income_estimator.models.capacity import GradientBoostedCapacityModel

MAXIMUM_REPORTED_CONTRIBUTIONS = 15


def _transaction_explanation(decision: TransactionDecision) -> TransactionExplanationV1:
    return TransactionExplanationV1(
        transaction_id=decision.transaction_id,
        posted_month=decision.posted_month,
        amount_minor=decision.amount_minor,
        decision=decision.classification,
        income_probability_basis_points=decision.income_probability_basis_points,
        reason_codes=decision.reason_codes,
        counterparty_cluster=decision.counterparty_cluster or "unavailable",
    )


def explain_capacity(
    capacity: GradientBoostedCapacityModel,
    features: Mapping[str, float | int | None],
    *,
    maximum_contributions: int = MAXIMUM_REPORTED_CONTRIBUTIONS,
) -> CapacityExplanationV1:
    """Decompose one capacity prediction into its largest additive contributions.

    Small contributions are folded into a single residual entry rather than dropped, so the
    reported decomposition still reconstructs the prediction. A report that lists only the top
    features and silently discards the rest would not add up, and a reader would have no way to
    tell.
    """

    contributions = capacity.contributions(features)
    ordered = sorted(contributions.items(), key=lambda item: (-abs(item[1]), item[0]))
    reported = ordered[:maximum_contributions]
    remainder = sum(value for _, value in ordered[maximum_contributions:])

    entries = [
        FeatureContributionV1(
            feature_name=name,
            feature_value=features.get(name),
            log_contribution=value,
            is_missing_feature=features.get(name) is None,
        )
        for name, value in reported
    ]
    if remainder:
        entries.append(
            FeatureContributionV1(
                feature_name="other_features",
                feature_value=None,
                log_contribution=remainder,
                is_missing_feature=True,
            )
        )
    return CapacityExplanationV1(
        model_version=capacity.artifact.model_version,
        anchor_feature_name=capacity.artifact.anchor_feature_name,
        anchor_log_value=capacity.anchor_log(features),
        base_score=capacity.artifact.base_score,
        predicted_log_target=capacity.predict_log_target(features),
        predicted_minor=capacity.predict_minor(features),
        positive_gate_basis_points=capacity.predict_positive_basis_points(features),
        gate_threshold_basis_points=capacity.artifact.gate_threshold_basis_points,
        contributions=tuple(entries),
    )


def build_explanation(
    estimate: IncomeEstimateV11,
    audit: EstimationAudit,
    *,
    features_by_month: Mapping[str, Mapping[str, float | int | None]],
    capacity: GradientBoostedCapacityModel | None = None,
) -> EstimationExplanationV1:
    """Join routed output with the decisions and streams that produced it."""

    decisions_by_month: dict[str, list[TransactionDecision]] = {}
    for decision in audit.transaction_decisions:
        if decision.direction != "CREDIT":
            continue
        decisions_by_month.setdefault(decision.posted_month, []).append(decision)

    monthly: list[MonthlyExplanationV1] = []
    for item in estimate.monthly_estimates:
        decisions = sorted(
            decisions_by_month.get(item.month, ()),
            key=lambda value: value.transaction_id,
        )
        features = features_by_month.get(item.month)
        monthly.append(
            MonthlyExplanationV1(
                month=item.month,
                realized_income_estimate_minor=item.realized_income_estimate_minor,
                sustainable_income_estimate_minor=item.sustainable_income_p50_minor,
                routing_reason_codes=item.routing_reason_codes,
                component_estimates=item.component_estimates,
                confidence_score_basis_points=item.confidence_score_basis_points,
                confidence_components=item.confidence_components,
                included_transactions=tuple(
                    _transaction_explanation(decision)
                    for decision in decisions
                    if decision.classification == "INCOME"
                ),
                excluded_transactions=tuple(
                    _transaction_explanation(decision)
                    for decision in decisions
                    if decision.classification != "INCOME"
                ),
                capacity=(
                    explain_capacity(capacity, features)
                    if capacity is not None and features is not None
                    else None
                ),
            )
        )

    return EstimationExplanationV1(
        estimator_version=estimate.estimator_version,
        feature_version=estimate.feature_version,
        input_contract_version=estimate.input_contract_version,
        output_contract_version=ESTIMATOR_OUTPUT_CONTRACT_VERSION,
        model_versions=estimate.model_versions,
        component_versions=estimate.component_versions,
        run_id=estimate.run_id,
        customer_id=estimate.customer_id,
        currency=estimate.currency,
        income_streams=estimate.income_streams,
        monthly_explanations=tuple(monthly),
    )


__all__ = [
    "ESTIMATOR_EXPLANATION_CONTRACT_VERSION",
    "MAXIMUM_REPORTED_CONTRIBUTIONS",
    "build_explanation",
    "explain_capacity",
]
