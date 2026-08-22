from __future__ import annotations

import pytest
from pydantic import ValidationError

from income_estimator.contracts import EstimatorInputV1
from income_estimator.pipeline import RuleBasedIncomeEstimator


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
