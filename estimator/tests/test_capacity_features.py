from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from income_estimator.contracts import EstimatorInputV12, validate_estimator_input
from income_estimator.features import build_customer_month_features, slice_request


def _card(card_id: str = "card-1") -> dict[str, object]:
    return {
        "schema_version": "1.2",
        "customer_id": "customer-test",
        "currency": "BRL",
        "card_id": card_id,
        "institution_id": "bank-a",
        "opened_on": "2025-11-01",
        "status": "ACTIVE",
    }


def _limit(month: int, used: int, *, total: int = 1_000_000) -> dict[str, object]:
    return {
        "schema_version": "1.2",
        "customer_id": "customer-test",
        "currency": "BRL",
        "credit_limit_id": f"limit-{month:02d}",
        "card_id": "card-1",
        "reference_date": f"2026-{month:02d}-28",
        "total_limit_minor": total,
        "used_limit_minor": used,
        "available_limit_minor": total - used,
    }


def _card_transaction(
    identifier: str,
    *,
    occurred_at: str,
    amount_minor: int,
    installment_count: int = 1,
) -> dict[str, object]:
    return {
        "schema_version": "1.2",
        "customer_id": "customer-test",
        "currency": "BRL",
        "card_transaction_id": identifier,
        "card_id": "card-1",
        "occurred_at": occurred_at,
        "amount_minor": amount_minor,
        "description": "CARD PURCHASE",
        "installment_count": installment_count,
    }


def _loan_payment(month: int, *, total: int = 90_000) -> dict[str, object]:
    return {
        "schema_version": "1.2",
        "customer_id": "customer-test",
        "currency": "BRL",
        "loan_payment_id": f"payment-{month:02d}",
        "loan_id": "loan-1",
        "installment_number": month,
        "installment_count": 12,
        "due_date": f"2026-{month:02d}-15",
        "principal_amount_minor": 80_000,
        "interest_amount_minor": total - 80_000,
        "total_amount_minor": total,
        "remaining_principal_after_minor": 960_000 - month * 80_000,
        "paid_at": f"2026-{month:02d}-15",
        "payment_transaction_id": None,
    }


def _loan_balance(month: int) -> dict[str, object]:
    return {
        "schema_version": "1.2",
        "customer_id": "customer-test",
        "currency": "BRL",
        "loan_balance_id": f"loan-balance-{month:02d}",
        "loan_id": "loan-1",
        "reference_date": f"2026-{month:02d}-28",
        "remaining_principal_minor": 960_000 - month * 80_000,
    }


def _investment_balance(month: int, balance: int) -> dict[str, object]:
    return {
        "schema_version": "1.2",
        "customer_id": "customer-test",
        "currency": "BRL",
        "investment_balance_id": f"investment-balance-{month:02d}",
        "investment_id": "investment-1",
        "reference_date": f"2026-{month:02d}-28",
        "balance_minor": balance,
    }


