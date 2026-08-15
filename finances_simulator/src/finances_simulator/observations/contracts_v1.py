"""Schema 1.1 project-owned observation contracts."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finances_simulator.domain.accounts import Direction


class ObservationModelV1(BaseModel):
    """Strict immutable base for schema 1.1 estimator-visible records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.1"] = "1.1"


class AccountV1(ObservationModelV1):
    """Observed deposit-account record."""

    customer_id: str
    account_id: str
    institution_id: str
    institution_name: str
    account_label: str
    account_type: Literal["CHECKING", "SAVINGS"]
    currency: str
    opened_on: str
    status: Literal["ACTIVE"] = "ACTIVE"


class BalanceV1(ObservationModelV1):
    """Observed deposit-account closing balance."""

    balance_id: str
    customer_id: str
    account_id: str
    reference_date: str
    balance_minor: int
    currency: str
    balance_type: Literal["CLOSING"] = "CLOSING"


class TransactionV1(ObservationModelV1):
    """Observed deposit-account transaction."""

    transaction_id: str
    customer_id: str
    account_id: str
    posted_at: str
    direction: Direction
    amount_minor: int = Field(gt=0)
    currency: str
    description: str
    balance_after_minor: int


class CreditCardV1(ObservationModelV1):
    """Observed credit-card record."""

    customer_id: str
    card_id: str
    institution_id: str
    institution_name: str
    card_label: str
    currency: str
    opened_on: str
    status: Literal["ACTIVE"] = "ACTIVE"


class CreditLimitV1(ObservationModelV1):
    """Observed credit-limit snapshot."""

    credit_limit_id: str
    customer_id: str
    card_id: str
    reference_date: str
    total_limit_minor: int = Field(gt=0)
    used_limit_minor: int = Field(ge=0)
    available_limit_minor: int = Field(ge=0)
    currency: str

    @model_validator(mode="after")
    def total_must_equal_used_plus_available(self) -> Self:
        """Reject internally inconsistent limit snapshots."""

        if self.total_limit_minor != self.used_limit_minor + self.available_limit_minor:
            raise ValueError(
                "total_limit_minor must equal used_limit_minor + available_limit_minor"
            )
        return self


class CardTransactionV1(ObservationModelV1):
    """Observed credit-card purchase."""

    card_transaction_id: str
    customer_id: str
    card_id: str
    occurred_at: str
    amount_minor: int = Field(gt=0)
    currency: str
    description: str
    installment_count: int = Field(gt=0)
    status: Literal["POSTED"] = "POSTED"

    @model_validator(mode="after")
    def purchase_must_cover_installments(self) -> Self:
        if self.installment_count > self.amount_minor:
            raise ValueError("installment_count must not exceed amount_minor")
        return self


class CardInvoiceV1(ObservationModelV1):
    """Observed closed or fully paid credit-card invoice."""

    invoice_id: str
    customer_id: str
    card_id: str
    statement_close_date: str
    due_date: str
    amount_due_minor: int = Field(gt=0)
    paid_amount_minor: int = Field(ge=0)
    currency: str
    status: Literal["CLOSED", "PAID"]
    paid_at: str | None = None
    payment_transaction_id: str | None = None

    @model_validator(mode="after")
    def payment_fields_must_match_status(self) -> Self:
        """Keep invoice status, amount, date, and payment reference consistent."""

        if self.due_date <= self.statement_close_date:
            raise ValueError("due_date must be after statement_close_date")
        if self.status == "PAID":
            if self.paid_amount_minor != self.amount_due_minor:
                raise ValueError("PAID invoice paid_amount_minor must equal amount_due_minor")
            if self.paid_at is None or self.payment_transaction_id is None:
                raise ValueError("PAID invoice requires paid_at and payment_transaction_id")
            if self.paid_at != self.due_date:
                raise ValueError("PAID invoice paid_at must equal due_date")
        elif (
            self.paid_amount_minor != 0
            or self.paid_at is not None
            or self.payment_transaction_id is not None
        ):
            raise ValueError(
                "CLOSED invoice requires zero paid_amount_minor and no payment details"
            )
        return self


class CardInvoiceItemV1(ObservationModelV1):
    """Observed installment item assigned to one credit-card invoice."""

    invoice_item_id: str
    customer_id: str
    card_id: str
    invoice_id: str
    card_transaction_id: str
    installment_number: int = Field(gt=0)
    installment_count: int = Field(gt=0)
    amount_minor: int = Field(gt=0)
    currency: str
    description: str

    @model_validator(mode="after")
    def installment_number_must_fit_count(self) -> Self:
        if self.installment_number > self.installment_count:
            raise ValueError("installment_number must not exceed installment_count")
        return self


__all__ = [
    "AccountV1",
    "BalanceV1",
    "CardInvoiceItemV1",
    "CardInvoiceV1",
    "CardTransactionV1",
    "CreditCardV1",
    "CreditLimitV1",
    "ObservationModelV1",
    "TransactionV1",
]
