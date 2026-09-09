"""Controlled reproductions of the September 2026 review findings.

Each test states the economically correct answer directly rather than recording what the pipeline
happens to produce, because the implementation under test is the thing that was wrong. Amounts are
chosen so the defective behaviour and the correct behaviour differ by a large, obvious margin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from income_estimator.assessment import assess_month, recurring_unrecognized_months
from income_estimator.contracts.output_v1_2 import MonthlyIncomeEstimateV12
from income_estimator.pipeline import RecurringIncomeEstimator
from income_estimator.production import ProductionIncomeEstimator


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
    """Upgrade a request built by ``request_payload`` to contract 1.3 with consent scopes.

    Pagination is reported incomplete on every account so the whole active span is a receiver-known
    gap; that is what makes gap imputation eligible at all.
    """

    payload = dict(payload)
    payload["schema_version"] = "1.3"
    payload["accounts"] = [dict(item, schema_version="1.3") for item in payload["accounts"]]
    payload["transactions"] = [
        dict(item, schema_version="1.3") for item in payload["transactions"]
    ]
    payload["consent_scopes"] = scopes
    return payload


def _incomplete_scopes(payload: dict[str, object]) -> list[dict[str, object]]:
    window_start = payload["window_start"]
    window_end = payload["window_end"]
    return [
        _scope(account["account_id"], window_start, window_end, pagination_complete=False)
        for account in payload["accounts"]
    ]


def _months(audit) -> dict[str, int]:
    return {
        item.month: item.estimated_income_minor for item in audit.estimate.monthly_estimates
    }


def test_quarterly_income_is_not_paid_in_the_months_between(request_payload, transaction):
    """A source that pays every three months earns nothing in the two months between.

    R$9,000 in January, April and July is R$27,000 of income. Filling February, March, May and June
    with another R$9,000 each reported R$63,000, which is not a measurement of anything.
    """

    payload = request_payload(
        transactions=[
            transaction(
                f"quarter-{month}",
                posted_at=f"2026-{month:02d}-05",
                amount_minor=900_000,
                description="PROFIT DISTRIBUTION",
            )
            for month in (1, 4, 7)
        ],
        months=7,
    )
    payload = _to_v1_3(payload, _incomplete_scopes(payload))

    audit = RecurringIncomeEstimator().explain(payload)
    monthly = _months(audit)

    assert audit.income_streams[0].frequency == "QUARTERLY"
    assert sum(monthly.values()) == 2_700_000
    assert monthly["2026-01"] == 900_000
    assert monthly["2026-04"] == 900_000
    assert monthly["2026-07"] == 900_000
    assert monthly["2026-02"] == 0
    assert monthly["2026-03"] == 0
    assert monthly["2026-05"] == 0
    assert monthly["2026-06"] == 0
    reasons = {item.month: item.reason_codes for item in audit.monthly_reconstructions}
    assert "RECURRING_STREAM_NOT_DUE_THIS_MONTH" in reasons["2026-02"]


def test_monthly_income_still_fills_a_missing_payment(request_payload, transaction):
    """The cadence rule must not stop a monthly source from filling a genuine gap."""

    payload = request_payload(
        transactions=[
            transaction(
                f"salary-{month}",
                posted_at=f"2026-{month:02d}-05",
                description="MONTHLY PAYROLL CREDIT",
            )
            for month in (1, 2, 3, 4, 6)
        ],
        months=6,
    )
    payload = _to_v1_3(payload, _incomplete_scopes(payload))

    audit = RecurringIncomeEstimator().explain(payload)
    monthly = _months(audit)

    assert audit.income_streams[0].frequency == "MONTHLY"
    assert monthly["2026-05"] == 500_000
    reasons = {item.month: item.reason_codes for item in audit.monthly_reconstructions}
    assert "RECURRING_STREAM_GAP_IMPUTED" in reasons["2026-05"]


def test_irregular_cadence_imputes_nothing(request_payload, transaction):
    """An unestablished cadence says nothing about when the next payment was due."""

    payload = request_payload(
        transactions=[
            transaction("irregular-1", posted_at="2026-01-05", description="SERVICE RECEIPT"),
            transaction("irregular-2", posted_at="2026-02-20", description="SERVICE RECEIPT"),
            transaction("irregular-3", posted_at="2026-03-03", description="SERVICE RECEIPT"),
            transaction("irregular-4", posted_at="2026-07-01", description="SERVICE RECEIPT"),
        ],
        months=7,
    )
    payload = _to_v1_3(payload, _incomplete_scopes(payload))

    audit = RecurringIncomeEstimator().explain(payload)
    monthly = _months(audit)

    assert audit.income_streams[0].frequency == "IRREGULAR"
    assert sum(monthly.values()) == 2_000_000
    assert monthly["2026-04"] == 0
    assert monthly["2026-05"] == 0
    assert monthly["2026-06"] == 0


def test_mixed_cadences_under_partial_coverage_are_filled_separately(
    request_payload, transaction
):
    """Two sources with different cadences in one request each follow their own rhythm.

    A monthly salary missing one month is a gap. A quarterly distribution absent in the same month
    is not. Reconstructing both against one assumed monthly rhythm was what inflated the quarterly
    source, so the crossed condition is measured here rather than each cadence alone.
    """

    salary = [
        transaction(
            f"salary-{month}",
            posted_at=f"2026-{month:02d}-05",
            description="MONTHLY PAYROLL CREDIT",
        )
        for month in (1, 2, 3, 4, 6, 7)
    ]
    distribution = [
        transaction(
            f"quarter-{month}",
            posted_at=f"2026-{month:02d}-12",
            amount_minor=900_000,
            description="PROFIT DISTRIBUTION",
        )
        for month in (1, 4, 7)
    ]
    payload = request_payload(transactions=salary + distribution, months=7)
    payload = _to_v1_3(payload, _incomplete_scopes(payload))

    audit = RecurringIncomeEstimator().explain(payload)
    monthly = _months(audit)

    assert {stream.frequency for stream in audit.income_streams} == {"MONTHLY", "QUARTERLY"}
    # Observed R$57,000, plus the one salary payment May was due and did not show.
    assert sum(monthly.values()) == 6_200_000
    assert monthly["2026-05"] == 500_000
    assert monthly["2026-02"] == 500_000
    assert monthly["2026-01"] == 1_400_000


def test_unrelated_equal_debit_does_not_erase_a_salary(request_payload, transaction):
    """Rent leaving one account is not evidence that a salary arriving at another was a transfer."""

    payload = request_payload(
        transactions=[
            transaction("salary", description="SALARY"),
            transaction(
                "rent",
                direction="DEBIT",
                account_id="savings",
                description="RENT",
            ),
        ],
        months=1,
    )

    audit = RecurringIncomeEstimator().explain(payload)
    salary = next(
        item for item in audit.transaction_decisions if item.transaction_id == "salary"
    )

    assert salary.classification == "INCOME"
    assert _months(audit)["2026-01"] == 500_000
    # The coincidence is not treated as proof, and it is not hidden either.
    assert "SAME_DAY_EQUAL_DEBIT_UNLINKED" in salary.reason_codes


def test_one_debit_cannot_settle_two_equal_credits(request_payload, transaction):
    """Matching is one-to-one, so a single outgoing payment cancels at most one incoming one."""

    payload = request_payload(
        transactions=[
            transaction("pix-1", description="PIX RECEIVED", account_id="checking"),
            transaction("pix-2", description="PIX RECEIVED", account_id="checking"),
            transaction(
                "outgoing",
                direction="DEBIT",
                account_id="savings",
                description="PAYMENT SENT",
            ),
        ],
        months=1,
    )

    audit = RecurringIncomeEstimator().explain(payload)
    settled = [
        item
        for item in audit.transaction_decisions
        if item.reason_codes == ("UNLINKED_EQUAL_DEBIT_NO_INCOME_EVIDENCE",)
    ]

    assert len(settled) == 1


@pytest.mark.parametrize(
    "description",
    ["SALÁRIO", "SALARIO", "FOLHA DE PAGAMENTO", "PRO LABORE", "APOSENTADORIA"],
)
def test_portuguese_payroll_descriptions_are_recognized(
    request_payload, transaction, description: str
):
    """The same payment written in Portuguese is the same payment."""

    payload = request_payload(
        transactions=[transaction("payroll", description=description)], months=1
    )

    audit = RecurringIncomeEstimator().explain(payload)

    assert audit.transaction_decisions[0].classification == "INCOME"
    assert _months(audit)["2026-01"] == 500_000


def test_portuguese_reversal_still_beats_a_portuguese_payroll_word(
    request_payload, transaction
):
    """Exclusions keep their precedence in Portuguese, as they do in English."""

    payload = request_payload(
        transactions=[transaction("estorno", description="ESTORNO SALARIO")], months=1
    )

    audit = RecurringIncomeEstimator().explain(payload)

    assert audit.transaction_decisions[0].classification == "EXCLUDED"
    assert _months(audit)["2026-01"] == 0


def test_noncanonical_date_is_refused_rather_than_silently_zeroed(
    request_payload, transaction
):
    """`20260105` parses as a date and then loses every string comparison downstream."""

    payload = request_payload(
        transactions=[transaction("salary", posted_at="20260105", observed_at="20260105")],
        months=1,
    )

    with pytest.raises(Exception, match="YYYY-MM-DD"):
        RecurringIncomeEstimator().explain(payload)


def test_months_must_match_the_window_it_describes(request_payload, transaction):
    """A window of one month with `months=2` used to publish an estimate for February."""

    payload = request_payload(transactions=[transaction("salary")], months=1)
    payload["months"] = 2

    with pytest.raises(Exception, match="spans 1 calendar month"):
        RecurringIncomeEstimator().explain(payload)


BUNDLE_ROOT = Path(__file__).parents[1] / "bundles" / "production-0.13.0"


@pytest.fixture(scope="module")
def promoted() -> ProductionIncomeEstimator:
    return ProductionIncomeEstimator.from_bundle(BUNDLE_ROOT)


def _empty_request() -> dict[str, object]:
    """A valid twelve-month request against two consented accounts holding no transactions."""

    return {
        "schema_version": "1.0",
        "source_contract_schema_version": "1.6",
        "run_id": "run-empty",
        "customer_id": "customer-empty",
        "currency": "BRL",
        "window_start": "2026-01-01",
        "window_end": "2026-12-31",
        "months": 12,
        "accounts": [
            {
                "schema_version": "1.0",
                "customer_id": "customer-empty",
                "account_id": account,
                "institution_id": f"bank-{account}",
                "currency": "BRL",
            }
            for account in ("checking", "savings")
        ],
        "transactions": [],
        "coverage": [],
    }


def test_empty_history_qualifies_no_amount(promoted: ProductionIncomeEstimator) -> None:
    """Two accounts and no transactions used to return R$4,319.65 of sustainable income.

    The number itself was never the problem; a research pipeline may produce whatever its features
    imply. The problem was that it arrived in the same field, under the same name, that a lending
    policy reads for a well-observed salaried year.
    """

    estimate = promoted.estimate_v1_2(_empty_request())

    for month in estimate.monthly_estimates:
        assert month.assessment_status == "INSUFFICIENT_EVIDENCE"
        assert month.qualified_sustainable_income_minor is None
        assert "NO_OBSERVED_TRANSACTIONS" in month.assessment_reason_codes
    # The research estimate is not deleted. It is no longer mistakable for a usable amount.
    assert estimate.monthly_estimates[-1].sustainable_income_point_minor is not None


def test_a_month_outside_calibrated_support_is_not_qualified(
    promoted: ProductionIncomeEstimator,
) -> None:
    """An amount whose interval could not be published is a question for a person."""

    from release.check_documented_cli import sample_request

    estimate = promoted.estimate_v1_2(sample_request())
    out_of_support = [
        month
        for month in estimate.monthly_estimates
        if month.quantile_unavailable_reason == "OUT_OF_CALIBRATED_SUPPORT"
    ]

    assert out_of_support
    for month in out_of_support:
        assert month.assessment_status == "REVIEW_REQUIRED"
        assert month.qualified_sustainable_income_minor is None
        assert "OUT_OF_CALIBRATED_SUPPORT" in month.assessment_reason_codes


def test_repeating_unrecognized_credits_are_not_counted_as_income(
    request_payload, transaction
) -> None:
    """A payer nothing recognizes cannot become a stream, so it becomes an ordinary zero.

    Counting it would replace a silent zero with a silent amount, which is worse. It stays
    uncounted, and the month it lands in is raised for a person instead.
    """

    payload = request_payload(
        transactions=[
            transaction(f"salary-{month}", posted_at=f"2026-{month:02d}-05", description="SALARY")
            for month in (1, 2, 3)
        ]
        + [
            transaction(
                f"unknown-{month}",
                posted_at=f"2026-{month:02d}-18",
                amount_minor=300_000,
                description="PIX RECEBIDO ACME",
            )
            for month in (1, 2, 3)
        ],
        months=3,
    )

    audit = RecurringIncomeEstimator().explain(payload)
    unknown = [
        item for item in audit.transaction_decisions if item.transaction_id.startswith("unknown-")
    ]

    assert len(unknown) == 3
    assert all(item.classification == "AMBIGUOUS" for item in unknown)
    assert _months(audit)["2026-01"] == 500_000
    assert recurring_unrecognized_months(audit.transaction_decisions) == frozenset(
        {"2026-01", "2026-02", "2026-03"}
    )


def test_material_unclassified_credit_needs_review() -> None:
    """A fifth or more of arriving money being unexplained is a question, not a rounding error."""

    status, reasons = assess_month(
        sustainable_income_minor=500_000,
        quantile_unavailable_reason=None,
        counted_income_minor=500_000,
        unclassified_credit_minor=200_000,
        window_has_observed_transactions=True,
        window_has_established_income=True,
        has_recurring_unrecognized_source=False,
    )

    assert status == "REVIEW_REQUIRED"
    assert "MATERIAL_UNCLASSIFIED_CREDITS" in reasons


def test_a_well_evidenced_month_still_qualifies() -> None:
    """The rule must withhold amounts without withholding every amount."""

    status, reasons = assess_month(
        sustainable_income_minor=500_000,
        quantile_unavailable_reason=None,
        counted_income_minor=500_000,
        unclassified_credit_minor=0,
        window_has_observed_transactions=True,
        window_has_established_income=True,
        has_recurring_unrecognized_source=False,
    )

    assert status == "SUPPORTED"
    assert reasons == ("EVIDENCE_WITHIN_CALIBRATED_SUPPORT",)


def test_a_qualified_amount_cannot_accompany_an_unqualified_status() -> None:
    """The contract itself refuses the shape the old one could not express."""

    with pytest.raises(Exception, match="published only for SUPPORTED"):
        MonthlyIncomeEstimateV12(
            month="2026-01",
            estimated_income_minor=500_000,
            confidence_lower_minor=500_000,
            confidence_upper_minor=500_000,
            realized_income_estimate_minor=500_000,
            sustainable_income_p50_minor=500_000,
            sustainable_income_point_minor=500_000,
            quantile_unavailable_reason="OUT_OF_CALIBRATED_SUPPORT",
            assessment_status="REVIEW_REQUIRED",
            assessment_reason_codes=("OUT_OF_CALIBRATED_SUPPORT",),
            qualified_sustainable_income_minor=500_000,
        )


def test_the_downgrade_to_1_1_keeps_every_number(promoted: ProductionIncomeEstimator) -> None:
    """Old consumers get the money they got before, and none of the new standing."""

    from release.check_documented_cli import sample_request

    request = sample_request()
    assessed = promoted.estimate_v1_2(request)
    legacy = promoted.estimate_v1_1(request)

    assert legacy.schema_version == "1.1"
    assert not hasattr(legacy.monthly_estimates[0], "assessment_status")
    for new, old in zip(assessed.monthly_estimates, legacy.monthly_estimates, strict=True):
        assert old.month == new.month
        assert old.estimated_income_minor == new.estimated_income_minor
        assert old.sustainable_income_p50_minor == new.sustainable_income_p50_minor
        assert old.sustainable_income_p10_minor == new.sustainable_income_p10_minor
        assert old.sustainable_income_p90_minor == new.sustainable_income_p90_minor
