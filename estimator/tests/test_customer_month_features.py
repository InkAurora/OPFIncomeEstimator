from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from income_estimator.contracts import EstimatorInputV11, validate_estimator_input
from income_estimator.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA,
    FEATURE_SCHEMA_FINGERPRINT,
    FEATURE_SET_VERSION,
    build_customer_month_features,
    feature_schema_fingerprint,
    slice_request,
)
from income_estimator.pipeline import RecurringIncomeEstimator, SupervisedIncomeEstimator

MODEL_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "transaction-classifier-0.3.0.json"
)


def _values(table, reference_month: str) -> dict[str, float | int | None]:
    return table.row(reference_month).to_mapping()


def _salary_months(transaction, count: int, *, amount_minor: int = 500_000):
    return [
        transaction(
            f"salary-{index:02d}",
            posted_at=f"2026-{index:02d}-05",
            amount_minor=amount_minor,
        )
        for index in range(1, count + 1)
    ]


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


def test_feature_set_version_and_schema_fingerprint_are_frozen() -> None:
    assert FEATURE_SET_VERSION == "customer-month-features-1.2.0"
    assert FEATURE_SCHEMA_FINGERPRINT == "e54e70affc30a6fad10282e62463a936"
    assert feature_schema_fingerprint(FEATURE_SCHEMA) == FEATURE_SCHEMA_FINGERPRINT
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES)) == 104


def test_every_row_carries_the_full_versioned_schema(request_payload, transaction) -> None:
    payload = request_payload(transactions=_salary_months(transaction, 3), months=3)

    table = build_customer_month_features(payload)

    assert table.feature_set_version == FEATURE_SET_VERSION
    assert table.feature_schema_fingerprint == FEATURE_SCHEMA_FINGERPRINT
    assert table.estimator_version == "recurring-streams-0.2.0"
    assert table.input_contract_version == "1.0"
    assert tuple(row.reference_month for row in table.rows) == ("2026-01", "2026-02", "2026-03")
    for row in table.rows:
        assert tuple(item.name for item in row.values) == FEATURE_NAMES
        assert row.as_of_date[:7] == row.reference_month
        assert len(row.to_vector(FEATURE_NAMES)) == len(FEATURE_NAMES)


def test_feature_table_is_deterministic(request_payload, transaction) -> None:
    payload = request_payload(transactions=_salary_months(transaction, 6), months=6)

    first = build_customer_month_features(payload)
    second = build_customer_month_features(payload)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_rolling_cash_flow_uses_only_trailing_months(request_payload, transaction) -> None:
    transactions = [
        *_salary_months(transaction, 12),
        transaction(
            "rent",
            posted_at="2026-12-10",
            direction="DEBIT",
            amount_minor=120_000,
            description="RENT PAYMENT",
        ),
    ]
    payload = request_payload(transactions=transactions, months=12)

    table = build_customer_month_features(payload)

    june = _values(table, "2026-06")
    assert june["credits_1m_minor"] == 500_000
    assert june["credits_3m_minor"] == 1_500_000
    assert june["credits_6m_minor"] == 3_000_000
    assert june["credits_12m_minor"] == 3_000_000
    assert june["income_12m_minor"] == 3_000_000
    assert june["debits_12m_minor"] == 0

    december = _values(table, "2026-12")
    assert december["credits_12m_minor"] == 6_000_000
    assert december["debits_1m_minor"] == 120_000
    assert december["transaction_count_1m"] == 2
    assert december["credit_count_1m"] == 1


def test_probable_income_weights_credits_by_income_probability(
    request_payload,
    transaction,
) -> None:
    payload = request_payload(
        transactions=[
            transaction("salary", posted_at="2026-01-05", amount_minor=400_000),
            transaction(
                "unknown",
                posted_at="2026-01-20",
                amount_minor=100_000,
                description="PIX RECEIVED",
            ),
        ],
        months=1,
    )

    values = _values(build_customer_month_features(payload), "2026-01")

    assert values["credits_1m_minor"] == 500_000
    assert values["income_1m_minor"] == 400_000
    assert values["probable_income_1m_minor"] == 380_000 + 25_000
    assert values["probable_income_mean_3m_minor"] == 405_000


