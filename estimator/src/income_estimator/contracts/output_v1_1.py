"""Estimator output contract 1.1.

Output `1.0` carries one number per month: reconstructed realized income with a heuristic band. It
cannot say what a customer can sustain, which component produced an estimate, or how much the
estimator trusts it. Contract `1.1` separates those concerns.

Three rules keep the contract honest.

Realized and sustainable income are distinct targets under ADR 0001 and never share a field.
Averaging them would be meaningless.

A quantile is present only when it was calibrated. Estimator `0.6` produces point estimates and
routing, not calibrated intervals, so `p10` and `p90` stay absent until `0.7` measures their
coverage. An absent quantile is `None` with a stated reason, never a point estimate widened by a
guess.

Component estimates stay visible. An ensemble that hides what it combined cannot be audited, so
every component keeps its own value, target, and weight even when routing gave it zero weight.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator

from income_estimator.contracts.v1 import (
    EstimatorContractModel,
    IncomeEstimateV1,
    MonthlyIncomeEstimateV1,
)

ESTIMATOR_OUTPUT_CONTRACT_VERSION = "1.1"

QUANTILE_UNAVAILABLE_UNCALIBRATED = "UNCALIBRATED_INTERVAL"
QUANTILE_UNAVAILABLE_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
QUANTILE_UNAVAILABLE_REASONS = (
    QUANTILE_UNAVAILABLE_INSUFFICIENT_HISTORY,
    QUANTILE_UNAVAILABLE_UNCALIBRATED,
)


class ComponentEstimateV11(EstimatorContractModel):
    """One base estimate the ensemble considered, weighted or not."""

    schema_version: Literal["1.1"] = "1.1"
    component: str = Field(min_length=1)
    target: Literal["REALIZED_INCOME_MONTH", "SUSTAINABLE_MONTHLY_INCOME"]
    estimate_minor: int = Field(ge=0)
    weight_basis_points: int = Field(ge=0, le=10_000)
    model_version: str | None = Field(default=None, min_length=1)


class ConfidenceComponentV11(EstimatorContractModel):
    """One named contribution to the confidence score, in basis points."""

    schema_version: Literal["1.1"] = "1.1"
    name: str = Field(min_length=1)
    value_basis_points: int = Field(ge=0, le=10_000)
    weight_basis_points: int = Field(ge=0, le=10_000)


class MonthlyIncomeEstimateV11(MonthlyIncomeEstimateV1):
    """Realized and sustainable estimates for one reference month.

    `estimated_income_minor` and its band are inherited unchanged, so a `1.0` consumer reads a
    `1.1` record without modification.
    """

    schema_version: Literal["1.1"] = "1.1"
    realized_income_estimate_minor: int = Field(ge=0)
    sustainable_income_p10_minor: int | None = Field(default=None, ge=0)
    sustainable_income_p50_minor: int | None = Field(default=None, ge=0)
    sustainable_income_p90_minor: int | None = Field(default=None, ge=0)
    annual_income_p10_minor: int | None = Field(default=None, ge=0)
    annual_income_p50_minor: int | None = Field(default=None, ge=0)
    annual_income_p90_minor: int | None = Field(default=None, ge=0)
    quantile_unavailable_reason: str | None = Field(default=None, min_length=1)
    component_estimates: tuple[ComponentEstimateV11, ...] = ()
    component_disagreement_basis_points: int | None = Field(default=None, ge=0)
    confidence_score_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    confidence_components: tuple[ConfidenceComponentV11, ...] = ()
    excluded_transaction_ids: tuple[str, ...] = ()
    routing_reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_extensions(self) -> Self:
        if self.realized_income_estimate_minor != self.estimated_income_minor:
            raise ValueError(
                "realized_income_estimate_minor must equal estimated_income_minor"
            )
        self._validate_quantiles(
            "sustainable",
            self.sustainable_income_p10_minor,
            self.sustainable_income_p50_minor,
            self.sustainable_income_p90_minor,
        )
        self._validate_quantiles(
            "annual",
            self.annual_income_p10_minor,
            self.annual_income_p50_minor,
            self.annual_income_p90_minor,
        )
        if (
            self.sustainable_income_p10_minor is None
            and self.sustainable_income_p90_minor is None
            and self.sustainable_income_p50_minor is not None
            and self.quantile_unavailable_reason is None
        ):
            raise ValueError(
                "a point estimate without an interval must state a quantile_unavailable_reason"
            )
        components = [item.component for item in self.component_estimates]
        if len(components) != len(set(components)):
            raise ValueError("component names must be unique within a month")
        names = [item.name for item in self.confidence_components]
        if len(names) != len(set(names)):
            raise ValueError("confidence component names must be unique within a month")
        if self.confidence_components and self.confidence_score_basis_points is None:
            raise ValueError("confidence components require a confidence score")
        overlap = set(self.contributing_transaction_ids) & set(self.excluded_transaction_ids)
        if overlap:
            raise ValueError("a transaction cannot be both contributing and excluded")
        if len(self.excluded_transaction_ids) != len(set(self.excluded_transaction_ids)):
            raise ValueError("excluded_transaction_ids must be unique")
        return self

    @staticmethod
    def _validate_quantiles(
        label: str,
        low: int | None,
        middle: int | None,
        high: int | None,
    ) -> None:
        if (low is not None or high is not None) and middle is None:
            raise ValueError(f"{label} interval requires a p50 estimate")
        ordered = [value for value in (low, middle, high) if value is not None]
        if ordered != sorted(ordered):
            raise ValueError(f"{label} quantiles must be ordered p10 <= p50 <= p90")


class IncomeStreamSummaryV11(EstimatorContractModel):
    """Auditable stream evidence, without any private identifier."""

    schema_version: Literal["1.1"] = "1.1"
    stream_id: str = Field(min_length=1)
    counterparty_cluster: str = Field(min_length=1)
    first_seen: str
    last_seen: str
    frequency: str = Field(min_length=1)
    median_amount_minor: int = Field(ge=0)
    recurrence_score_basis_points: int = Field(ge=0, le=10_000)
    pattern: str = Field(min_length=1)
    transaction_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        for value, field_name in ((self.first_seen, "first_seen"), (self.last_seen, "last_seen")):
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise ValueError(f"{field_name} must be an ISO-8601 calendar date") from error
        if self.last_seen < self.first_seen:
            raise ValueError("last_seen must not precede first_seen")
        return self


class IncomeEstimateV11(IncomeEstimateV1):
    """Versioned envelope carrying every producing component version."""

    schema_version: Literal["1.1"] = "1.1"
    monthly_estimates: tuple[MonthlyIncomeEstimateV11, ...]
    feature_version: str = Field(min_length=1)
    input_contract_version: str = Field(min_length=1)
    model_versions: tuple[str, ...] = ()
    component_versions: tuple[str, ...] = ()
    income_streams: tuple[IncomeStreamSummaryV11, ...] = ()

    @model_validator(mode="after")
    def validate_streams(self) -> Self:
        stream_ids = [item.stream_id for item in self.income_streams]
        if len(stream_ids) != len(set(stream_ids)):
            raise ValueError("stream_id values must be unique")
        return self


__all__ = [
    "ESTIMATOR_OUTPUT_CONTRACT_VERSION",
    "QUANTILE_UNAVAILABLE_INSUFFICIENT_HISTORY",
    "QUANTILE_UNAVAILABLE_REASONS",
    "QUANTILE_UNAVAILABLE_UNCALIBRATED",
    "ComponentEstimateV11",
    "ConfidenceComponentV11",
    "IncomeEstimateV11",
    "IncomeStreamSummaryV11",
    "MonthlyIncomeEstimateV11",
]
