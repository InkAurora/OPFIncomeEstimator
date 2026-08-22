"""Feature outcome wrapper and deterministic rounding helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FeatureOutcome:
    """A computed feature value, or an explicit reason it cannot be computed."""

    value: int | float | None
    missing_reason: str | None = None

    @property
    def is_missing(self) -> bool:
        return self.missing_reason is not None


def present(value: int | float) -> FeatureOutcome:
    return FeatureOutcome(value=value)


def missing(reason: str) -> FeatureOutcome:
    return FeatureOutcome(value=None, missing_reason=reason)


def round_minor(value: float) -> int:
    """Round half away from zero so monetary features never depend on float parity."""

    if value >= 0:
        return math.floor(value + 0.5)
    return -math.floor(-value + 0.5)


def round_ratio(value: float) -> float:
    return round(value, 8)


def round_variance(value: float) -> float:
    return round(value, 6)


def round_basis_points(value: float) -> int:
    return round_minor(value)


def weighted_amount_minor(amount_minor: int, probability_basis_points: int) -> int:
    """Apply a basis-point probability with deterministic integer arithmetic."""

    return (amount_minor * probability_basis_points + 5_000) // 10_000


__all__ = [
    "FeatureOutcome",
    "missing",
    "present",
    "round_basis_points",
    "round_minor",
    "round_ratio",
    "round_variance",
    "weighted_amount_minor",
]
