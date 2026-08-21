"""Phase-7 validation at simulator, estimator, and storage boundaries."""

from dataclasses import fields

from pydantic import BaseModel

from finances_simulator.generation import GeneratedScenario

_FORBIDDEN_OBSERVED_FIELDS = {
    "active_income_source_ids",
    "anomaly_id",
    "anomaly_ref",
    "anomaly_type",
    "economic_type",
    "employment_status",
    "income_profile",
    "income_source_id",
    "life_event_id",
    "life_event_ref",
    "life_event_type",
    "true_income_minor",
}


class BoundaryValidationError(ValueError):
    """Raised when data crossing a Phase-7 component boundary is invalid."""


def _validate_record(record: BaseModel, *, expected_schema_version: str) -> None:
    """Re-parse one immutable model instead of trusting its construction path."""

    reparsed = type(record).model_validate(record.model_dump(mode="python"))
    if reparsed != record:
        raise BoundaryValidationError(
            f"{type(record).__name__} changed while being revalidated"
        )
    schema_version = getattr(record, "schema_version", None)
    if schema_version != expected_schema_version:
        raise BoundaryValidationError(
            f"{type(record).__name__} has schema_version {schema_version!r}; "
            f"expected {expected_schema_version!r}"
        )


def validate_generated_boundary(generated: GeneratedScenario) -> None:
    """Validate every projected record and enforce private/observed isolation."""

    expected_schema_version = generated.simulation.profile.contract_schema_version
    expected_customer_id = generated.simulation.customer_twin.customer_id
    private_customer_ids: set[str] = set()
    observed_customer_ids: set[str] = set()

    for bundle_name, bundle in (
        ("private", generated.ground_truth),
        ("observed", generated.observations),
    ):
        for bundle_field in fields(bundle):
            records = getattr(bundle, bundle_field.name)
            if not isinstance(records, tuple):
                raise BoundaryValidationError(
                    f"{bundle_name}.{bundle_field.name} must be an immutable tuple"
                )
            for record in records:
                if not isinstance(record, BaseModel):
                    raise BoundaryValidationError(
                        f"{bundle_name}.{bundle_field.name} contains a non-Pydantic record"
                    )
                _validate_record(record, expected_schema_version=expected_schema_version)
                customer_id = getattr(record, "customer_id", None)
                if customer_id is not None:
                    if bundle_name == "private":
                        private_customer_ids.add(customer_id)
                    else:
                        observed_customer_ids.add(customer_id)
                if bundle_name == "observed":
                    leaked = _FORBIDDEN_OBSERVED_FIELDS.intersection(
                        record.model_dump(mode="python")
                    )
                    if leaked:
                        leaked_fields = ", ".join(sorted(leaked))
                        raise BoundaryValidationError(
                            f"observed.{bundle_field.name} leaks private fields: "
                            f"{leaked_fields}"
                        )

    for label, customer_ids in (
        ("private", private_customer_ids),
        ("observed", observed_customer_ids),
    ):
        unexpected = customer_ids - {expected_customer_id}
        if unexpected:
            raise BoundaryValidationError(
                f"{label} bundle contains records for unexpected customers: "
                f"{sorted(unexpected)}"
            )


__all__ = ["BoundaryValidationError", "validate_generated_boundary"]
