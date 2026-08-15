"""Loan domain models for deterministic constant-principal contracts."""

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LoanStatus(StrEnum):
    """Loan lifecycle at one simulation boundary."""

    ACTIVE = "ACTIVE"
    PAID_OFF = "PAID_OFF"


class LoanPaymentStatus(StrEnum):
    """Contractual installment settlement state."""

    SCHEDULED = "SCHEDULED"
    PAID = "PAID"


class Loan(BaseModel):
    """Originated constant-principal loan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    loan_id: str
    customer_id: str
    institution_id: str
    institution_name: str
    loan_label: str
    loan_type: str
    currency: str
    originated_at: date
    principal_minor: int = Field(gt=0)
    annual_interest_basis_points: int = Field(ge=1, le=10_000)
    term_months: int = Field(ge=1, le=480)
    amortization_system: str
    disbursement_account_id: str
    payment_account_id: str
    disbursement_event_id: str
    disbursement_description: str
    payment_description: str

    @model_validator(mode="after")
    def principal_must_cover_term(self) -> Self:
        if self.principal_minor < self.term_months:
            raise ValueError("principal_minor must be greater than or equal to term_months")
        if self.loan_type != "PERSONAL":
            raise ValueError("loan_type must be PERSONAL")
        if self.amortization_system != "CONSTANT_PRINCIPAL":
            raise ValueError("amortization_system must be CONSTANT_PRINCIPAL")
        return self


class LoanPayment(BaseModel):
    """One scheduled or paid constant-principal installment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payment_id: str
    loan_id: str
    customer_id: str
    installment_number: int = Field(gt=0)
    installment_count: int = Field(gt=0)
    due_date: date
    opening_principal_minor: int = Field(gt=0)
    principal_minor: int = Field(gt=0)
    interest_minor: int = Field(ge=0)
    payment_minor: int = Field(gt=0)
    remaining_principal_minor: int = Field(ge=0)
    status: LoanPaymentStatus
    paid_at: date | None = None
    payment_event_id: str | None = None

    @model_validator(mode="after")
    def payment_state_must_reconcile(self) -> Self:
        """Require exact component math and coherent payment lifecycle."""

        if self.installment_number > self.installment_count:
            raise ValueError("installment_number must not exceed installment_count")
        if self.opening_principal_minor != (self.principal_minor + self.remaining_principal_minor):
            raise ValueError(
                "opening_principal_minor must equal principal_minor + remaining_principal_minor"
            )
        if self.payment_minor != self.principal_minor + self.interest_minor:
            raise ValueError("payment_minor must equal principal_minor + interest_minor")
        if self.status is LoanPaymentStatus.PAID:
            if self.paid_at != self.due_date or self.payment_event_id is None:
                raise ValueError("PAID loan payment requires due-date payment references")
        elif self.paid_at is not None or self.payment_event_id is not None:
            raise ValueError("SCHEDULED loan payment cannot contain payment references")
        return self


class LoanBalanceSnapshot(BaseModel):
    """Remaining principal at one month-end reference date."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    customer_id: str
    loan_id: str
    reference_date: date
    remaining_principal_minor: int = Field(ge=0)
    currency: str


__all__ = [
    "Loan",
    "LoanBalanceSnapshot",
    "LoanPayment",
    "LoanPaymentStatus",
    "LoanStatus",
]