def test_stability_features_expose_dispersion_and_zero_income_months(
    request_payload,
    transaction,
) -> None:
    transactions = [
        transaction("salary-01", posted_at="2026-01-05", amount_minor=600_000),
        transaction("salary-02", posted_at="2026-02-05", amount_minor=400_000),
        transaction("salary-04", posted_at="2026-04-05", amount_minor=600_000),
        transaction("salary-05", posted_at="2026-05-05", amount_minor=400_000),
        transaction("salary-06", posted_at="2026-06-05", amount_minor=500_000),
    ]
    payload = request_payload(transactions=transactions, months=6)

    values = _values(build_customer_month_features(payload), "2026-06")

    assert values["income_mean_6m_minor"] == 416_667
    assert values["income_median_6m_minor"] == 450_000
    assert values["income_std_6m_minor"] == 203_443
    assert values["income_variance_6m"] == pytest.approx(41_388_888_888.889, rel=1e-9)
    assert values["income_cv_6m"] == pytest.approx(0.48826222)
    assert values["zero_income_months_6m"] == 1
    assert values["income_min_12m_minor"] == 0
    assert values["income_max_12m_minor"] == 600_000
    assert values["income_p25_12m_minor"] == 400_000
    assert values["income_p50_12m_minor"] == 450_000
    assert values["income_p75_12m_minor"] == 575_000


def test_short_history_reports_insufficient_history_instead_of_zero(
    request_payload,
    transaction,
) -> None:
    payload = request_payload(transactions=_salary_months(transaction, 3), months=3)

    row = build_customer_month_features(payload).row("2026-01")
    reasons = {item.name: item.missing_reason for item in row.values}

    assert reasons["income_std_3m_minor"] == "INSUFFICIENT_HISTORY"
    assert reasons["income_variance_3m"] == "INSUFFICIENT_HISTORY"
    assert reasons["income_cv_6m"] == "INSUFFICIENT_HISTORY"
    assert row.to_mapping()["income_mean_3m_minor"] == 500_000
    assert row.to_mapping()["window_months"] == 1


def test_zero_income_history_reports_undefined_denominator(request_payload, transaction) -> None:
    payload = request_payload(
        transactions=[
            transaction(
                "refund",
                posted_at="2026-01-05",
                amount_minor=10_000,
                description="PURCHASE REFUND",
            )
        ],
        months=6,
    )

    row = build_customer_month_features(payload).row("2026-06")
    reasons = {item.name: item.missing_reason for item in row.values}
    values = row.to_mapping()

    assert reasons["income_cv_6m"] == "UNDEFINED_ZERO_DENOMINATOR"
    assert reasons["largest_source_share_12m"] == "NO_OBSERVED_RECORDS"
    assert reasons["months_since_last_source_activity"] == "NO_OBSERVED_RECORDS"
    assert values["income_12m_minor"] == 0
    assert values["zero_income_months_6m"] == 6
    assert values["excluded_refund_12m_minor"] == 10_000
    assert values["source_count_12m"] == 0


def test_source_structure_reports_concentration_and_largest_share(
    request_payload,
    transaction,
) -> None:
    transactions = [
        *_salary_months(transaction, 6),
        *[
            transaction(
                f"consulting-{index:02d}",
                posted_at=f"2026-{index:02d}-18",
                amount_minor=100_000,
                description="CONSULTING SERVICE RECEIPT",
            )
            for index in range(1, 7)
        ],
    ]
    payload = request_payload(transactions=transactions, months=6)

    values = _values(build_customer_month_features(payload), "2026-06")

    assert values["source_count_12m"] == 2
    assert values["recurring_source_count_12m"] == 2
    assert values["active_source_count_3m"] == 2
    assert values["source_income_12m_minor"] == 3_600_000
    assert values["largest_source_share_12m"] == pytest.approx(0.83333333)
    assert values["source_concentration_hhi_12m"] == pytest.approx(0.72222222)
    assert values["recurrence_score_mean_12m_basis_points"] == 9_884
    assert values["recurrence_score_max_12m_basis_points"] == 9_884
    assert values["source_amount_cv_mean_12m"] == 0.0
    assert values["months_since_last_source_activity"] == 0
    assert values["distinct_credit_counterparties_12m"] == 2


