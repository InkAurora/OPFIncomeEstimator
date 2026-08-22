from __future__ import annotations

from income_estimator.pipeline import RecurringIncomeEstimator, RuleBasedIncomeEstimator


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


def _incomplete_coverage() -> list[dict[str, object]]:
    return [
        {
            "schema_version": "1.0",
            "customer_id": "customer-test",
            "account_id": "checking",
            "configured_coverage_percent": 100,
            "eligible_record_count": 10,
            "observed_original_record_count": 9,
            "effective_coverage_basis_points": 9_000,
        }
    ]


def test_recurring_estimator_imputes_internal_gap(request_payload, transaction) -> None:
    payload = request_payload(
        months=6,
        transactions=[
            transaction("salary-1", posted_at="2026-01-05"),
            transaction("salary-2", posted_at="2026-02-05"),
            transaction("salary-4", posted_at="2026-04-05"),
            transaction("salary-5", posted_at="2026-05-05"),
            transaction("salary-6", posted_at="2026-06-05"),
        ],
    )
    payload["coverage"] = _incomplete_coverage()

    audit = RecurringIncomeEstimator().explain(payload)

    assert [item.estimated_income_minor for item in audit.estimate.monthly_estimates] == [
        500_000,
        500_000,
        500_000,
        500_000,
        500_000,
        500_000,
    ]
    assert audit.income_streams[0].pattern == "RECURRING_SOURCE"
    march = audit.monthly_reconstructions[2]
    assert march.observed_income_minor == 0
    assert march.imputed_income_minor == 500_000
    assert march.reason_codes == ("RECURRING_STREAM_GAP_IMPUTED",)
    assert march.imputed_stream_ids == (audit.income_streams[0].stream_id,)


def test_recurring_estimator_does_not_invent_full_coverage_income(
    request_payload,
    transaction,
) -> None:
    payload = request_payload(
        months=5,
        transactions=[
            transaction("salary-1", posted_at="2026-01-05"),
            transaction("salary-2", posted_at="2026-02-05"),
            transaction("salary-4", posted_at="2026-04-05"),
            transaction("salary-5", posted_at="2026-05-05"),
        ],
    )

    estimate = RecurringIncomeEstimator().estimate(payload)

    assert estimate.monthly_estimates[2].estimated_income_minor == 0
    assert estimate.monthly_estimates[2].contributing_transaction_ids == ()


def test_recurring_estimator_imputes_single_missing_edge_month(
    request_payload,
    transaction,
) -> None:
    payload = request_payload(
        months=5,
        transactions=[
            transaction("salary-2", posted_at="2026-02-05"),
            transaction("salary-3", posted_at="2026-03-05"),
            transaction("salary-4", posted_at="2026-04-05"),
            transaction("salary-5", posted_at="2026-05-05"),
        ],
    )
    payload["coverage"] = _incomplete_coverage()

    estimate = RecurringIncomeEstimator().estimate(payload)

    assert estimate.monthly_estimates[0].estimated_income_minor == 500_000
    assert estimate.monthly_estimates[0].contributing_transaction_ids
