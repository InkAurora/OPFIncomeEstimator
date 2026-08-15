"""Credit-card domain models."""

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InvoiceStatus(StrEnum):
    """Lifecycle status of a credit-card invoice."""

    CLOSED = "CLOSED"
    PAID = "PAID"


class CreditCard(BaseModel):
    """Simulator-owned representation of a customer's credit card."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    card_id: str
    customer_id: str
    institution_id: str
    institution_name: str
    card_label: str
    currency: str
    opened_on: date
    payment_account_id: str
    credit_limit_minor: int = Field(gt=0)
    maximum_utilization_basis_points: int = Field(ge=1, le=10_000)
    statement_close_day: int = Field(ge=1, le=31)
    payment_due_day: int = Field(ge=1, le=31)
    payment_description: str


class CardPurchase(BaseModel):
    """Ground-truth purchase made with a credit card."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    purchase_id: str
    event_id: str
    customer_id: str
    card_id: str
    purchased_at: date
    amount_minor: int = Field(gt=0)
    currency: str
    merchant: str
    description: str
    installment_count: int = Field(gt=0)
    rule_id: str
    occurrence_index: int = Field(ge=0)
    used_limit_after_purchase_minor: int = Field(ge=0)

    @model_validator(mode="after")
    def used_limit_must_include_purchase(self) -> Self:
        if self.installment_count > self.amount_minor:
            raise ValueError("installment_count must not exceed amount_minor")
        if self.used_limit_after_purchase_minor < self.amount_minor:
            raise ValueError("used limit after purchase must include the full purchase amount")
        return self


class CardInstallment(BaseModel):
    """One purchase installment assigned to a card invoice."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    invoice_item_id: str
    purchase_id: str
    card_id: str
    invoice_id: str
    statement_close_date: date
    due_date: date
    installment_number: int = Field(gt=0)
    installment_count: int = Field(gt=0)
    amount_minor: int = Field(gt=0)
    description: str

    @model_validator(mode="after")
    def schedule_must_be_possible(self) -> Self:
        if self.installment_number > self.installment_count:
            raise ValueError("installment_number must not exceed installment_count")
        if self.due_date <= self.statement_close_date:
            raise ValueError("due_date must be after statement_close_date")
        return self


class CardInvoice(BaseModel):
    """Closed credit-card statement and its payment state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    invoice_id: str
    customer_id: str
    card_id: str
    statement_close_date: date
    due_date: date
    amount_due_minor: int = Field(gt=0)
    paid_amount_minor: int = Field(ge=0)
    status: InvoiceStatus
    paid_at: date | None = None
    payment_event_id: str | None = None
    installment_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def payment_state_must_reconcile(self) -> Self:
        if self.due_date <= self.statement_close_date:
            raise ValueError("due_date must be after statement_close_date")
        if len(self.installment_ids) != len(set(self.installment_ids)):
            raise ValueError("installment_ids must be unique")
        if self.status is InvoiceStatus.PAID:
            if self.paid_amount_minor != self.amount_due_minor:
                raise ValueError("PAID invoice amount must reconcile")
            if self.paid_at != self.due_date or self.payment_event_id is None:
                raise ValueError("PAID invoice requires due-date payment references")
        elif (
            self.paid_amount_minor != 0
            or self.paid_at is not None
            or self.payment_event_id is not None
        ):
            raise ValueError("CLOSED invoice cannot contain payment details")
        return self


class CreditLimitSnapshot(BaseModel):
    """Credit-limit utilization at one reference date."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    customer_id: str
    card_id: str
    reference_date: date
    total_limit_minor: int = Field(ge=0)
    used_limit_minor: int = Field(ge=0)
    available_limit_minor: int = Field(ge=0)
    currency: str

    @model_validator(mode="after")
    def total_must_equal_used_plus_available(self) -> "CreditLimitSnapshot":
        """Require limit components to reconcile exactly."""

        if self.total_limit_minor != self.used_limit_minor + self.available_limit_minor:
            raise ValueError(
                "total_limit_minor must equal used_limit_minor + available_limit_minor"
            )
        return self


__all__ = [
    "CardInstallment",
    "CardInvoice",
    "CardPurchase",
    "CreditCard",
    "CreditLimitSnapshot",
    "InvoiceStatus",
]
