"""Isolated join between customer-month features and private income targets.

Observed features are built first, by the runtime feature layer, from an estimator input request.
Private targets are projected separately by the simulator. Only after both exist independently are
they joined here, on `customer_id` and `reference_month`. Nothing in this module is importable from
estimator runtime.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from finances_simulator.batch import GeneratedPopulation
from finances_simulator.ground_truth import INCOME_TARGET_VERSION, project_income_targets
from finances_simulator.integration import build_estimator_input_v1_2

from income_estimator.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_FINGERPRINT,
    FEATURE_SET_VERSION,
    build_customer_month_features,
)
from training.datasets import SPLIT_VERSION, customer_partition

CAPACITY_DATASET_VERSION = "synthetic-customer-months-1.0.0"


@dataclass(frozen=True, slots=True)
class CapacityRow:
    """One customer-month training row: observed features plus the private label."""

    customer_id: str
    reference_month: str
    features: dict[str, float | int | None]
    sustainable_monthly_income_minor: int
    realized_income_month_minor: int
    expected_income_month_minor: int
    is_partial_month: bool

    @property
    def log_target(self) -> float:
        return math.log1p(self.sustainable_monthly_income_minor)


def build_capacity_dataset(
    populations: tuple[GeneratedPopulation, ...],
) -> tuple[CapacityRow, ...]:
    """Join only after observed feature extraction has completed."""

    rows: list[CapacityRow] = []
    for population in populations:
        for generated in population.members:
            request = build_estimator_input_v1_2(generated)
            table = build_customer_month_features(request)
            targets = {
                item.month: item for item in project_income_targets(generated.simulation)
            }
            for row in table.rows:
                target = targets.get(row.reference_month)
                if target is None:
                    continue
                rows.append(
                    CapacityRow(
                        customer_id=row.customer_id,
                        reference_month=row.reference_month,
                        features=row.to_mapping(),
                        sustainable_monthly_income_minor=(
                            target.sustainable_monthly_income_minor
                        ),
                        realized_income_month_minor=target.realized_income_month_minor,
                        expected_income_month_minor=target.expected_income_month_minor,
                        is_partial_month=target.is_partial_month,
                    )
                )
    return tuple(sorted(rows, key=lambda item: (item.customer_id, item.reference_month)))


def split_capacity_rows(
    rows: tuple[CapacityRow, ...],
) -> dict[str, tuple[CapacityRow, ...]]:
    """Reuse the frozen customer partition so no customer spans two partitions."""

    result: dict[str, list[CapacityRow]] = {"train": [], "validation": [], "test": []}
    for row in rows:
        result[customer_partition(row.customer_id)].append(row)
    return {name: tuple(items) for name, items in result.items()}


__all__ = [
    "CAPACITY_DATASET_VERSION",
    "FEATURE_NAMES",
    "FEATURE_SCHEMA_FINGERPRINT",
    "FEATURE_SET_VERSION",
    "INCOME_TARGET_VERSION",
    "SPLIT_VERSION",
    "CapacityRow",
    "build_capacity_dataset",
    "split_capacity_rows",
]
