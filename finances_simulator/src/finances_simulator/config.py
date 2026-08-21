"""Validated scenario configuration and stable configuration hashing."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class ConfigurationError(ValueError):
    """Raised when a scenario configuration cannot be read or validated."""


class ConfigModel(BaseModel):
    """Base model for strict simulator configuration contracts."""

    model_config = ConfigDict(extra="forbid")


class ScenarioSettings(ConfigModel):
    """Settings controlling the scenario timeline."""

    name: NonEmptyString
    start_date: date
    default_months: int = Field(gt=0)

    @field_validator("start_date")
    @classmethod
    def start_date_must_be_first_day(cls, value: date) -> date:
        if value.day != 1:
            raise ValueError("start_date must be the first day of a month")
        return value


class CustomerSettings(ConfigModel):
    """Initial customer and checking-account settings."""

    currency: CurrencyCode
    opening_balance_minor: int = Field(ge=0)
    institution_id: NonEmptyString
    institution_name: NonEmptyString
    account_label: NonEmptyString


class SalaryRule(ConfigModel):
    """Monthly salary-generation rule."""

    amount_minor: int = Field(gt=0)
    day_of_month: int = Field(ge=1, le=31)
    payer: NonEmptyString
    description: NonEmptyString


class FixedExpenseRule(ConfigModel):
    """Monthly fixed-expense generation rule."""

    rule_id: NonEmptyString
    category: NonEmptyString
    amount_minor: int = Field(gt=0)
    day_of_month: int = Field(ge=1, le=31)
    payee: NonEmptyString
    description: NonEmptyString


class Merchant(ConfigModel):
    """Merchant details used for a variable expense."""

    entity: NonEmptyString
    description: NonEmptyString


class VariableExpenseRule(ConfigModel):
    """Bounds for monthly variable-expense generation."""

    count_min: int = Field(ge=0)
    count_max: int = Field(ge=0)
    amount_min_minor: int = Field(gt=0)
    amount_max_minor: int = Field(gt=0)
    day_min: int = Field(ge=1, le=28)
    day_max: int = Field(ge=1, le=28)
    merchants: list[Merchant] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ranges(self) -> VariableExpenseRule:
        errors: list[str] = []
        if self.count_max < self.count_min:
            errors.append("count_max must be greater than or equal to count_min")
        if self.amount_max_minor < self.amount_min_minor:
            errors.append("amount_max_minor must be greater than or equal to amount_min_minor")
        if self.day_max < self.day_min:
            errors.append("day_max must be greater than or equal to day_min")
        if errors:
            raise ValueError("; ".join(errors))
        return self


class ScenarioConfigV0(ConfigModel):
    """Frozen schema 1.0 configuration for the legacy salaried scenario."""

    schema_version: Literal["1.0"]
    scenario: ScenarioSettings
    customer: CustomerSettings
    salary: SalaryRule
    fixed_expenses: list[FixedExpenseRule] = Field(min_length=5, max_length=5)
    variable_expenses: VariableExpenseRule

    @model_validator(mode="after")
    def fixed_expense_rule_ids_must_be_unique(self) -> ScenarioConfigV0:
        rule_ids = [rule.rule_id for rule in self.fixed_expenses]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("fixed_expenses rule_id values must be unique")
        return self


if TYPE_CHECKING:
    from finances_simulator.config_v1 import ScenarioConfigV1
    from finances_simulator.config_v2 import ScenarioConfigV2
    from finances_simulator.config_v3 import ScenarioConfigV3
    from finances_simulator.config_v4 import ScenarioConfigV4
    from finances_simulator.config_v5 import ScenarioConfigV5

    type ScenarioConfig = (
        ScenarioConfigV0
        | ScenarioConfigV1
        | ScenarioConfigV2
        | ScenarioConfigV3
        | ScenarioConfigV4
        | ScenarioConfigV5
    )
else:
    # Preserve the schema-1.0 public class API, including ``model_validate``.
    ScenarioConfig = ScenarioConfigV0


def _validation_error_message(path: Path, error: ValidationError) -> str:
    details: list[str] = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "configuration"
        details.append(f"  - {location}: {item['msg']}")
    return f"Invalid scenario configuration '{path}':\n" + "\n".join(details)


def load_scenario_config(path: Path) -> ScenarioConfig:
    """Load and validate one YAML scenario file.

    Raises:
        ConfigurationError: If the file cannot be read, parsed, or validated.
    """

    try:
        raw_yaml = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(
            f"Unable to read scenario configuration '{path}': {error}"
        ) from error

    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as error:
        location = ""
        problem_mark = getattr(error, "problem_mark", None)
        if problem_mark is not None:
            location = f" at line {problem_mark.line + 1}, column {problem_mark.column + 1}"
        problem = getattr(error, "problem", None) or str(error)
        raise ConfigurationError(f"Invalid YAML in '{path}'{location}: {problem}") from error

    if data is None:
        raise ConfigurationError(f"Scenario configuration '{path}' is empty")

    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Invalid scenario configuration '{path}': top level must be a mapping"
        )

    schema_version = data.get("schema_version")
    if schema_version == "1.0":
        config_model = ScenarioConfigV0
    elif schema_version == "1.1":
        from finances_simulator.config_v1 import ScenarioConfigV1

        config_model = ScenarioConfigV1
    elif schema_version == "1.2":
        from finances_simulator.config_v2 import ScenarioConfigV2

        config_model = ScenarioConfigV2
    elif schema_version == "1.3":
        from finances_simulator.config_v3 import ScenarioConfigV3

        config_model = ScenarioConfigV3
    elif schema_version == "1.4":
        from finances_simulator.config_v4 import ScenarioConfigV4

        config_model = ScenarioConfigV4
    elif schema_version == "1.5":
        from finances_simulator.config_v5 import ScenarioConfigV5

        config_model = ScenarioConfigV5
    else:
        raise ConfigurationError(
            f"Invalid scenario configuration '{path}': unsupported schema_version "
            f"{schema_version!r}; expected '1.0', '1.1', '1.2', '1.3', '1.4', or '1.5'"
        )

    try:
        return config_model.model_validate(data)
    except ValidationError as error:
        raise ConfigurationError(_validation_error_message(path, error)) from error


def canonical_config_json(config: ScenarioConfig) -> str:
    """Return stable, compact JSON for a validated scenario configuration."""

    return json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def config_sha256(config: ScenarioConfig) -> str:
    """Return the SHA-256 digest of the canonical configuration JSON."""

    canonical_bytes = canonical_config_json(config).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()
