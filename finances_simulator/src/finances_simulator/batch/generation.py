"""Batch population service with worker-count-independent results."""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from finances_simulator.config import ScenarioConfig, config_sha256
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.validation.v7 import validate_generated_boundary

BATCH_SCHEMA_VERSION = "1.0"
BATCH_SIMULATOR_VERSION = "0.7.0"
MAX_POPULATION_SIZE = 100_000


class BatchGenerationError(RuntimeError):
    """Raised when one population member cannot be generated or validated."""


@dataclass(frozen=True, slots=True)
class GeneratedPopulation:
    """Ordered, fully validated population plus deterministic batch identity."""

    batch_id: str
    config_sha256: str
    seeds: tuple[int, ...]
    months: int
    members: tuple[GeneratedScenario, ...]
    batch_schema_version: str = BATCH_SCHEMA_VERSION
    simulator_version: str = BATCH_SIMULATOR_VERSION

    @property
    def population_size(self) -> int:
        return len(self.members)


def _generate_member(payload: tuple[ScenarioConfig, int, int | None]) -> GeneratedScenario:
    config, seed, months = payload
    return generate_scenario(config, seed=seed, months=months)


def _require_integer(name: str, value: int, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")


def _make_batch_id(fingerprint: str, seeds: tuple[int, ...], months: int) -> str:
    payload = json.dumps(
        {
            "batch_schema_version": BATCH_SCHEMA_VERSION,
            "config_sha256": fingerprint,
            "months": months,
            "seeds": seeds,
            "simulator_version": BATCH_SIMULATOR_VERSION,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"bat_{hashlib.sha256(payload).hexdigest()[:32]}"


def generate_population(
    config: ScenarioConfig,
    *,
    population_size: int,
    seed: int,
    months: int | None = None,
    workers: int | None = None,
) -> GeneratedPopulation:
    """Generate ordered members from consecutive seeds, optionally in parallel.

    Worker scheduling never enters a random stream, identifier, sort key, or manifest.
    Therefore changing ``workers`` changes throughput only.
    """

    _require_integer("population_size", population_size, minimum=1)
    if population_size > MAX_POPULATION_SIZE:
        raise ValueError(
            f"population_size must be less than or equal to {MAX_POPULATION_SIZE}"
        )
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if months is not None:
        _require_integer("months", months, minimum=1)
        if months > 1_200:
            raise ValueError("months must be between 1 and 1200")
    if workers is not None:
        _require_integer("workers", workers, minimum=1)

    seeds = tuple(seed + index for index in range(population_size))
    effective_workers = workers or min(population_size, os.cpu_count() or 1)
    payloads = tuple((config, member_seed, months) for member_seed in seeds)
    try:
        if effective_workers == 1:
            members = tuple(_generate_member(payload) for payload in payloads)
        else:
            with ProcessPoolExecutor(max_workers=effective_workers) as executor:
                # executor.map preserves input order even when tasks finish out of order.
                members = tuple(executor.map(_generate_member, payloads))
        for member in members:
            validate_generated_boundary(member)
    except Exception as error:
        raise BatchGenerationError(f"Unable to generate population: {error}") from error

    run_ids = tuple(member.simulation.run_id for member in members)
    customer_ids = tuple(
        member.simulation.customer_twin.customer_id for member in members
    )
    if len(set(run_ids)) != len(run_ids):
        raise BatchGenerationError("Population contains duplicate run identifiers")
    if len(set(customer_ids)) != len(customer_ids):
        raise BatchGenerationError("Population contains duplicate customer identifiers")

    effective_months = members[0].simulation.months
    if any(member.simulation.months != effective_months for member in members):
        raise BatchGenerationError("Population members use inconsistent month counts")
    fingerprint = config_sha256(config)
    if any(member.simulation.config_sha256 != fingerprint for member in members):
        raise BatchGenerationError("Population members use inconsistent configurations")

    return GeneratedPopulation(
        batch_id=_make_batch_id(fingerprint, seeds, effective_months),
        config_sha256=fingerprint,
        seeds=seeds,
        months=effective_months,
        members=members,
    )


__all__ = [
    "BATCH_SCHEMA_VERSION",
    "BATCH_SIMULATOR_VERSION",
    "MAX_POPULATION_SIZE",
    "BatchGenerationError",
    "GeneratedPopulation",
    "generate_population",
]
