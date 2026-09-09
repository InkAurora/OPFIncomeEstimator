from __future__ import annotations

from pathlib import Path

from income_estimator.pipeline import (
    RecurringIncomeEstimator,
    RuleBasedIncomeEstimator,
    SupervisedIncomeEstimator,
)

MODEL_PATH = (
    Path(__file__).parents[1]
    / "training"
    / "artifacts"
    / "transaction-classifier-0.3.0.json"
)


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


def _scope(
    account_id: str,
    fetched_from: str,
    fetched_through: str,
    *,
    pagination_complete: bool = True,
) -> dict[str, object]:
    """A contract-1.3 consent scope: what the receiver itself fetched for one account."""

    return {
        "schema_version": "1.3",
        "customer_id": "customer-test",
        "account_id": account_id,
        "fetched_from": fetched_from,
        "fetched_through": fetched_through,
        "pagination_complete": pagination_complete,
    }


def _to_v1_3(payload: dict[str, object], scopes: list[dict[str, object]]) -> dict[str, object]:
    """Upgrade a request built by ``request_payload`` to contract 1.3 with consent scopes."""

    payload = dict(payload)
    payload["schema_version"] = "1.3"
    payload["accounts"] = [dict(item, schema_version="1.3") for item in payload["accounts"]]
    payload["transactions"] = [
        dict(item, schema_version="1.3") for item in payload["transactions"]
    ]
    payload["consent_scopes"] = scopes
    return payload


def test_observed_income_is_not_scaled_by_declared_coverage(request_payload, transaction) -> None:
    """Coverage 1.3 forbids the field that used to scale observed income upward.

    A receiver fetched R$400,000 and knows nothing about accounts outside its consent; the
    estimate is what was observed, not a multiple of it.
    """

    payload = request_payload(transactions=[transaction("salary", amount_minor=400_000)])
    payload = _to_v1_3(
        payload,
        [
            _scope("checking", "2026-01-01", "2026-02-28"),
            _scope("savings", "2026-01-01", "2026-02-28"),
        ],
    )

    audit = RuleBasedIncomeEstimator().explain(payload)
    estimate = audit.estimate.monthly_estimates[0]
    reconstruction = audit.monthly_reconstructions[0]

    assert estimate.estimated_income_minor == 400_000
    assert reconstruction.coverage_adjustment_minor == 0
    assert "COVERAGE_SCALING_APPLIED" not in reconstruction.reason_codes


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
    payload = _to_v1_3(
        payload,
        [
            # Pagination never completed for checking, so the receiver cannot tell a due month it
            # did fetch from one it did not: every due month in the stream's active span is a gap.
            _scope("checking", "2026-01-01", "2026-06-30", pagination_complete=False),
            _scope("savings", "2026-01-01", "2026-06-30"),
        ],
    )

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


def test_recurring_estimator_does_not_impute_into_fetched_months(
    request_payload,
    transaction,
) -> None:
    """A month the receiver fetched and found nothing in is a non-payment, not a hidden one."""

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
    payload = _to_v1_3(
        payload,
        [
            _scope("checking", "2026-01-01", "2026-06-30"),
            _scope("savings", "2026-01-01", "2026-06-30"),
        ],
    )

    audit = RecurringIncomeEstimator().explain(payload)

    march = audit.monthly_reconstructions[2]
    assert march.observed_income_minor == 0
    assert march.imputed_income_minor == 0
    assert "RECURRING_STREAM_GAP_IMPUTED" not in march.reason_codes
    assert sum(item.estimated_income_minor for item in audit.estimate.monthly_estimates) == (
        5 * 500_000
    )


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
    payload = _to_v1_3(
        payload,
        [
            # Fetched from month 2 onward, so January is the one month the receiver knows it
            # never fetched for this account.
            _scope("checking", "2026-02-01", "2026-05-31"),
            _scope("savings", "2026-01-01", "2026-05-31"),
        ],
    )

    estimate = RecurringIncomeEstimator().estimate(payload)

    assert estimate.monthly_estimates[0].estimated_income_minor == 500_000
    assert estimate.monthly_estimates[0].contributing_transaction_ids


def test_supervised_candidate_loads_frozen_artifact_and_preserves_safety_rules(
    request_payload,
    transaction,
) -> None:
    payload = request_payload(
        transactions=[
            transaction("salary"),
            transaction(
                "transfer",
                amount_minor=250_000,
                description="OWN TRANSFER FROM SAVINGS",
            ),
        ]
    )

    audit = SupervisedIncomeEstimator(MODEL_PATH).explain(payload)
    decisions = {item.transaction_id: item for item in audit.transaction_decisions}

    assert audit.metadata.estimator_version == "supervised-transactions-0.3.0"
    assert audit.metadata.model_versions == ("transaction-gbdt-stumps-0.3.0",)
    assert decisions["salary"].classification == "INCOME"
    assert decisions["transfer"].classification == "EXCLUDED"
    assert decisions["transfer"].reason_codes == (
        "EXCLUDED_DESCRIPTION_TRANSFER_FROM",
    )
