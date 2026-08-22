from __future__ import annotations

from income_estimator.pipeline import RuleBasedIncomeEstimator


def _decision_map(audit):
    return {item.transaction_id: item for item in audit.transaction_decisions}


def test_rules_are_precedence_ordered_and_auditable(request_payload, transaction) -> None:
    transactions = [
        transaction("salary", description="FCB | MONTHLY PAYROLL CREDIT"),
        transaction("refund", description="PURCHASE REFUND", amount_minor=10_000),
        transaction("mystery", description="PIX RECEIVED", amount_minor=20_000),
        transaction(
            "duplicate",
            description="MONTHLY PAYROLL CREDIT",
            duplicate_of_transaction_id="salary",
        ),
        transaction(
            "transfer-debit",
            direction="DEBIT",
            amount_minor=30_000,
            account_id="checking",
            description="TRANSFER TO OWN RESERVE",
        ),
        transaction(
            "transfer-credit",
            amount_minor=30_000,
            account_id="savings",
            description="PIX RECEIVED",
        ),
        transaction("loan", description="CONSULTING SERVICE RECEIPT", amount_minor=800_000),
        transaction("redemption", description="FUND CASH DISTRIBUTION", amount_minor=400_000),
    ]
    payload = request_payload(transactions=transactions)
    payload["loans"] = [
        {
            "schema_version": "1.0",
            "customer_id": "customer-test",
            "loan_id": "loan-1",
            "disbursement_transaction_id": "loan",
        }
    ]
    payload["investment_transactions"] = [
        {
            "schema_version": "1.0",
            "customer_id": "customer-test",
            "investment_transaction_id": "investment-tx-1",
            "transaction_type": "REDEMPTION",
            "related_account_transaction_id": "redemption",
        }
    ]

    audit = RuleBasedIncomeEstimator().explain(payload)
    decisions = _decision_map(audit)

    assert decisions["salary"].classification == "INCOME"
    assert decisions["refund"].classification == "EXCLUDED"
    assert decisions["mystery"].classification == "AMBIGUOUS"
    assert decisions["duplicate"].reason_codes == ("DUPLICATE_OBSERVATION",)
    assert decisions["transfer-credit"].reason_codes == ("VISIBLE_OWN_TRANSFER_PAIR",)
    assert decisions["loan"].reason_codes == ("LOAN_DISBURSEMENT_LINK",)
    assert decisions["redemption"].reason_codes == ("INVESTMENT_REDEMPTION_LINK",)
    assert audit.estimate.monthly_estimates[0].contributing_transaction_ids == ("salary",)


def test_reversal_excludes_both_records(request_payload, transaction) -> None:
    payload = request_payload(
        transactions=[
            transaction("original", amount_minor=10_000, description="ANNUAL BONUS"),
            transaction(
                "reversal",
                amount_minor=10_000,
                description="REVERSAL ANNUAL BONUS",
                reversal_of_transaction_id="original",
            ),
        ]
    )

    decisions = _decision_map(RuleBasedIncomeEstimator().explain(payload))

    assert decisions["original"].reason_codes == ("REVERSED_ORIGINAL",)
    assert decisions["reversal"].reason_codes == ("REVERSAL_OBSERVATION",)


def test_corrected_repost_is_counted_and_marked(request_payload, transaction) -> None:
    """ADR 0004: the correction carries the income the reversed original no longer can."""

    payload = request_payload(
        transactions=[
            transaction("original", amount_minor=10_000, description="SALARY PAYROLL"),
            transaction(
                "reversal",
                amount_minor=10_000,
                description="REVERSAL SALARY PAYROLL",
                reversal_of_transaction_id="original",
            ),
            transaction(
                "repost",
                amount_minor=10_000,
                description="SALARY PAYROLL",
                repost_of_transaction_id="original",
            ),
        ]
    )

    audit = RuleBasedIncomeEstimator().explain(payload)
    decisions = _decision_map(audit)

    assert decisions["original"].reason_codes == ("REVERSED_ORIGINAL",)
    assert decisions["reversal"].reason_codes == ("REVERSAL_OBSERVATION",)
    assert decisions["repost"].classification == "INCOME"
    assert decisions["repost"].reason_codes[-1] == "CORRECTED_REPOST"
    assert audit.estimate.monthly_estimates[0].contributing_transaction_ids == ("repost",)
