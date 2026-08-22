from __future__ import annotations

from income_estimator.pipeline import RuleBasedIncomeEstimator


def test_pipeline_is_deterministic_and_detects_monthly_stream(request_payload, transaction) -> None:
    payload = request_payload(
        transactions=[
            transaction("salary-2", posted_at="2026-02-05", amount_minor=510_000),
            transaction("salary-1", posted_at="2026-01-05", amount_minor=500_000),
        ]
    )
    estimator = RuleBasedIncomeEstimator()

    first = estimator.explain(payload)
    second = estimator.explain(payload)

    assert first == second
    assert [item.estimated_income_minor for item in first.estimate.monthly_estimates] == [
        500_000,
        510_000,
    ]
    assert len(first.income_streams) == 1
    assert first.income_streams[0].frequency == "MONTHLY"
    assert first.income_streams[0].transaction_ids == ("salary-1", "salary-2")
    assert first.metadata.model_versions == ()


def test_account_coverage_adjusts_visible_income(request_payload, transaction) -> None:
    payload = request_payload(transactions=[transaction("salary", amount_minor=400_000)])
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

    estimate = RuleBasedIncomeEstimator().estimate(payload).monthly_estimates[0]

    assert estimate.estimated_income_minor == 1_000_000
    assert estimate.confidence_lower_minor == 400_000
    assert estimate.confidence_upper_minor == 1_600_000
