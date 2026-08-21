"""Deterministic primitives shared by simulator components."""

from __future__ import annotations

import calendar
import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TypeVar
from uuid import NAMESPACE_URL, UUID, uuid5

SIMULATOR_VERSION = "0.7.0"
CONTRACT_SCHEMA_VERSION = "1.5"
RNG_ALGORITHM_VERSION = "sha256-counter-v1"

_Choice = TypeVar("_Choice")


@dataclass(frozen=True, slots=True)
class VersionProfile:
    """Versions governing one deterministic simulation path."""

    simulator_version: str
    contract_schema_version: str
    rng_algorithm: str = RNG_ALGORITHM_VERSION


V0_PROFILE = VersionProfile(simulator_version="0.1.0", contract_schema_version="1.0")
V1_PROFILE = VersionProfile(simulator_version="0.2.0", contract_schema_version="1.1")
V2_PROFILE = VersionProfile(simulator_version="0.3.0", contract_schema_version="1.2")
V3_PROFILE = VersionProfile(simulator_version="0.4.0", contract_schema_version="1.3")
V4_PROFILE = VersionProfile(simulator_version="0.5.0", contract_schema_version="1.4")
V5_PROFILE = VersionProfile(simulator_version="0.6.0", contract_schema_version="1.5")

_SIMULATION_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://opf-income-estimator.local/finances-simulator",
)
_KIND_PREFIXES = {
    "customer": "cus",
    "account": "acc",
    "income_source": "inc",
    "event": "evt",
    "entry": "ent",
    "card": "crd",
    "card_transaction": "ctx",
    "invoice": "inv",
    "invoice_item": "itm",
    "credit_limit": "lim",
    "transfer_group": "trf",
    "loan": "loa",
    "loan_payment": "lpy",
    "loan_balance": "lnb",
    "investment": "ivx",
    "investment_transaction": "itx",
    "investment_balance": "ivb",
    "balance_sheet": "nwt",
    "life_event": "lfe",
    "anomaly": "ano",
}


def _require_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")


def _normalized_config_hash(config_sha256: str) -> str:
    if not isinstance(config_sha256, str):
        raise TypeError("config_sha256 must be a string")

    normalized = config_sha256.strip().lower()
    if not normalized:
        raise ValueError("config_sha256 must not be empty")
    return normalized


def _normalized_kind(kind: str) -> str:
    if not isinstance(kind, str):
        raise TypeError("kind must be a string")

    ascii_kind = unicodedata.normalize("NFKD", kind).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^a-z0-9]+", "_", ascii_kind.lower()).strip("_")
    if not normalized:
        raise ValueError("kind must contain at least one letter or digit")
    return normalized


def _kind_prefix(kind: str) -> str:
    known_prefix = _KIND_PREFIXES.get(kind)
    if known_prefix is not None:
        return known_prefix

    compact = kind.replace("_", "")
    return compact[:3].ljust(3, "x")


def simulation_namespace(
    config_sha256: str,
    seed: int,
    *,
    simulator_version: str = SIMULATOR_VERSION,
) -> UUID:
    """Return stable UUID namespace for one configuration and seed."""

    _require_int("seed", seed)
    config_hash = _normalized_config_hash(config_sha256)
    name = f"simulator={simulator_version};config={config_hash};seed={seed}"
    return uuid5(_SIMULATION_NAMESPACE, name)


def make_run_id(
    config_sha256: str,
    seed: int,
    months: int,
    *,
    simulator_version: str = SIMULATOR_VERSION,
) -> str:
    """Return deterministic run identifier for complete simulation inputs."""

    _require_int("months", months)
    if months < 0:
        raise ValueError("months must be non-negative")

    namespace = simulation_namespace(
        config_sha256,
        seed,
        simulator_version=simulator_version,
    )
    identifier = uuid5(namespace, f"run;months={months}")
    return f"run_{identifier.hex}"


def deterministic_id(namespace: UUID, kind: str, key: str | int) -> str:
    """Build a stable, type-prefixed identifier inside ``namespace``."""

    if not isinstance(namespace, UUID):
        raise TypeError("namespace must be a UUID")
    if not isinstance(key, str | int) or isinstance(key, bool):
        raise TypeError("key must be a string or integer")

    normalized_kind = _normalized_kind(kind)
    identifier = uuid5(namespace, f"{normalized_kind}:{key}")
    return f"{_kind_prefix(normalized_kind)}_{identifier.hex}"


