"""Account and ledger-entry domain models."""

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Direction(StrEnum):
    """Direction of a ledger entry relative to its account."""

    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class Account(BaseModel):
    """Simulator-owned representation of a customer's account."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str
    customer_id: str
    institution_id: str
    institution_name: str
    account_label: str
    account_type: Literal["CHECKING", "SAVINGS"] = "CHECKING"
    currency: str
    opened_on: date
    opening_balance_minor: int = Field(ge=0)


class LedgerEntry(BaseModel):
    """Posting produced by a financial event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str
    event_id: str
    account_id: str
    posted_at: date
    direction: Direction
    amount_minor: int = Field(gt=0)
    balance_after_minor: int
    transfer_group_id: str | None = None
    description: str
