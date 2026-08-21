"""Cross-record invariants for schema-1.5 incomplete observations."""

from __future__ import annotations

from dataclasses import fields

from finances_simulator.domain.accounts import Direction
from finances_simulator.observations.projector_v4 import ObservationBundleV4
from finances_simulator.observations.projector_v5 import ObservationBundleV5
from finances_simulator.validation.invariants import InvariantViolation

_PRIVATE_FIELDS = {
    "economic_type",
    "income_source_id",
    "life_event_id",
    "life_event_ref",
    "life_event_type",
    "anomaly_id",
    "anomaly_ref",
    "anomaly_type",
    "employment_status",
    "active_income_source_ids",
}


def _half_up_ratio(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator if denominator else 0


def validate_observation_degradation(
    complete: ObservationBundleV4,
    degraded: ObservationBundleV5,
) -> None:
    """Validate coverage accounting, artifact lineage, and estimator leakage boundary."""

    records = degraded.transactions
    by_id = {item.transaction_id: item for item in records}
    if len(by_id) != len(records):
        raise InvariantViolation("Observed transaction IDs must be unique.")

    complete_by_id = {item.transaction_id: item for item in complete.transactions}
    originals = tuple(
        item
        for item in records
        if item.duplicate_of_transaction_id is None
        and item.reversal_of_transaction_id is None
    )
    original_by_id = {item.transaction_id: item for item in originals}
    if not set(original_by_id).issubset(complete_by_id):
        raise InvariantViolation("Observed originals must be a subset of complete observations.")

    for record in records:
        duplicate_of = record.duplicate_of_transaction_id
        reversal_of = record.reversal_of_transaction_id
        if duplicate_of is not None:
            original = original_by_id.get(duplicate_of)
            if original is None or (
                record.customer_id != original.customer_id
                or record.account_id != original.account_id
                or record.posted_at != original.posted_at
                or record.direction is not original.direction
                or record.amount_minor != original.amount_minor
                or record.currency != original.currency
                or record.description != original.description
                or record.balance_after_minor != original.balance_after_minor
                or record.observed_at < original.observed_at
            ):
                raise InvariantViolation(
                    f"Duplicate record {record.transaction_id} lacks traceable identical source."
                )
        if reversal_of is not None:
            original = original_by_id.get(reversal_of)
            if original is None or (
                record.customer_id != original.customer_id
                or record.account_id != original.account_id
                or record.posted_at != original.posted_at
                or record.direction is original.direction
                or record.amount_minor != original.amount_minor
                or record.currency != original.currency
                or record.observed_at < original.observed_at
            ):
                raise InvariantViolation(
                    f"Reversal record {record.transaction_id} lacks traceable opposite source."
                )
            expected_direction = (
                Direction.DEBIT
                if original.direction is Direction.CREDIT
                else Direction.CREDIT
            )
            if record.direction is not expected_direction:
                raise InvariantViolation(
                    f"Reversal record {record.transaction_id} has incorrect direction."
                )

    complete_count_by_account: dict[str, int] = {}
    for item in complete.transactions:
        complete_count_by_account[item.account_id] = (
            complete_count_by_account.get(item.account_id, 0) + 1
        )
    original_count_by_account: dict[str, int] = {}
    duplicate_count_by_account: dict[str, int] = {}
    reversal_count_by_account: dict[str, int] = {}
    late_count_by_account: dict[str, int] = {}
    for item in originals:
        original_count_by_account[item.account_id] = (
            original_count_by_account.get(item.account_id, 0) + 1
        )
        late_count_by_account[item.account_id] = late_count_by_account.get(item.account_id, 0) + (
            item.observed_at > item.posted_at
        )
    for item in records:
        duplicate_count_by_account[item.account_id] = duplicate_count_by_account.get(
            item.account_id, 0
        ) + (item.duplicate_of_transaction_id is not None)
        reversal_count_by_account[item.account_id] = reversal_count_by_account.get(
            item.account_id, 0
        ) + (item.reversal_of_transaction_id is not None)

    metric_ids = {item.coverage_id for item in degraded.observation_coverage}
    metric_accounts = {item.account_id for item in degraded.observation_coverage}
    if (
        len(metric_ids) != len(degraded.observation_coverage)
        or len(metric_accounts) != len(degraded.observation_coverage)
    ):
        raise InvariantViolation("Observation coverage rows must be unique by ID and account.")
    if metric_accounts != {item.account_id for item in complete.accounts}:
        raise InvariantViolation("Observation coverage must contain every deposit account.")

    for metric in degraded.observation_coverage:
        eligible = complete_count_by_account.get(metric.account_id, 0)
        observed = original_count_by_account.get(metric.account_id, 0)
        expected_consented = _half_up_ratio(
            eligible * metric.configured_coverage_percent,
            100,
        )
        expected_effective = _half_up_ratio(observed * 10_000, eligible)
        if (
            metric.eligible_record_count != eligible
            or metric.consented_record_count != expected_consented
            or metric.observed_original_record_count != observed
            or metric.missing_record_count != expected_consented - observed
            or metric.late_record_count != late_count_by_account.get(metric.account_id, 0)
            or metric.duplicate_record_count
            != duplicate_count_by_account.get(metric.account_id, 0)
            or metric.reversal_record_count
            != reversal_count_by_account.get(metric.account_id, 0)
            or metric.effective_coverage_basis_points != expected_effective
        ):
            raise InvariantViolation(
                f"Observation coverage {metric.coverage_id} does not reconcile."
            )

    for bundle_field in fields(degraded):
        for record in getattr(degraded, bundle_field.name):
            if not _PRIVATE_FIELDS.isdisjoint(record.model_dump()):
                raise InvariantViolation(
                    f"Observed {bundle_field.name} contains a private field."
                )


__all__ = ["validate_observation_degradation"]
