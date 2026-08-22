from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from income_estimator.contracts.explanation_v1 import (
    CapacityExplanationV1,
    EstimationExplanationV1,
    FeatureContributionV1,
    MonthlyExplanationV1,
    TransactionExplanationV1,
)
from income_estimator.explainability import explain_capacity
from income_estimator.models.capacity import GradientBoostedCapacityModel
from income_estimator.pipeline import EnsembleIncomeEstimator

CAPACITY_MODEL_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "capacity-estimator-0.5.0.json"
)
CALIBRATION_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "quantile-calibration-0.7.0.json"
)

PRIVATE_TRUTH_FIELDS = (
    "economic_type",
    "is_income",
    "income_source_id",
    "is_self_transfer",
    "truth_transaction_id",
    "life_event_id",
    "true_income_minor",
    "sustainable_monthly_income_minor",
    "income_profile",
)


def _payload(request_payload, transaction, *, months: int = 3):
    return request_payload(
        transactions=[
            transaction(f"salary-{index:02d}", posted_at=f"2026-{index:02d}-05")
            for index in range(1, months + 1)
        ]
        + [
            transaction(
                "refund",
                posted_at="2026-02-11",
                amount_minor=9_000,
                description="PURCHASE REFUND",
            )
        ],
        months=months,
    )


def _estimator() -> EnsembleIncomeEstimator:
    return EnsembleIncomeEstimator(
        CAPACITY_MODEL_PATH,
        calibration_path=CALIBRATION_PATH,
    )


def test_contributions_reconstruct_the_prediction() -> None:
    capacity = GradientBoostedCapacityModel.from_path(CAPACITY_MODEL_PATH)
    features = {
        "income_mean_3m_minor": 500_000,
        "income_median_12m_minor": 500_000,
        "income_1m_minor": 500_000,
        "months_observed": 12,
        "income_cv_12m": 0.02,
    }

    total = (
        capacity.anchor_log(features)
        + capacity.artifact.base_score
        + sum(capacity.contributions(features).values())
    )

    assert math.isclose(total, capacity.predict_log_target(features), rel_tol=1e-12)


def test_reported_contributions_still_add_up() -> None:
    """Truncating to the largest contributions must not silently lose the rest."""

    capacity = GradientBoostedCapacityModel.from_path(CAPACITY_MODEL_PATH)
    features = {"income_mean_3m_minor": 420_000, "months_observed": 8}

    explanation = explain_capacity(capacity, features, maximum_contributions=3)
    total = (
        explanation.anchor_log_value
        + explanation.base_score
        + sum(item.log_contribution for item in explanation.contributions)
    )

    assert len(explanation.contributions) <= 4
    assert any(item.feature_name == "other_features" for item in explanation.contributions)
    assert math.isclose(total, explanation.predicted_log_target, abs_tol=1e-9)


def test_a_decomposition_that_does_not_add_up_is_rejected() -> None:
    with pytest.raises(ValidationError, match="reconstruct predicted_log_target"):
        CapacityExplanationV1(
            model_version="test",
            anchor_feature_name="income_mean_3m_minor",
            anchor_log_value=10.0,
            base_score=0.0,
            predicted_log_target=13.0,
            predicted_minor=442_413,
            positive_gate_basis_points=9_000,
            gate_threshold_basis_points=5_000,
            contributions=(
                FeatureContributionV1(
                    feature_name="income_1m_minor",
                    feature_value=1,
                    log_contribution=0.5,
                ),
            ),
        )


def test_missing_flag_must_agree_with_the_value() -> None:
    with pytest.raises(ValidationError, match="must agree with feature_value"):
        FeatureContributionV1(
            feature_name="card_spend_3m_minor",
            feature_value=None,
            log_contribution=0.1,
            is_missing_feature=False,
        )


