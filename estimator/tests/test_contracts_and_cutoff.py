from __future__ import annotations

import pytest
from pydantic import ValidationError

from income_estimator.contracts import EstimatorInputV1, EstimatorInputV11
from income_estimator.pipeline import RecurringIncomeEstimator, RuleBasedIncomeEstimator
from income_estimator.transaction_intelligence import extract_transaction_features


def test_input_is_strict(request_payload, transaction) -> None:
    payload = request_payload(transactions=[transaction("salary")])
    payload["true_income_minor"] = 500_000

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EstimatorInputV1.model_validate(payload)


def test_observation_after_cutoff_cannot_contribute(request_payload, transaction) -> None:
    payload = request_payload(
        transactions=[
            transaction(
                "late-salary",
                posted_at="2026-02-05",
                observed_at="2026-03-01",
            )
        ]
    )

    audit = RuleBasedIncomeEstimator().explain(payload)

    assert [item.estimated_income_minor for item in audit.estimate.monthly_estimates] == [0, 0]
    assert audit.transaction_decisions[0].reason_codes == ("OBSERVED_AFTER_CUTOFF",)
    assert audit.transaction_decisions[0].normalized_description == ""


def test_contract_rejects_cross_customer_records(request_payload, transaction) -> None:
    payload = request_payload(transactions=[transaction("salary")])
    payload["transactions"][0]["customer_id"] = "another-customer"

    with pytest.raises(ValidationError, match="customer_id"):
        EstimatorInputV1.model_validate(payload)


def test_input_1_1_accepts_optional_provider_context(request_payload, transaction) -> None:
    payload = request_payload(transactions=[transaction("salary")])
    payload["schema_version"] = "1.1"
    for account in payload["accounts"]:
        account["schema_version"] = "1.1"
    payload["transactions"][0].update(
        {
            "schema_version": "1.1",
            "provider_transaction_type": "PIX_CREDIT",
            "counterparty_name": "Example Employer Ltd.",
            "counterparty_document_hash": "a" * 64,
            "balance_after_minor": 800_000,
        }
    )
    payload["balances"] = [
        {
            "schema_version": "1.1",
            "balance_id": "balance-1",
            "customer_id": "customer-test",
            "account_id": "checking",
            "reference_date": "2026-01-31",
            "balance_minor": 800_000,
            "currency": "BRL",
        }
    ]

    request = EstimatorInputV11.model_validate(payload)
    audit = RecurringIncomeEstimator().explain(request)

    assert request.transactions[0].counterparty_name == "Example Employer Ltd."
    assert request.balances[0].balance_minor == 800_000
    assert audit.metadata.input_contract_version == "1.1"


def test_recurrence_features_use_prior_observations_only(request_payload, transaction) -> None:
    transactions = [
        transaction("third", posted_at="2026-03-05", observed_at="2026-03-05"),
        transaction("first", posted_at="2026-01-05", observed_at="2026-01-05"),
        transaction("second", posted_at="2026-02-05", observed_at="2026-02-05"),
    ]
    payload = request_payload(transactions=transactions, months=3)
    payload["schema_version"] = "1.1"
    for account in payload["accounts"]:
        account["schema_version"] = "1.1"
    for item in payload["transactions"]:
        item.update(
            {
                "schema_version": "1.1",
                "counterparty_document_hash": "b" * 64,
            }
        )
    request = EstimatorInputV11.model_validate(payload)

    features = {
        item.transaction.source.transaction_id: item
        for item in extract_transaction_features(request)
    }

    assert features["first"].prior_same_counterparty_count == 0
    assert features["second"].prior_same_counterparty_count == 1
    assert features["third"].prior_same_counterparty_count == 2
    assert features["second"].prior_same_counterparty_count_90d == 1