def test_coverage_and_activity_describe_observable_scope(request_payload, transaction) -> None:
    payload = request_payload(transactions=_salary_months(transaction, 12), months=12)
    payload["coverage"] = _incomplete_coverage()

    values = _values(build_customer_month_features(payload), "2026-12")

    assert values["window_months"] == 12
    assert values["months_observed"] == 12
    assert values["months_since_first_observation"] == 11
    assert values["accounts_declared"] == 2
    assert values["accounts_observed"] == 1
    assert values["institutions_observed"] == 1
    assert values["active_accounts_3m"] == 1
    assert values["effective_consent_coverage_basis_points"] == 9_000
    assert values["minimum_account_coverage_basis_points"] == 9_000
    assert values["observed_domain_count"] == 1
    assert values["data_completeness_score_basis_points"] == 6_500
    assert values["days_since_last_credit"] == 26


def _input_1_1_payload(request_payload, transaction) -> dict[str, object]:
    transactions = [
        *_salary_months(transaction, 6),
        transaction(
            "loan-credit",
            posted_at="2026-02-20",
            amount_minor=900_000,
            description="PERSONAL CREDIT DEPOSIT",
        ),
        transaction(
            "investment-in",
            posted_at="2026-03-12",
            direction="DEBIT",
            amount_minor=200_000,
            description="FUND CONTRIBUTION",
        ),
        transaction(
            "investment-out",
            posted_at="2026-04-12",
            amount_minor=150_000,
            description="FUND WITHDRAWAL",
        ),
    ]
    payload = request_payload(transactions=transactions, months=6)
    payload["schema_version"] = "1.1"
    for account in payload["accounts"]:
        account["schema_version"] = "1.1"
    for item in payload["transactions"]:
        item["schema_version"] = "1.1"
    payload["loans"] = [
        {
            "schema_version": "1.1",
            "customer_id": "customer-test",
            "loan_id": "loan-1",
            "disbursement_transaction_id": "loan-credit",
        }
    ]
    payload["investment_transactions"] = [
        {
            "schema_version": "1.1",
            "customer_id": "customer-test",
            "investment_transaction_id": "inv-1",
            "transaction_type": "CONTRIBUTION",
            "related_account_transaction_id": "investment-in",
        },
        {
            "schema_version": "1.1",
            "customer_id": "customer-test",
            "investment_transaction_id": "inv-2",
            "transaction_type": "REDEMPTION",
            "related_account_transaction_id": "investment-out",
        },
    ]
    payload["balances"] = [
        {
            "schema_version": "1.1",
            "balance_id": "balance-1",
            "customer_id": "customer-test",
            "account_id": "checking",
            "reference_date": "2026-06-25",
            "balance_minor": 1_250_000,
            "currency": "BRL",
        }
    ]
    return payload


def test_balance_loan_and_investment_context_from_contract_1_1(
    request_payload,
    transaction,
) -> None:
    payload = _input_1_1_payload(request_payload, transaction)

    table = build_customer_month_features(payload)
    values = _values(table, "2026-06")

    assert table.input_contract_version == "1.1"
    assert values["available_balance_minor"] == 1_250_000
    assert values["balance_accounts_observed"] == 1
    assert values["balance_staleness_days"] == 5
    assert values["observed_loan_count"] == 1
    assert values["loan_disbursement_12m_minor"] == 900_000
    assert values["months_since_last_loan_disbursement"] == 4
    assert values["excluded_loan_disbursement_12m_minor"] == 900_000
    assert values["investment_contribution_12m_minor"] == 200_000
    assert values["investment_redemption_12m_minor"] == 150_000
    assert values["net_investment_contributions_12m_minor"] == 50_000
    assert values["investment_transaction_count_12m"] == 2
    assert values["excluded_investment_redemption_12m_minor"] == 150_000
    assert values["observed_domain_count"] == 4


def test_product_context_is_missing_before_it_is_observed(request_payload, transaction) -> None:
    payload = _input_1_1_payload(request_payload, transaction)

    table = build_customer_month_features(payload)
    january = table.row("2026-01")
    reasons = {item.name: item.missing_reason for item in january.values}

    assert reasons["observed_loan_count"] == "NO_OBSERVED_RECORDS"
    assert reasons["investment_contribution_12m_minor"] == "NO_OBSERVED_RECORDS"
    assert reasons["available_balance_minor"] == "NO_OBSERVED_RECORDS"
    assert january.to_mapping()["observed_domain_count"] == 1
    assert table.row("2026-02").to_mapping()["observed_loan_count"] == 1