def month_start(start: date, index: int) -> date:
    """Return first day of month ``index`` months on or after ``start``."""

    if not isinstance(start, date):
        raise TypeError("start must be a date")
    _require_int("index", index)
    if index < 0:
        raise ValueError("index must be non-negative")

    absolute_month = start.year * 12 + start.month - 1 + index
    year, zero_based_month = divmod(absolute_month, 12)
    return date(year, zero_based_month + 1, 1)


def month_end(start: date) -> date:
    """Return final calendar day of month containing ``start``."""

    if not isinstance(start, date):
        raise TypeError("start must be a date")
    final_day = calendar.monthrange(start.year, start.month)[1]
    return date(start.year, start.month, final_day)


def scheduled_date(month: date, day: int) -> date:
    """Return scheduled day in ``month``, clamped to its valid day range."""

    if not isinstance(month, date):
        raise TypeError("month must be a date")
    _require_int("day", day)

    final_day = calendar.monthrange(month.year, month.month)[1]
    clamped_day = min(max(day, 1), final_day)
    return date(month.year, month.month, clamped_day)


class DeterministicRandom:
    """Small versioned PRNG whose output does not depend on Python internals."""

    __slots__ = ("_counter", "_seed_material")

    def __init__(self, seed: int) -> None:
        _require_int("seed", seed)
        self._seed_material = str(seed).encode("ascii")
        self._counter = 0

    def _draw_bytes(self, byte_count: int) -> bytes:
        output = bytearray()
        while len(output) < byte_count:
            counter_bytes = self._counter.to_bytes(16, "big")
            output.extend(
                hashlib.sha256(
                    RNG_ALGORITHM_VERSION.encode("ascii")
                    + b"\0"
                    + self._seed_material
                    + b"\0"
                    + counter_bytes
                ).digest()
            )
            self._counter += 1
        return bytes(output[:byte_count])

    def _randbelow(self, upper_bound: int) -> int:
        _require_int("upper_bound", upper_bound)
        if upper_bound <= 0:
            raise ValueError("upper_bound must be positive")

        byte_count = max(1, (upper_bound.bit_length() + 7) // 8)
        value_space = 1 << (byte_count * 8)
        acceptance_limit = value_space - (value_space % upper_bound)
        while True:
            candidate = int.from_bytes(self._draw_bytes(byte_count), "big")
            if candidate < acceptance_limit:
                return candidate % upper_bound

    def randint(self, lower_bound: int, upper_bound: int) -> int:
        """Return one unbiased integer from an inclusive range."""

        _require_int("lower_bound", lower_bound)
        _require_int("upper_bound", upper_bound)
        if upper_bound < lower_bound:
            raise ValueError("upper_bound must be greater than or equal to lower_bound")
        return lower_bound + self._randbelow(upper_bound - lower_bound + 1)

    def choice(self, values: Sequence[_Choice]) -> _Choice:
        """Return one uniformly selected item from a non-empty sequence."""

        if not values:
            raise IndexError("cannot choose from an empty sequence")
        return values[self._randbelow(len(values))]


def make_rng(seed: int) -> DeterministicRandom:
    """Create an isolated, cross-runtime deterministic random generator."""

    return DeterministicRandom(seed)


def make_rng_stream(seed: int, label: str) -> DeterministicRandom:
    """Create a labeled deterministic stream isolated from other simulator domains."""

    _require_int("seed", seed)
    if not isinstance(label, str) or not label:
        raise ValueError("label must be a non-empty string")
    derived_seed = int.from_bytes(
        hashlib.sha256(f"{RNG_ALGORITHM_VERSION}:{seed}:{label}".encode()).digest(),
        "big",
    )
    return DeterministicRandom(derived_seed)


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "DeterministicRandom",
    "RNG_ALGORITHM_VERSION",
    "SIMULATOR_VERSION",
    "V0_PROFILE",
    "V1_PROFILE",
    "V2_PROFILE",
    "V3_PROFILE",
    "V4_PROFILE",
    "V5_PROFILE",
    "VersionProfile",
    "deterministic_id",
    "make_rng",
    "make_rng_stream",
    "make_run_id",
    "month_end",
    "month_start",
    "scheduled_date",
    "simulation_namespace",
]