def _product_payload(request_payload, transaction, *, months: int = 6) -> dict[str, object]:
    payload = request_payload(
        transactions=[
            transaction(f"salary-{index:02d}", posted_at=f"2026-{index:02d}-05")
            for index in range(1, months + 1)
        ],
        months=months,
    )
    payload["schema_version"] = "1.2"
    for account in payload["accounts"]:
        account["schema_version"] = "1.2"
    for item in payload["transactions"]:
        item["schema_version"] = "1.2"
    payload["loans"] = [
        {
            "schema_version": "1.2",
            "customer_id": "customer-test",
            "loan_id": "loan-1",
            "disbursement_transaction_id": "salary-01",
        }
    ]
    payload["credit_cards"] = [_card()]
    payload["credit_limits"] = [
        _limit(1, 460_000),
        _limit(2, 300_000),
        _limit(3, 100_000),
    ]
    payload["card_transactions"] = [
        _card_transaction("purchase-1", occurred_at="2026-01-10", amount_minor=240_000,
                          installment_count=4),
        _card_transaction("purchase-2", occurred_at="2026-02-14", amount_minor=60_000),
        _card_transaction("purchase-3", occurred_at="2026-05-20", amount_minor=90_000),
    ]
    payload["card_invoices"] = [
        {
            "schema_version": "1.2",
            "customer_id": "customer-test",
            "currency": "BRL",
            "invoice_id": "invoice-1",
            "card_id": "card-1",
            "statement_close_date": "2026-01-28",
            "due_date": "2026-02-10",
            "amount_due_minor": 60_000,
            "paid_amount_minor": 60_000,
            "status": "PAID",
            "paid_at": "2026-02-10",
        }
    ]
    payload["loan_payments"] = [_loan_payment(month) for month in (2, 3)]
    payload["loan_balances"] = [_loan_balance(month) for month in (2, 3)]
    payload["investments"] = [
        {
            "schema_version": "1.2",
            "customer_id": "customer-test",
            "currency": "BRL",
            "investment_id": "investment-1",
            "institution_id": "bank-b",
            "opened_on": "2026-01-15",
            "status": "ACTIVE",
        }
    ]
    payload["investment_balances"] = [
        _investment_balance(2, 150_000),
        _investment_balance(3, 260_000),
    ]
    payload["balances"] = [
        {
            "schema_version": "1.2",
            "balance_id": f"balance-{month:02d}",
            "customer_id": "customer-test",
            "account_id": "checking",
            "reference_date": f"2026-{month:02d}-28",
            "balance_minor": 400_000 + month * 10_000,
            "currency": "BRL",
        }
        for month in (2, 3)
    ]
    return payload


def test_contract_1_2_accepts_optional_product_collections(
    request_payload,
    transaction,
) -> None:
    request = validate_estimator_input(_product_payload(request_payload, transaction))

    assert isinstance(request, EstimatorInputV12)
    assert request.schema_version == "1.2"
    assert len(request.credit_limits) == 3
    assert len(request.card_transactions) == 3
    assert len(request.loan_payments) == 2
    assert len(request.investment_balances) == 2


def test_contract_1_2_rejects_orphan_product_records(request_payload, transaction) -> None:
    payload = _product_payload(request_payload, transaction)
    payload["credit_limits"][0]["card_id"] = "card-unknown"

    with pytest.raises(ValidationError, match="observed credit card"):
        EstimatorInputV12.model_validate(payload)


def test_contract_1_2_rejects_inconsistent_limit_snapshot(
    request_payload,
    transaction,
) -> None:
    payload = _product_payload(request_payload, transaction)
    payload["credit_limits"][0]["available_limit_minor"] = 1

    with pytest.raises(ValidationError, match="used plus available"):
        EstimatorInputV12.model_validate(payload)


def test_capacity_features_are_computed_from_product_records(
    request_payload,
    transaction,
) -> None:
    payload = _product_payload(request_payload, transaction)

    table = build_customer_month_features(payload)
    march = table.row("2026-03").to_mapping()

    assert table.input_contract_version == "1.2"
    assert march["card_spend_3m_minor"] == 300_000
    assert march["credit_utilization_ratio"] == 0.1
    assert march["monthly_debt_payment_minor"] == 90_000
    assert march["outstanding_debt_minor"] == 720_000 + 100_000
    assert march["investment_balance_minor"] == 260_000
    assert march["observed_domain_count"] == 5
    assert march["available_balance_minor"] == 430_000


def test_installment_commitment_falls_as_installments_are_billed(
    request_payload,
    transaction,
) -> None:
    """A four-installment purchase in January is fully billed by April."""

    table = build_customer_month_features(_product_payload(request_payload, transaction))
    commitment = {
        row.reference_month: row.to_mapping()["installment_commitment_minor"]
        for row in table.rows
    }

    assert commitment["2026-01"] == 180_000
    assert commitment["2026-02"] == 120_000
    assert commitment["2026-03"] == 60_000
    assert commitment["2026-04"] == 0
    assert commitment["2026-05"] == 0


