"""Validated configuration contract for Phase-6 incomplete observations."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from finances_simulator.config import ConfigModel, NonEmptyString
from finances_simulator.config_v1 import ReferenceId
from finances_simulator.config_v4 import ScenarioConfigV4

CoveragePercent = Literal[100, 70, 40]


class InstitutionConsentSettings(ConfigModel):
    """Standard history coverage for one configured institution."""

    institution_ref: ReferenceId
    coverage_percent: CoveragePercent


class AccountConsentSettings(ConfigModel):
    """Account-level coverage override."""

    account_ref: ReferenceId
    coverage_percent: CoveragePercent


class ConsentCoverageSettings(ConfigModel):
    """Default, institution, and account consent coverage policy."""

    default_coverage_percent: CoveragePercent = 100
    institutions: list[InstitutionConsentSettings] = Field(default_factory=list, max_length=32)
    accounts: list[AccountConsentSettings] = Field(default_factory=list, max_length=32)


class InstitutionDescriptionSettings(ConfigModel):
    """Provider presentation added to estimator-visible transaction descriptions."""

    institution_ref: ReferenceId
    description_prefix: NonEmptyString
    reversal_prefix: NonEmptyString = "REVERSAL"


class ObservationDegradationSettings(ConfigModel):
    """Deterministic consent and deposit-transaction degradation controls."""

    consent: ConsentCoverageSettings = Field(default_factory=ConsentCoverageSettings)
    institution_descriptions: list[InstitutionDescriptionSettings] = Field(
        default_factory=list,
        max_length=32,
    )
    missing_record_basis_points: int = Field(default=0, ge=0, le=10_000)
    late_record_basis_points: int = Field(default=0, ge=0, le=10_000)
    duplicate_record_basis_points: int = Field(default=0, ge=0, le=10_000)
    reversal_record_basis_points: int = Field(default=0, ge=0, le=10_000)
    maximum_late_days: int = Field(default=7, ge=1, le=365)
    maximum_reversal_delay_days: int = Field(default=30, ge=1, le=365)


class ScenarioConfigV5(ScenarioConfigV4):
    """Complete validated configuration for contract schema 1.5."""

    schema_version: Literal["1.5"]
    observation_degradation: ObservationDegradationSettings = Field(
        default_factory=ObservationDegradationSettings
    )

    @model_validator(mode="after")
    def validate_observation_references(self) -> Self:
        errors: list[str] = []
        settings = self.observation_degradation
        institution_refs = [item.institution_ref for item in settings.consent.institutions]
        account_refs = [item.account_ref for item in settings.consent.accounts]
        description_refs = [
            item.institution_ref for item in settings.institution_descriptions
        ]

        def require_unique(values: list[str], label: str) -> None:
            if len(values) != len(set(values)):
                errors.append(f"{label} values must be unique")

        require_unique(institution_refs, "consent.institutions.institution_ref")
        require_unique(account_refs, "consent.accounts.account_ref")
        require_unique(description_refs, "institution_descriptions.institution_ref")

        known_institutions = {item.institution_ref for item in self.institutions}
        known_accounts = {item.account_ref for item in self.accounts}
        for institution_ref in (*institution_refs, *description_refs):
            if institution_ref not in known_institutions:
                errors.append(
                    f"observation degradation references unknown institution {institution_ref!r}"
                )
        for account_ref in account_refs:
            if account_ref not in known_accounts:
                errors.append(
                    f"observation degradation references unknown account {account_ref!r}"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self


ScenarioConfigV1_5 = ScenarioConfigV5

__all__ = [
    "AccountConsentSettings",
    "ConsentCoverageSettings",
    "CoveragePercent",
    "InstitutionConsentSettings",
    "InstitutionDescriptionSettings",
    "ObservationDegradationSettings",
    "ScenarioConfigV1_5",
    "ScenarioConfigV5",
]