def test_included_and_excluded_evidence_cannot_overlap() -> None:
    included = TransactionExplanationV1(
        transaction_id="salary",
        posted_month="2026-01",
        amount_minor=500_000,
        decision="INCOME",
        income_probability_basis_points=9_500,
        reason_codes=("STRONG_INCOME_DESCRIPTION_PAYROLL",),
        counterparty_cluster="description:PAYROLL",
    )
    excluded = included.model_copy(update={"decision": "EXCLUDED"})

    with pytest.raises(ValidationError, match="both included and excluded"):
        MonthlyExplanationV1(
            month="2026-01",
            realized_income_estimate_minor=500_000,
            included_transactions=(included,),
            excluded_transactions=(excluded,),
        )


def test_explanation_traces_every_decision_and_version(
    request_payload,
    transaction,
) -> None:
    explanation = _estimator().explain_estimate(_payload(request_payload, transaction))
    february = next(
        item for item in explanation.monthly_explanations if item.month == "2026-02"
    )

    assert isinstance(explanation, EstimationExplanationV1)
    assert explanation.estimator_version == "ensemble-0.6.0"
    assert explanation.output_contract_version == "1.1"
    assert "capacity-gbdt-stumps-0.5.0" in explanation.model_versions
    assert "conformal-intervals-0.7.0" in explanation.model_versions
    assert explanation.income_streams

    assert [item.transaction_id for item in february.included_transactions] == ["salary-02"]
    excluded = {item.transaction_id: item.reason_codes for item in february.excluded_transactions}
    assert excluded["refund"] == ("EXCLUDED_DESCRIPTION_REFUND",)
    assert february.routing_reason_codes
    assert february.confidence_components
    assert february.capacity is not None


def test_explanation_agrees_with_the_estimate_it_explains(
    request_payload,
    transaction,
) -> None:
    payload = _payload(request_payload, transaction)
    estimator = _estimator()

    estimate = estimator.estimate_v1_1(payload)
    explanation = estimator.explain_estimate(payload)

    for month, explained in zip(estimate.monthly_estimates, explanation.monthly_explanations):
        assert month.month == explained.month
        assert month.realized_income_estimate_minor == explained.realized_income_estimate_minor
        assert month.sustainable_income_p50_minor == explained.sustainable_income_estimate_minor
        assert month.routing_reason_codes == explained.routing_reason_codes
        assert month.confidence_score_basis_points == explained.confidence_score_basis_points


def test_explanations_contain_no_private_label(request_payload, transaction) -> None:
    explanation = _estimator().explain_estimate(_payload(request_payload, transaction))

    payload = json.dumps(explanation.model_dump(mode="json"))

    assert all(field not in payload for field in PRIVATE_TRUTH_FIELDS)


def test_explanation_is_deterministic(request_payload, transaction) -> None:
    payload = _payload(request_payload, transaction)
    estimator = _estimator()

    assert estimator.explain_estimate(payload) == estimator.explain_estimate(payload)


def test_without_a_capacity_model_the_explanation_omits_that_section(
    request_payload,
    transaction,
) -> None:
    explanation = EnsembleIncomeEstimator().explain_estimate(
        _payload(request_payload, transaction)
    )

    assert all(item.capacity is None for item in explanation.monthly_explanations)
    assert explanation.model_versions == ()
    assert all(item.routing_reason_codes for item in explanation.monthly_explanations)


def test_every_promoted_artifact_has_a_model_card() -> None:
    """An artifact without a card is not promoted."""

    card_text = (Path(__file__).parents[1] / "docs" / "model-cards.md").read_text(
        encoding="utf-8"
    )

    for version in (
        "recurring-streams-0.2.0",
        "capacity-gbdt-stumps-0.5.0",
        "conformal-intervals-0.7.0",
    ):
        assert version in card_text
        section = card_text.split(version, 1)[1]
        assert "**Known failure modes.**" in section
    assert "transaction-gbdt-stumps-0.3.0" in card_text
