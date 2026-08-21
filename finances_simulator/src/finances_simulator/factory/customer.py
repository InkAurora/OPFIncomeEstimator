"""Deterministic sampling of configured customer archetypes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, TypeVar

from finances_simulator.config_v3 import CustomerFactorySettings
from finances_simulator.domain.income import (
    MAX_FACTORY_CUSTOMERS,
    CustomerFactoryMember,
    SampledIncomeSource,
)
from finances_simulator.simulation.primitives import DeterministicRandom, make_rng_stream

MAX_FACTORY_SAMPLE_COUNT = 100_000
_WEIGHT_TOTAL_BASIS_POINTS = 10_000
_STREAM_PREFIX = "customer-factory-v1"


class _Weighted(Protocol):
    weight_basis_points: int


_WeightedItem = TypeVar("_WeightedItem", bound=_Weighted)


def _require_non_negative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _weighted_choice(
    values: Sequence[_WeightedItem],
    *,
    rng: DeterministicRandom,
    stable_key: Callable[[_WeightedItem], str],
) -> _WeightedItem:
    """Select from exact basis-point intervals in semantic-key order."""

    ordered = tuple(sorted(values, key=stable_key))
    if not ordered:
        raise ValueError("weighted choices must not be empty")
    total = sum(value.weight_basis_points for value in ordered)
    if total != _WEIGHT_TOTAL_BASIS_POINTS:
        raise ValueError("weighted choices must sum to 10000 basis points")

    ticket = rng.randint(0, _WEIGHT_TOTAL_BASIS_POINTS - 1)
    cumulative = 0
    for value in ordered:
        cumulative += value.weight_basis_points
        if ticket < cumulative:
            return value

    raise RuntimeError("weighted choice did not cover its validated ticket")


class CustomerFactory:
    """Sample addressable customers from isolated deterministic streams."""

    __slots__ = ("_seed", "_settings")

    def __init__(self, settings: CustomerFactorySettings, *, seed: int) -> None:
        if not isinstance(settings, CustomerFactorySettings):
            raise TypeError("settings must be CustomerFactorySettings")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("seed must be an integer")
        self._settings = settings
        self._seed = seed

    def _rng(self, customer_index: int, label: str) -> DeterministicRandom:
        return make_rng_stream(
            self._seed,
            f"{_STREAM_PREFIX}:customer:{customer_index}:{label}",
        )

    def sample_one(self, index: int = 0) -> CustomerFactoryMember:
        """Return one sample whose result depends only on seed, config, and index."""

        _require_non_negative_int("index", index)
        if index >= MAX_FACTORY_CUSTOMERS:
            raise ValueError(f"index must be less than {MAX_FACTORY_CUSTOMERS}")

        profile = _weighted_choice(
            self._settings.income_profiles,
            rng=self._rng(index, "income-profile"),
            stable_key=lambda item: item.income_profile.value,
        )
        bundle = _weighted_choice(
            profile.source_bundles,
            rng=self._rng(index, f"source-bundle:{profile.income_profile.value}"),
            stable_key=lambda item: item.source_bundle_ref,
        )
        behavior = _weighted_choice(
            self._settings.behavior_profiles,
            rng=self._rng(index, "behavior-profile"),
            stable_key=lambda item: item.behavior_profile.value,
        )
        wealth = _weighted_choice(
            self._settings.wealth_profiles,
            rng=self._rng(index, "wealth-band"),
            stable_key=lambda item: item.wealth_band.value,
        )

        income_sources = tuple(
            SampledIncomeSource(
                source_ref=source.source_ref,
                income_kind=source.income_kind,
                payer=source.payer,
                description=source.description,
                destination_account_ref=source.destination_account_ref,
                base_amount_minor=(
                    source.amount_distribution.minimum_minor
                    + source.amount_distribution.step_minor
                    * self._rng(
                        index,
                        (
                            f"income-source:{profile.income_profile.value}:"
                            f"{bundle.source_bundle_ref}:{source.source_ref}:base-amount"
                        ),
                    ).randint(
                        0,
                        (
                            source.amount_distribution.maximum_minor
                            - source.amount_distribution.minimum_minor
                        )
                        // source.amount_distribution.step_minor,
                    )
                ),
                day_of_month=source.day_of_month,
                frequency=source.frequency,
                start_month_index=source.start_month_index,
                occurrences=source.occurrences,
                payment_probability_basis_points=(source.payment_probability_basis_points),
                volatility_basis_points=source.volatility_basis_points,
                seasonality_basis_points=tuple(source.seasonality_basis_points),
            )
            for source in sorted(bundle.sources, key=lambda item: item.source_ref)
        )

        return CustomerFactoryMember(
            customer_index=index,
            income_profile=profile.income_profile,
            source_bundle_ref=bundle.source_bundle_ref,
            behavior_profile=behavior.behavior_profile,
            wealth_band=wealth.wealth_band,
            spending_multiplier_basis_points=behavior.spending_multiplier_basis_points,
            saving_multiplier_basis_points=behavior.saving_multiplier_basis_points,
            deposit_balance_multiplier_basis_points=(
                wealth.deposit_balance_multiplier_basis_points
            ),
            investment_balance_multiplier_basis_points=(
                wealth.investment_balance_multiplier_basis_points
            ),
            income_sources=income_sources,
        )

    def sample(self, count: int) -> tuple[CustomerFactoryMember, ...]:
        """Materialize an index-stable sample, bounded for in-memory use."""

        _require_non_negative_int("count", count)
        if count > MAX_FACTORY_SAMPLE_COUNT:
            raise ValueError(f"count must be less than or equal to {MAX_FACTORY_SAMPLE_COUNT}")
        return tuple(self.sample_one(index) for index in range(count))


__all__ = ["MAX_FACTORY_SAMPLE_COUNT", "CustomerFactory"]