def test_missing_product_domains_are_declared_not_zeroed(request_payload, transaction) -> None:
    payload = request_payload(transactions=_salary_months(transaction, 3), months=3)

    row = build_customer_month_features(payload).row("2026-03")
    reasons = {item.name: item.missing_reason for item in row.values}
    values = row.to_mapping()

    for name in (
        "card_spend_3m_minor",
        "credit_utilization_ratio",
        "installment_commitment_minor",
        "monthly_debt_payment_minor",
        "outstanding_debt_minor",
        "investment_balance_minor",
    ):
        assert reasons[name] == "CONTRACT_DOMAIN_UNAVAILABLE"
        assert values[name] is None


def test_future_observations_cannot_change_earlier_rows(request_payload, transaction) -> None:
    known = _salary_months(transaction, 6)
    payload = request_payload(transactions=known, months=12)
    later = request_payload(
        transactions=[
            *known,
            *[
                transaction(f"salary-{index:02d}", posted_at=f"2026-{index:02d}-05")
                for index in range(7, 13)
            ],
            transaction(
                "backdated",
                posted_at="2026-03-20",
                observed_at="2026-11-02",
                amount_minor=750_000,
                description="DELAYED SERVICE PAYMENT",
            ),
        ],
        months=12,
    )

    original = build_customer_month_features(payload)
    extended = build_customer_month_features(later)

    for month in ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"):
        assert original.row(month) == extended.row(month)
    assert _values(extended, "2026-03")["income_1m_minor"] == 500_000
    assert _values(extended, "2026-10")["income_12m_minor"] == 5_000_000
    assert _values(extended, "2026-11")["income_12m_minor"] == 6_250_000


def test_late_arrival_enters_history_only_after_it_is_observed(
    request_payload,
    transaction,
) -> None:
    payload = request_payload(
        transactions=[
            transaction(
                "late-salary",
                posted_at="2026-01-05",
                observed_at="2026-03-02",
                amount_minor=500_000,
            )
        ],
        months=3,
    )

    table = build_customer_month_features(payload)

    assert _values(table, "2026-01")["income_1m_minor"] == 0
    assert _values(table, "2026-02")["income_3m_minor"] == 0
    assert _values(table, "2026-03")["income_3m_minor"] == 500_000
    assert _values(table, "2026-03")["months_observed"] == 1


def test_row_matches_a_request_truncated_at_that_reference_month(
    request_payload,
    transaction,
) -> None:
    transactions = _salary_months(transaction, 12)
    full = build_customer_month_features(request_payload(transactions=transactions, months=12))

    for months in (1, 4, 9):
        reference_month = f"2026-{months:02d}"
        truncated_payload = request_payload(
            transactions=[
                item for item in transactions if item["observed_at"][:7] <= reference_month
            ],
            months=months,
        )
        truncated = build_customer_month_features(truncated_payload)

        assert truncated.rows[-1] == full.row(reference_month)


def test_point_in_time_slice_remains_contract_valid(request_payload, transaction) -> None:
    payload = _input_1_1_payload(request_payload, transaction)
    request = validate_estimator_input(payload)

    sliced = slice_request(request, date(2026, 3, 31))

    assert isinstance(sliced, EstimatorInputV11)
    assert sliced.window_end == "2026-03-31"
    assert sliced.months == 3
    assert {item.transaction_id for item in sliced.transactions} < {
        item.transaction_id for item in request.transactions
    }
    assert sliced.balances == ()
    assert EstimatorInputV11.model_validate(sliced.model_dump(mode="python")) == sliced


def test_optional_supervised_candidate_is_recorded_but_not_default(
    request_payload,
    transaction,
) -> None:
    payload = request_payload(transactions=_salary_months(transaction, 6), months=6)

    default_table = build_customer_month_features(payload)
    candidate_table = build_customer_month_features(
        payload,
        SupervisedIncomeEstimator(MODEL_PATH),
    )

    assert default_table.model_versions == ()
    assert default_table.estimator_version == RecurringIncomeEstimator.estimator_version
    assert candidate_table.model_versions == ("transaction-gbdt-stumps-0.3.0",)
    assert candidate_table.estimator_version == "supervised-transactions-0.3.0"
    assert candidate_table.transaction_feature_version == "transaction-model-features-1.0.0"
    assert tuple(item.name for item in candidate_table.rows[-1].values) == FEATURE_NAMES