def test_capacity_features_are_point_in_time(request_payload, transaction) -> None:
    table = build_customer_month_features(_product_payload(request_payload, transaction))
    january = table.row("2026-01").to_mapping()
    reasons = {
        item.name: item.missing_reason for item in table.row("2026-01").values
    }

    assert january["card_spend_3m_minor"] == 240_000
    assert january["credit_utilization_ratio"] == 0.46
    assert reasons["monthly_debt_payment_minor"] == "NO_OBSERVED_RECORDS"
    assert reasons["investment_balance_minor"] == "NO_OBSERVED_RECORDS"
    assert january["outstanding_debt_minor"] == 460_000
    assert reasons["available_balance_minor"] == "NO_OBSERVED_RECORDS"
    assert january["observed_domain_count"] == 4


def test_future_products_cannot_change_earlier_rows(request_payload, transaction) -> None:
    payload = _product_payload(request_payload, transaction)
    extended = _product_payload(request_payload, transaction)
    extended["credit_limits"].append(_limit(6, 900_000))
    extended["card_transactions"].append(
        _card_transaction("purchase-late", occurred_at="2026-06-02", amount_minor=500_000)
    )
    extended["investment_balances"].append(_investment_balance(6, 999_000))

    original = build_customer_month_features(payload)
    later = build_customer_month_features(extended)

    for month in ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05"):
        assert original.row(month) == later.row(month)
    assert later.row("2026-06").to_mapping()["investment_balance_minor"] == 999_000


def test_contract_1_1_reports_capacity_as_contractually_unavailable(
    request_payload,
    transaction,
) -> None:
    payload = _product_payload(request_payload, transaction)
    for key in (
        "credit_cards",
        "credit_limits",
        "card_transactions",
        "card_invoices",
        "loan_payments",
        "loan_balances",
        "investments",
        "investment_balances",
    ):
        payload.pop(key)
    payload["schema_version"] = "1.1"
    for collection in ("accounts", "transactions", "loans", "balances"):
        for item in payload[collection]:
            item["schema_version"] = "1.1"

    row = build_customer_month_features(payload).row("2026-03")
    reasons = {item.name: item.missing_reason for item in row.values}

    for name in (
        "card_spend_3m_minor",
        "credit_utilization_ratio",
        "installment_commitment_minor",
        "monthly_debt_payment_minor",
        "outstanding_debt_minor",
        "investment_balance_minor",
    ):
        assert reasons[name] == "CONTRACT_DOMAIN_UNAVAILABLE"


def test_contract_1_2_without_products_reports_no_observed_records(
    request_payload,
    transaction,
) -> None:
    """An empty product collection is a consent or ownership gap, never a zero."""

    payload = _product_payload(request_payload, transaction)
    for key in (
        "credit_cards",
        "credit_limits",
        "card_transactions",
        "card_invoices",
        "loan_payments",
        "loan_balances",
        "investments",
        "investment_balances",
    ):
        payload[key] = []

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
        assert reasons[name] == "NO_OBSERVED_RECORDS"
        assert values[name] is None


def test_point_in_time_slice_of_contract_1_2_remains_valid(
    request_payload,
    transaction,
) -> None:
    request = validate_estimator_input(_product_payload(request_payload, transaction))

    sliced = slice_request(request, date(2026, 2, 28))

    assert isinstance(sliced, EstimatorInputV12)
    assert [item.credit_limit_id for item in sliced.credit_limits] == ["limit-01", "limit-02"]
    assert [item.card_transaction_id for item in sliced.card_transactions] == [
        "purchase-1",
        "purchase-2",
    ]
    assert [item.loan_payment_id for item in sliced.loan_payments] == ["payment-02"]
    assert [item.investment_balance_id for item in sliced.investment_balances] == [
        "investment-balance-02"
    ]
    assert EstimatorInputV12.model_validate(sliced.model_dump(mode="python")) == sliced
