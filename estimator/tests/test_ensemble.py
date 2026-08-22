from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from income_estimator.contracts import IncomeEstimateV1, MonthlyIncomeEstimateV1
from income_estimator.contracts.output_v1_1 import (
    ComponentEstimateV11,
    IncomeEstimateV11,
    MonthlyIncomeEstimateV11,
)
from income_estimator.models.ensemble import ENSEMBLE_VERSION
from income_estimator.pipeline import EnsembleIncomeEstimator

CAPACITY_MODEL_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "capacity-estimator-0.5.0.json"
)


def _month(**overrides) -> dict[str, object]:
    payload = {
        "month": "2026-06",
        "estimated_income_minor": 500_000,
        "realized_income_estimate_minor": 500_000,
        "confidence_lower_minor": 450_000,
        "confidence_upper_minor": 550_000,
    }
    payload.update(overrides)
    return payload


def test_realized_field_must_agree_with_the_1_0_field() -> None:
    with pytest.raises(ValidationError, match="must equal estimated_income_minor"):
        MonthlyIncomeEstimateV11.model_validate(
            _month(realized_income_estimate_minor=400_000)
        )


def test_interval_requires_a_point_estimate() -> None:
    with pytest.raises(ValidationError, match="requires a p50"):
        MonthlyIncomeEstimateV11.model_validate(
            _month(sustainable_income_p10_minor=100_000)
        )


def test_quantiles_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="must be ordered"):
        MonthlyIncomeEstimateV11.model_validate(
            _month(
                sustainable_income_p10_minor=900_000,
                sustainable_income_p50_minor=500_000,
                sustainable_income_p90_minor=100_000,
            )
        )


def test_point_estimate_without_interval_must_say_why() -> None:
    with pytest.raises(ValidationError, match="quantile_unavailable_reason"):
        MonthlyIncomeEstimateV11.model_validate(
            _month(sustainable_income_p50_minor=500_000)
        )

    record = MonthlyIncomeEstimateV11.model_validate(
        _month(
            sustainable_income_p50_minor=500_000,
            quantile_unavailable_reason="UNCALIBRATED_INTERVAL",
        )
    )
    assert record.sustainable_income_p10_minor is None


def test_a_transaction_cannot_be_contributing_and_excluded() -> None:
    with pytest.raises(ValidationError, match="both contributing and excluded"):
        MonthlyIncomeEstimateV11.model_validate(
            _month(
                contributing_transaction_ids=("salary",),
                excluded_transaction_ids=("salary",),
            )
        )


def test_duplicate_component_names_are_rejected() -> None:
    component = ComponentEstimateV11(
        component="capacity_model",
        target="SUSTAINABLE_MONTHLY_INCOME",
        estimate_minor=1,
        weight_basis_points=0,
    )

    with pytest.raises(ValidationError, match="component names must be unique"):
        MonthlyIncomeEstimateV11.model_validate(
            _month(component_estimates=(component, component))
        )


def _payload(request_payload, transaction, *, months: int = 6, amounts=None):
    amounts = amounts or [500_000] * months
    return request_payload(
        transactions=[
            transaction(
                f"salary-{index:02d}",
                posted_at=f"2026-{index:02d}-05",
                amount_minor=amounts[index - 1],
            )
            for index in range(1, months + 1)
        ],
        months=months,
    )


def test_ensemble_emits_both_targets_and_stays_1_0_readable(
    request_payload,
    transaction,
) -> None:
    payload = _payload(request_payload, transaction)

    estimate = EnsembleIncomeEstimator(CAPACITY_MODEL_PATH).estimate_v1_1(payload)
    month = estimate.monthly_estimates[-1]

    assert isinstance(estimate, IncomeEstimateV11)
    assert estimate.estimator_version == "ensemble-0.6.0"
    assert ENSEMBLE_VERSION in estimate.component_versions
    assert estimate.model_versions == ("capacity-gbdt-stumps-0.5.0",)
    assert month.realized_income_estimate_minor == 500_000
    assert month.sustainable_income_p50_minor is not None
    assert month.sustainable_income_p10_minor is None
    assert month.quantile_unavailable_reason == "UNCALIBRATED_INTERVAL"

    legacy = IncomeEstimateV1(
        estimator_version=estimate.estimator_version,
        run_id=estimate.run_id,
        customer_id=estimate.customer_id,
        currency=estimate.currency,
        monthly_estimates=tuple(
            MonthlyIncomeEstimateV1(
                month=item.month,
                estimated_income_minor=item.estimated_income_minor,
                confidence_lower_minor=item.confidence_lower_minor,
                confidence_upper_minor=item.confidence_upper_minor,
                contributing_transaction_ids=item.contributing_transaction_ids,
            )
            for item in estimate.monthly_estimates
        ),
    )
    assert len(legacy.monthly_estimates) == len(estimate.monthly_estimates)
    assert legacy.monthly_estimates[-1].estimated_income_minor == 500_000


