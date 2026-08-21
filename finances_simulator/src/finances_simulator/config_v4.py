"""Validated configuration contract for Phase-5 life-event scenarios."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from finances_simulator.config import ConfigModel, NonEmptyString
from finances_simulator.config_v1 import ReferenceId
from finances_simulator.config_v3 import ScenarioConfigV3
from finances_simulator.domain.income import MAX_INCOME_AMOUNT_MINOR, SeasonalityBasisPoints
from finances_simulator.domain.life_events import MaritalStatus


class InitialLifeStateSettings(ConfigModel):
    """Non-financial customer state at the start of the simulation."""

    marital_status: MaritalStatus = MaritalStatus.SINGLE
    dependent_count: int = Field(default=0, ge=0, le=100)
    property_count: int = Field(default=0, ge=0, le=100)
    vehicle_count: int = Field(default=0, ge=0, le=100)
    job_title: NonEmptyString | None = None


class SeasonalitySettings(ConfigModel):
    """Calendar-month multipliers layered over source and behavior settings."""

    income_multipliers_basis_points: tuple[SeasonalityBasisPoints, ...] = Field(
        default=(10_000,) * 12,
        min_length=12,
        max_length=12,
    )
    expense_multipliers_basis_points: tuple[SeasonalityBasisPoints, ...] = Field(
        default=(10_000,) * 12,
        min_length=12,
        max_length=12,
    )


class LifeEventSettings(ConfigModel):
    """Fields shared by all configured life events."""

    life_event_ref: ReferenceId
    effective_date: date


class RaiseEventSettings(LifeEventSettings):
    event_type: Literal["RAISE"]
    income_source_ref: ReferenceId
    new_base_amount_minor: int | None = Field(
        default=None,
        gt=0,
        le=MAX_INCOME_AMOUNT_MINOR,
    )
    amount_multiplier_basis_points: int | None = Field(
        default=None,
        gt=0,
        le=100_000,
    )

    @model_validator(mode="after")
    def require_one_amount_change(self) -> Self:
        if (self.new_base_amount_minor is None) == (self.amount_multiplier_basis_points is None):
            raise ValueError(
                "exactly one of new_base_amount_minor or amount_multiplier_basis_points "
                "must be provided"
            )
        return self


class PromotionEventSettings(LifeEventSettings):
    event_type: Literal["PROMOTION"]
    income_source_ref: ReferenceId
    new_job_title: NonEmptyString
    new_base_amount_minor: int | None = Field(
        default=None,
        gt=0,
        le=MAX_INCOME_AMOUNT_MINOR,
    )
    amount_multiplier_basis_points: int | None = Field(
        default=None,
        gt=0,
        le=100_000,
    )

    @model_validator(mode="after")
    def amount_change_must_be_unambiguous(self) -> Self:
        if (
            self.new_base_amount_minor is not None
            and self.amount_multiplier_basis_points is not None
        ):
            raise ValueError(
                "new_base_amount_minor and amount_multiplier_basis_points are mutually exclusive"
            )
        return self


class JobLossEventSettings(LifeEventSettings):
    event_type: Literal["JOB_LOSS"]
    income_source_ref: ReferenceId


class JobChangeEventSettings(LifeEventSettings):
    event_type: Literal["JOB_CHANGE"]
    income_source_ref: ReferenceId
    new_payer: NonEmptyString
    new_description: NonEmptyString
    new_base_amount_minor: int = Field(gt=0, le=MAX_INCOME_AMOUNT_MINOR)
    new_job_title: NonEmptyString | None = None


class MarriageEventSettings(LifeEventSettings):
    event_type: Literal["MARRIAGE"]


class DivorceEventSettings(LifeEventSettings):
    event_type: Literal["DIVORCE"]


class DependentAddedEventSettings(LifeEventSettings):
    event_type: Literal["DEPENDENT_ADDED"]
    count: int = Field(default=1, ge=1, le=20)


class DependentRemovedEventSettings(LifeEventSettings):
    event_type: Literal["DEPENDENT_REMOVED"]
    count: int = Field(default=1, ge=1, le=20)


class PurchaseLifeEventSettings(LifeEventSettings):
    source_account_ref: ReferenceId
    amount_minor: int = Field(gt=0)
    counterparty: NonEmptyString
    description: NonEmptyString


class PropertyPurchaseEventSettings(PurchaseLifeEventSettings):
    event_type: Literal["PROPERTY_PURCHASE"]


class VehiclePurchaseEventSettings(PurchaseLifeEventSettings):
    event_type: Literal["VEHICLE_PURCHASE"]


class BonusEventSettings(LifeEventSettings):
    event_type: Literal["BONUS"]
    income_source_ref: ReferenceId
    amount_minor: int = Field(gt=0, le=MAX_INCOME_AMOUNT_MINOR)
    description: NonEmptyString


class InheritanceEventSettings(LifeEventSettings):
    event_type: Literal["INHERITANCE"]
    destination_account_ref: ReferenceId
    amount_minor: int = Field(gt=0)
    source_entity: NonEmptyString
    description: NonEmptyString


class ExpenseLifeEventSettings(LifeEventSettings):
    source_account_ref: ReferenceId
    amount_minor: int = Field(gt=0)
    payee: NonEmptyString
    description: NonEmptyString


class MedicalExpenseEventSettings(ExpenseLifeEventSettings):
    event_type: Literal["MEDICAL_EXPENSE"]


class VacationEventSettings(ExpenseLifeEventSettings):
    event_type: Literal["VACATION"]


LifeEventConfig = Annotated[
    RaiseEventSettings
    | PromotionEventSettings
    | JobLossEventSettings
    | JobChangeEventSettings
    | MarriageEventSettings
    | DivorceEventSettings
    | DependentAddedEventSettings
    | DependentRemovedEventSettings
    | PropertyPurchaseEventSettings
    | VehiclePurchaseEventSettings
    | BonusEventSettings
    | InheritanceEventSettings
    | MedicalExpenseEventSettings
    | VacationEventSettings,
    Field(discriminator="event_type"),
]


class AnomalySettings(ConfigModel):
    """Fields shared by all private anomaly labels."""

    anomaly_ref: ReferenceId
    occurred_at: date
    amount_minor: int = Field(gt=0)


class LargePixTransferAnomalySettings(AnomalySettings):
    anomaly_type: Literal["LARGE_PIX_TRANSFER"]
    source_account_ref: ReferenceId
    destination_account_ref: ReferenceId
    outgoing_description: NonEmptyString
    incoming_description: NonEmptyString

    @model_validator(mode="after")
    def endpoints_must_differ(self) -> Self:
        if self.source_account_ref == self.destination_account_ref:
            raise ValueError("source_account_ref and destination_account_ref must differ")
        return self


class RefundAnomalySettings(AnomalySettings):
    anomaly_type: Literal["REFUND"]
    destination_account_ref: ReferenceId
    source_entity: NonEmptyString
    description: NonEmptyString


class AssetSaleAnomalySettings(AnomalySettings):
    anomaly_type: Literal["ASSET_SALE"]
    destination_account_ref: ReferenceId
    buyer: NonEmptyString
    asset_type: NonEmptyString
    description: NonEmptyString


class InvestmentRedemptionAnomalySettings(AnomalySettings):
    anomaly_type: Literal["INVESTMENT_REDEMPTION"]
    investment_ref: ReferenceId
    destination_account_ref: ReferenceId
    description: NonEmptyString


AnomalyConfig = Annotated[
    LargePixTransferAnomalySettings
    | RefundAnomalySettings
    | AssetSaleAnomalySettings
    | InvestmentRedemptionAnomalySettings,
    Field(discriminator="anomaly_type"),
]


class ScenarioConfigV4(ScenarioConfigV3):
    """Complete validated configuration for contract schema 1.4."""

    schema_version: Literal["1.4"]
    initial_life_state: InitialLifeStateSettings = Field(default_factory=InitialLifeStateSettings)
    seasonality: SeasonalitySettings = Field(default_factory=SeasonalitySettings)
    life_events: list[LifeEventConfig] = Field(default_factory=list, max_length=256)
    anomalies: list[AnomalyConfig] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def validate_phase_five_references(self) -> Self:
        errors: list[str] = []
        event_refs = [item.life_event_ref for item in self.life_events]
        anomaly_refs = [item.anomaly_ref for item in self.anomalies]
        if len(event_refs) != len(set(event_refs)):
            errors.append("life_events.life_event_ref values must be unique")
        if len(anomaly_refs) != len(set(anomaly_refs)):
            errors.append("anomalies.anomaly_ref values must be unique")
        if set(event_refs) & set(anomaly_refs):
            errors.append("life-event and anomaly references must not overlap")

        known_accounts = {item.account_ref for item in self.accounts}
        known_investments = {item.investment_ref for item in self.investments}
        known_sources = {
            source.source_ref
            for profile in self.customer_factory.income_profiles
            for bundle in profile.source_bundles
            for source in bundle.sources
        }

        for item in self.life_events:
            if item.effective_date < self.scenario.start_date:
                errors.append(f"life event {item.life_event_ref!r} precedes scenario.start_date")
            source_ref = getattr(item, "income_source_ref", None)
            if source_ref is not None and source_ref not in known_sources:
                errors.append(
                    f"life event {item.life_event_ref!r} references unknown income source "
                    f"{source_ref!r}"
                )
            account_ref = getattr(item, "source_account_ref", None)
            if account_ref is not None and account_ref not in known_accounts:
                errors.append(
                    f"life event {item.life_event_ref!r} references unknown source account "
                    f"{account_ref!r}"
                )
            account_ref = getattr(item, "destination_account_ref", None)
            if account_ref is not None and account_ref not in known_accounts:
                errors.append(
                    f"life event {item.life_event_ref!r} references unknown destination "
                    f"account {account_ref!r}"
                )

        for item in self.anomalies:
            if item.occurred_at < self.scenario.start_date:
                errors.append(f"anomaly {item.anomaly_ref!r} precedes scenario.start_date")
            for field_name in ("source_account_ref", "destination_account_ref"):
                account_ref = getattr(item, field_name, None)
                if account_ref is not None and account_ref not in known_accounts:
                    errors.append(
                        f"anomaly {item.anomaly_ref!r} references unknown account {account_ref!r}"
                    )
            investment_ref = getattr(item, "investment_ref", None)
            if investment_ref is not None and investment_ref not in known_investments:
                errors.append(
                    f"anomaly {item.anomaly_ref!r} references unknown investment {investment_ref!r}"
                )

        if (
            len(self.investment_redemption_rules)
            + sum(isinstance(item, InvestmentRedemptionAnomalySettings) for item in self.anomalies)
            > 256
        ):
            errors.append(
                "investment redemption rules plus anomalies may contain at most 256 entries"
            )
        if (
            sum(rule.occurrences for rule in self.investment_redemption_rules)
            + sum(isinstance(item, InvestmentRedemptionAnomalySettings) for item in self.anomalies)
            > 10_000
        ):
            errors.append(
                "investment redemption rules plus anomalies may schedule at most 10000 attempts"
            )
        if errors:
            raise ValueError("; ".join(errors))
        return self


ScenarioConfigV1_4 = ScenarioConfigV4

__all__ = [
    "AnomalyConfig",
    "AnomalySettings",
    "AssetSaleAnomalySettings",
    "BonusEventSettings",
    "DependentAddedEventSettings",
    "DependentRemovedEventSettings",
    "DivorceEventSettings",
    "ExpenseLifeEventSettings",
    "InitialLifeStateSettings",
    "InheritanceEventSettings",
    "InvestmentRedemptionAnomalySettings",
    "JobChangeEventSettings",
    "JobLossEventSettings",
    "LargePixTransferAnomalySettings",
    "LifeEventConfig",
    "LifeEventSettings",
    "MarriageEventSettings",
    "MedicalExpenseEventSettings",
    "PromotionEventSettings",
    "PropertyPurchaseEventSettings",
    "RaiseEventSettings",
    "RefundAnomalySettings",
    "ScenarioConfigV1_4",
    "ScenarioConfigV4",
    "SeasonalitySettings",
    "VacationEventSettings",
    "VehiclePurchaseEventSettings",
]
