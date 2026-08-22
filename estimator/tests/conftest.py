"""Shared estimator fixtures."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable

import pytest


@pytest.fixture
def request_payload() -> Callable[..., dict[str, object]]:
    def build(*, transactions: list[dict[str, object]], months: int = 2) -> dict[str, object]:
        end_month_index = 2026 * 12 + months
        end_year = (end_month_index - 1) // 12
        end_month = (end_month_index - 1) % 12 + 1
        return {
            "schema_version": "1.0",
            "source_contract_schema_version": "1.5",
            "run_id": "run-test",
            "customer_id": "customer-test",
            "currency": "BRL",
            "window_start": "2026-01-01",
            "window_end": (
                f"{end_year:04d}-{end_month:02d}-"
                f"{monthrange(end_year, end_month)[1]:02d}"
            ),
            "months": months,
            "accounts": [
                {
                    "schema_version": "1.0",
                    "customer_id": "customer-test",
                    "account_id": "checking",
                    "institution_id": "bank-a",
                    "currency": "BRL",
                },
                {
                    "schema_version": "1.0",
                    "customer_id": "customer-test",
                    "account_id": "savings",
                    "institution_id": "bank-b",
                    "currency": "BRL",
                },
            ],
            "transactions": transactions,
            "coverage": [],
        }

    return build


@pytest.fixture
def transaction() -> Callable[..., dict[str, object]]:
    def build(
        transaction_id: str,
        *,
        posted_at: str = "2026-01-05",
        observed_at: str | None = None,
        direction: str = "CREDIT",
        amount_minor: int = 500_000,
        description: str = "MONTHLY PAYROLL CREDIT",
        account_id: str = "checking",
        duplicate_of_transaction_id: str | None = None,
        reversal_of_transaction_id: str | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "transaction_id": transaction_id,
            "customer_id": "customer-test",
            "account_id": account_id,
            "posted_at": posted_at,
            "observed_at": observed_at or posted_at,
            "direction": direction,
            "amount_minor": amount_minor,
            "currency": "BRL",
            "description": description,
            "duplicate_of_transaction_id": duplicate_of_transaction_id,
            "reversal_of_transaction_id": reversal_of_transaction_id,
        }

    return build