def test_every_component_stays_visible_with_its_weight(
    request_payload,
    transaction,
) -> None:
    payload = _payload(request_payload, transaction)

    month = EnsembleIncomeEstimator(CAPACITY_MODEL_PATH).estimate_v1_1(
        payload
    ).monthly_estimates[-1]
    weights = {item.component: item.weight_basis_points for item in month.component_estimates}
    targets = {item.component: item.target for item in month.component_estimates}

    assert weights["cashflow_baseline_0_1"] == 0
    assert weights["recurring_streams_0_2"] == 10_000
    assert targets["cashflow_baseline_0_1"] == "REALIZED_INCOME_MONTH"
    assert targets["capacity_model"] == "SUSTAINABLE_MONTHLY_INCOME"
    assert sum(1 for value in weights.values() if value == 10_000) == 2


def test_stable_income_routes_to_cash_flow_and_volatile_routes_to_the_model(
    request_payload,
    transaction,
) -> None:
    """The one documented exception: stable income is already answered by last month."""

    stable = _payload(request_payload, transaction)
    volatile = _payload(
        request_payload,
        transaction,
        amounts=[900_000, 100_000, 800_000, 200_000, 700_000, 300_000],
    )
    estimator = EnsembleIncomeEstimator(CAPACITY_MODEL_PATH)

    stable_reasons = estimator.estimate_v1_1(stable).monthly_estimates[-1].routing_reason_codes
    volatile_reasons = (
        estimator.estimate_v1_1(volatile).monthly_estimates[-1].routing_reason_codes
    )

    assert "STABLE_INCOME_PREFERS_CASH_FLOW" in stable_reasons
    assert "CAPACITY_MODEL_SELECTED" in volatile_reasons
    assert "VOLATILE_INCOME" in volatile_reasons


def test_without_a_capacity_artifact_the_ensemble_still_answers(
    request_payload,
    transaction,
) -> None:
    payload = _payload(request_payload, transaction)

    estimate = EnsembleIncomeEstimator().estimate_v1_1(payload)
    month = estimate.monthly_estimates[-1]

    assert estimate.model_versions == ()
    assert month.sustainable_income_p50_minor is not None
    assert month.routing_reason_codes == ("CAPACITY_MODEL_UNAVAILABLE",)
    assert all(
        item.component != "capacity_model" for item in month.component_estimates
    )


def test_confidence_is_capped_by_observed_coverage(request_payload, transaction) -> None:
    payload = _payload(request_payload, transaction)
    payload["coverage"] = [
        {
            "schema_version": "1.0",
            "customer_id": "customer-test",
            "account_id": "checking",
            "configured_coverage_percent": 40,
            "eligible_record_count": 10,
            "observed_original_record_count": 4,
            "effective_coverage_basis_points": 4_000,
        }
    ]

    month = EnsembleIncomeEstimator(CAPACITY_MODEL_PATH).estimate_v1_1(
        payload
    ).monthly_estimates[-1]
    components = {item.name: item.value_basis_points for item in month.confidence_components}

    assert month.confidence_score_basis_points is not None
    assert month.confidence_score_basis_points <= components["data_coverage"]
    assert components["income_stability"] == 10_000


def test_ensemble_is_deterministic(request_payload, transaction) -> None:
    payload = _payload(request_payload, transaction)
    estimator = EnsembleIncomeEstimator(CAPACITY_MODEL_PATH)

    first = estimator.estimate_v1_1(payload)
    second = estimator.estimate_v1_1(payload)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_excluded_credits_are_reported_beside_contributing_ones(
    request_payload,
    transaction,
) -> None:
    payload = request_payload(
        transactions=[
            transaction("salary", posted_at="2026-01-05"),
            transaction(
                "refund",
                posted_at="2026-01-11",
                amount_minor=10_000,
                description="PURCHASE REFUND",
            ),
        ],
        months=1,
    )

    month = EnsembleIncomeEstimator(CAPACITY_MODEL_PATH).estimate_v1_1(
        payload
    ).monthly_estimates[0]

    assert month.contributing_transaction_ids == ("salary",)
    assert month.excluded_transaction_ids == ("refund",)
