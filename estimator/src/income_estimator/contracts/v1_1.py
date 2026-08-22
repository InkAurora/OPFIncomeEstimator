"""Estimator input contract 1.1 with optional provider-visible context."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from income_estimator.contracts.v1 import (
    EstimatorAccountV1,
    EstimatorContractModel,
    EstimatorCoverageV1,
    EstimatorInputV1,
    EstimatorInvestmentTransactionV1,
    EstimatorLoanV1,
    EstimatorTransactionV1,
    _parse_date,
)

ESTIMATOR_INPUT_CONTRACT_VERSION = "1.1"


class EstimatorAccountV11(EstimatorAccountV1):
    schema_version: Literal["1.1"] = "1.1"


class EstimatorTransactionV11(EstimatorTransactionV1):
    schema_version: Literal["1.1"] = "1.1"
    provider_transaction_type: str | None = Field(default=None, min_length=1)
    counterparty_name: str | None = Field(default=None, min_length=1)
    counterparty_document_hash: str | None = Field(
        default=None,
        min_length=16,
        max_length=128,
    )
    balance_after_minor: int | None = None


class EstimatorLoanV11(EstimatorLoanV1):
    schema_version: Literal["1.1"] = "1.1"


class EstimatorInvestmentTransactionV11(EstimatorInvestmentTransactionV1):
    schema_version: Literal["1.1"] = "1.1"


class EstimatorCoverageV11(EstimatorCoverageV1):
    schema_version: Literal["1.1"] = "1.1"


class EstimatorBalanceV11(EstimatorContractModel):
    schema_version: Literal["1.1"] = "1.1"
    balance_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    reference_date: str
    balance_minor: int
    currency: str = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_reference_date(self) -> Self:
        _parse_date(self.reference_date, "reference_date")
        return self


class EstimatorInputV11(EstimatorInputV1):
    """Backward-compatible input extension; all new provider fields remain optional."""

    schema_version: Literal["1.1"] = "1.1"
    accounts: tuple[EstimatorAccountV11, ...]
    transactions: tuple[EstimatorTransactionV11, ...]
    loans: tuple[EstimatorLoanV11, ...] = ()
    investment_transactions: tuple[EstimatorInvestmentTransactionV11, ...] = ()
    coverage: tuple[EstimatorCoverageV11, ...] = ()
    balances: tuple[EstimatorBalanceV11, ...] = ()

    @model_validator(mode="after")
    def validate_balances(self) -> Self:
        account_ids = {record.account_id for record in self.accounts}
        if any(record.customer_id != self.customer_id for record in self.balances):
            raise ValueError("all balances must belong to customer_id")
        if any(record.account_id not in account_ids for record in self.balances):
            raise ValueError("balances must reference an observed account")
        if any(record.currency != self.currency for record in self.balances):
            raise ValueError("balance currencies must match input currency")
        balance_ids = [record.balance_id for record in self.balances]
        if len(balance_ids) != len(set(balance_ids)):
            raise ValueError("balance_id values must be unique")
        return self


__all__ = [
    "ESTIMATOR_INPUT_CONTRACT_VERSION",
    "EstimatorAccountV11",
    "EstimatorBalanceV11",
    "EstimatorCoverageV11",
    "EstimatorInputV11",
    "EstimatorInvestmentTransactionV11",
    "EstimatorLoanV11",
    "EstimatorTransactionV11",
]
