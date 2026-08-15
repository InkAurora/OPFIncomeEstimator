"""Versioned project-owned observation contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finances_simulator.domain.accounts import Direction


class ObservationModel(BaseModel):
    """Strict immutable base for estimator-visible records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"


class ObservedAccount(ObservationModel):
    customer_id: str
    account_id: str
    institution_id: str
    institution_name: str
    account_label: str
    account_type: Literal["CHECKING"]
    currency: str
    opened_on: str
    status: Literal["ACTIVE"] = "ACTIVE"


class ObservedTransaction(ObservationModel):
    transaction_id: str
    customer_id: str
    account_id: str
    posted_at: str
    direction: Direction
    amount_minor: int = Field(gt=0)
    currency: str
    description: str
    balance_after_minor: int


class ObservedBalance(ObservationModel):
    balance_id: str
    customer_id: str
    account_id: str
    reference_date: str
    balance_minor: int
    currency: str
    balance_type: Literal["CLOSING"] = "CLOSING"
