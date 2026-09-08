"""Controlled reproductions of the September 2026 review findings.

Each test states the economically correct answer directly rather than recording what the pipeline
happens to produce, because the implementation under test is the thing that was wrong. Amounts are
chosen so the defective behaviour and the correct behaviour differ by a large, obvious margin.
"""

from __future__ import annotations

import pytest

from income_estimator.pipeline import RecurringIncomeEstimator


def _coverage(eligible: int, observed: int) -> list[dict[str, object]]:
    """Declared partial coverage, which is what makes gap imputation eligible at all."""

    return [
        {
            "schema_version": "1.0",
            "customer_id": "customer-test",
            "account_id": "checking",
            "configured_coverage_percent": 90,
            "eligible_record_count": eligible,
            "observed_original_record_count": observed,
            "effective_coverage_basis_points": round(observed * 10_000 / eligible),
        }
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
    payload["coverage"] = _coverage(100, 90)

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
    payload["coverage"] = _coverage(100, 90)

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
    payload["coverage"] = _coverage(100, 90)

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
    payload["coverage"] = _coverage(100, 90)

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
