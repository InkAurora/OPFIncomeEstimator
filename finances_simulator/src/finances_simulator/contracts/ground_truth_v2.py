"""Schema 1.2 private ground-truth contracts."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finances_simulator.domain.events import EconomicType
from finances_simulator.ground_truth.contracts_v1 import (
    CardTransactionGroundTruthV1,
    CustomerGroundTruthV1,
    CustomerMonthGroundTruthV1,
    TransactionGroundTruthV1,
)


class GroundTruthModelV2(BaseModel):
    """Strict immutable base for schema 1.2 private records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.2"] = "1.2"


class CustomerGroundTruthV2(CustomerGroundTruthV1):
    """Private customer truth extended with loan and investment products."""

    schema_version: Literal["1.2"] = "1.2"
    loan_ids: tuple[str, ...]
    investment_ids: tuple[str, ...]
    total_opening_investment_balance_minor: int = Field(ge=0)
    total_opening_loan_principal_minor: int = Field(ge=0)


class CustomerMonthGroundTruthV2(CustomerMonthGroundTruthV1):
    """Monthly economic truth with financing cost and investment gain."""

    schema_version: Literal["1.2"] = "1.2"
    loan_interest_paid_minor: int = Field(ge=0)
    investment_return_minor: int = Field(ge=0)


class TransactionGroundTruthV2(TransactionGroundTruthV1):
    schema_version: Literal["1.2"] = "1.2"


class CardTransactionGroundTruthV2(CardTransactionGroundTruthV1):
    schema_version: Literal["1.2"] = "1.2"


class LoanPaymentGroundTruthV2(GroundTruthModelV2):
    """Private component truth for one paid loan installment."""

    event_id: str
    loan_payment_id: str
    customer_id: str
    loan_id: str
    occurred_at: str
    economic_type: EconomicType
    installment_number: int = Field(gt=0)
    installment_count: int = Field(gt=0)
    opening_principal_minor: int = Field(gt=0)
    principal_amount_minor: int = Field(gt=0)
    interest_amount_minor: int = Field(ge=0)
    total_amount_minor: int = Field(gt=0)
    remaining_principal_after_minor: int = Field(ge=0)
    currency: str
    source_entity: str
    destination_entity: str
    description: str
    metadata: dict[str, str | int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def payment_must_reconcile(self) -> Self:
        if self.economic_type is not EconomicType.LOAN_PAYMENT:
            raise ValueError("loan payment economic_type must be LOAN_PAYMENT")
        if self.installment_number > self.installment_count:
            raise ValueError("installment_number must not exceed installment_count")
        if self.opening_principal_minor != (
            self.principal_amount_minor + self.remaining_principal_after_minor
        ):
            raise ValueError(
                "opening_principal_minor must equal principal_amount_minor + "
                "remaining_principal_after_minor"
            )
        if self.total_amount_minor != (self.principal_amount_minor + self.interest_amount_minor):
            raise ValueError(
                "total_amount_minor must equal principal_amount_minor + interest_amount_minor"
            )
        return self


class InvestmentTransactionGroundTruthV2(GroundTruthModelV2):
    """Private truth for one investment flow or return."""

    event_id: str
    investment_transaction_id: str
    customer_id: str
    investment_id: str
    occurred_at: str
    economic_type: EconomicType
    transaction_type: Literal["CONTRIBUTION", "REDEMPTION", "RETURN"]
    amount_minor: int = Field(gt=0)
    currency: str
    source_entity: str
    destination_entity: str
    description: str
    balance_after_minor: int = Field(ge=0)
    rule_id: str | None = None
    occurrence_index: int | None = Field(default=None, ge=0)
    metadata: dict[str, str | int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def economic_type_must_match_transaction_type(self) -> Self:
        expected = {
            "CONTRIBUTION": EconomicType.INVESTMENT_CONTRIBUTION,
            "REDEMPTION": EconomicType.INVESTMENT_REDEMPTION,
            "RETURN": EconomicType.INVESTMENT_RETURN,
        }[self.transaction_type]
        if self.economic_type is not expected:
            raise ValueError("economic_type must match transaction_type")
        has_rule = self.rule_id is not None and self.occurrence_index is not None
        has_no_rule = self.rule_id is None and self.occurrence_index is None
        if self.transaction_type == "RETURN":
            if not has_no_rule:
                raise ValueError("RETURN cannot contain schedule-rule linkage")
        elif not has_rule:
            raise ValueError("external investment flow requires schedule-rule linkage")
        return self


class BalanceSheetGroundTruthV2(GroundTruthModelV2):
    """Private reconciled month-end balance sheet."""

    balance_sheet_id: str
    customer_id: str
    month: str
    reference_date: str
    currency: str
    opening_total_deposit_balance_minor: int
    opening_total_investment_balance_minor: int = Field(ge=0)
    opening_total_assets_minor: int
    opening_total_card_outstanding_minor: int = Field(ge=0)
    opening_total_loan_principal_minor: int = Field(ge=0)
    opening_total_liabilities_minor: int = Field(ge=0)
    opening_net_worth_minor: int
    total_deposit_balance_minor: int
    total_investment_balance_minor: int = Field(ge=0)
    total_assets_minor: int
    total_card_outstanding_minor: int = Field(ge=0)
    total_loan_principal_minor: int = Field(ge=0)
    total_liabilities_minor: int = Field(ge=0)
    net_worth_minor: int

    @model_validator(mode="after")
    def balance_sheet_must_reconcile(self) -> Self:
        if self.opening_total_assets_minor != (
            self.opening_total_deposit_balance_minor + self.opening_total_investment_balance_minor
        ):
            raise ValueError(
                "opening_total_assets_minor must equal opening deposit balance + "
                "opening investment balance"
            )
        if self.opening_total_liabilities_minor != (
            self.opening_total_card_outstanding_minor + self.opening_total_loan_principal_minor
        ):
            raise ValueError(
                "opening_total_liabilities_minor must equal opening card outstanding + "
                "opening loan principal"
            )
        if self.opening_net_worth_minor != (
            self.opening_total_assets_minor - self.opening_total_liabilities_minor
        ):
            raise ValueError("opening_net_worth_minor must equal opening assets minus liabilities")
        if self.total_assets_minor != (
            self.total_deposit_balance_minor + self.total_investment_balance_minor
        ):
            raise ValueError("total_assets_minor must equal deposit balance + investment balance")
        if self.total_liabilities_minor != (
            self.total_card_outstanding_minor + self.total_loan_principal_minor
        ):
            raise ValueError("total_liabilities_minor must equal card outstanding + loan principal")
        if self.net_worth_minor != self.total_assets_minor - self.total_liabilities_minor:
            raise ValueError("net_worth_minor must equal assets minus liabilities")
        return self


__all__ = [
    "BalanceSheetGroundTruthV2",
    "CardTransactionGroundTruthV2",
    "CustomerGroundTruthV2",
    "CustomerMonthGroundTruthV2",
    "GroundTruthModelV2",
    "InvestmentTransactionGroundTruthV2",
    "LoanPaymentGroundTruthV2",
    "TransactionGroundTruthV2",
]
