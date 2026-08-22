"""Customer-month feature contract 1.0.

The feature table is an internal, versioned artifact rather than part of shared output contract
1.0. Later milestones train the capacity estimator on it, so it records the feature-set version,
schema fingerprint, and every producing component version alongside the values.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CUSTOMER_MONTH_FEATURE_CONTRACT_VERSION = "1.0"


class FeatureContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CustomerMonthFeatureValueV1(FeatureContractModel):
    """One feature value, or one explicit reason it could not be computed."""

    name: str = Field(min_length=1)
    value: int | float | None = None
    missing_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_missingness(self) -> Self:
        if self.value is None and self.missing_reason is None:
            raise ValueError("a feature without a value must carry a missing_reason")
        if self.value is not None and self.missing_reason is not None:
            raise ValueError("a feature with a value must not carry a missing_reason")
        return self


class CustomerMonthFeatureRowV1(FeatureContractModel):
    """Point-in-time features for one customer and one reference month."""

    schema_version: Literal["1.0"] = "1.0"
    customer_id: str = Field(min_length=1)
    reference_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    as_of_date: str
    currency: str = Field(min_length=3, max_length=3)
    values: tuple[CustomerMonthFeatureValueV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_row(self) -> Self:
        try:
            date.fromisoformat(self.as_of_date)
            date.fromisoformat(f"{self.reference_month}-01")
        except ValueError as error:
            raise ValueError("reference_month and as_of_date must be calendar dates") from error
        if self.as_of_date[:7] != self.reference_month:
            raise ValueError("as_of_date must fall inside reference_month")
        names = [item.name for item in self.values]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique within a row")
        return self

    @property
    def missing_features(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.values if item.missing_reason is not None)

    def to_mapping(self) -> dict[str, int | float | None]:
        """Return a flat name-to-value mapping in schema order for downstream training."""

        return {item.name: item.value for item in self.values}

    def to_vector(self, names: tuple[str, ...]) -> tuple[int | float | None, ...]:
        mapping = self.to_mapping()
        return tuple(mapping[name] for name in names)


class CustomerMonthFeatureTableV1(FeatureContractModel):
    """Ordered per-customer feature rows plus the versions that produced them."""

    schema_version: Literal["1.0"] = "1.0"
    feature_set_version: str = Field(min_length=1)
    feature_schema_fingerprint: str = Field(min_length=16, max_length=64)
    transaction_feature_version: str = Field(min_length=1)
    estimator_version: str = Field(min_length=1)
    input_contract_version: str = Field(min_length=1)
    model_versions: tuple[str, ...] = ()
    run_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    rows: tuple[CustomerMonthFeatureRowV1, ...]

    @model_validator(mode="after")
    def validate_table(self) -> Self:
        months = tuple(row.reference_month for row in self.rows)
        if months != tuple(sorted(months)) or len(months) != len(set(months)):
            raise ValueError("feature rows must be uniquely ordered by reference_month")
        if any(row.customer_id != self.customer_id for row in self.rows):
            raise ValueError("all feature rows must belong to customer_id")
        if any(row.currency != self.currency for row in self.rows):
            raise ValueError("all feature rows must use the table currency")
        return self

    def row(self, reference_month: str) -> CustomerMonthFeatureRowV1:
        for item in self.rows:
            if item.reference_month == reference_month:
                return item
        raise KeyError(reference_month)


__all__ = [
    "CUSTOMER_MONTH_FEATURE_CONTRACT_VERSION",
    "CustomerMonthFeatureRowV1",
    "CustomerMonthFeatureTableV1",
    "CustomerMonthFeatureValueV1",
]
