"""Phase-4 income-diversity and customer-factory tests."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from finances_simulator.cli import main
from finances_simulator.config import ScenarioConfig, load_scenario_config
from finances_simulator.config_v3 import ScenarioConfigV3
from finances_simulator.contracts import observed_v3
from finances_simulator.domain.accounts import Direction
from finances_simulator.domain.customer import CustomerTwinV3
from finances_simulator.domain.events import EconomicType
from finances_simulator.domain.income import (
    BehaviorProfile,
    IncomeFrequency,
    IncomeKind,
    IncomeProfile,
    WealthBand,
)
from finances_simulator.factory import MAX_FACTORY_SAMPLE_COUNT, CustomerFactory
from finances_simulator.factory.customer import _weighted_choice
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.outputs import write_run
from finances_simulator.outputs import writer as writer_module
from finances_simulator.simulation.primitives import V3_PROFILE
from finances_simulator.simulation.v3 import (
    _scaled_config,
    realize_income_amount,
    round_half_up_ratio,
    scale_minor_amount,
)
from finances_simulator.validation import InvariantViolation, validate_income_simulation

PROFILE_WEIGHTS = {
    IncomeProfile.SALARIED: 2_000,
    IncomeProfile.SELF_EMPLOYED: 1_600,
    IncomeProfile.BUSINESS_OWNER: 1_400,
    IncomeProfile.RETIRED: 1_200,
    IncomeProfile.INVESTOR: 1_000,
    IncomeProfile.MIXED: 1_800,
    IncomeProfile.UNEMPLOYED: 1_000,
}
BEHAVIOR_WEIGHTS = {
    BehaviorProfile.LOW_SPENDING: 2_500,
    BehaviorProfile.BALANCED: 5_000,
    BehaviorProfile.HIGH_SPENDING: 2_500,
}
WEALTH_WEIGHTS = {
    WealthBand.LOW: 3_000,
    WealthBand.MIDDLE: 5_000,
    WealthBand.HIGH: 2_000,
}


def _source(
    source_ref: str,
    income_kind: IncomeKind,
    *,
    minimum_minor: int = 100_000,
    maximum_minor: int = 300_000,
    step_minor: int = 10_000,
) -> dict[str, Any]:
    return {
        "source_ref": source_ref,
        "income_kind": income_kind.value,
        "payer": f"PAYER {source_ref.upper()}",
        "description": f"PAYMENT {source_ref.upper()}",
        "destination_account_ref": "checking",
        "amount_distribution": {
            "minimum_minor": minimum_minor,
            "maximum_minor": maximum_minor,
            "step_minor": step_minor,
        },
        "day_of_month": 15,
        "frequency": IncomeFrequency.MONTHLY.value,
        "start_month_index": 0,
        "occurrences": 24,
        "payment_probability_basis_points": 9_000,
        "volatility_basis_points": 2_500,
        "seasonality_basis_points": [10_000] * 12,
    }


def _bundle(
    bundle_ref: str,
    weight: int,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "source_bundle_ref": bundle_ref,
        "weight_basis_points": weight,
        "sources": sources,
    }


def _profile_payloads() -> list[dict[str, Any]]:
    return [
        {
            "income_profile": IncomeProfile.SALARIED.value,
            "weight_basis_points": PROFILE_WEIGHTS[IncomeProfile.SALARIED],
            "source_bundles": [
                _bundle(
                    "salary_standard",
                    3_000,
                    [_source("salary_standard", IncomeKind.SALARY)],
                ),
                _bundle(
                    "salary_plus_other",
                    7_000,
                    [
                        _source("salary_primary", IncomeKind.SALARY),
                        _source("salary_other", IncomeKind.OTHER),
                    ],
                ),
            ],
        },
        {
            "income_profile": IncomeProfile.SELF_EMPLOYED.value,
            "weight_basis_points": PROFILE_WEIGHTS[IncomeProfile.SELF_EMPLOYED],
            "source_bundles": [
                _bundle(
                    "self_employed_standard",
                    10_000,
                    [_source("self_employment", IncomeKind.SELF_EMPLOYMENT)],
                )
            ],
        },
        {
            "income_profile": IncomeProfile.BUSINESS_OWNER.value,
            "weight_basis_points": PROFILE_WEIGHTS[IncomeProfile.BUSINESS_OWNER],
            "source_bundles": [
                _bundle(
                    "business_standard",
                    10_000,
                    [_source("business_profit", IncomeKind.BUSINESS_PROFIT)],
                )
            ],
        },
        {
            "income_profile": IncomeProfile.RETIRED.value,
            "weight_basis_points": PROFILE_WEIGHTS[IncomeProfile.RETIRED],
            "source_bundles": [
                _bundle(
                    "retired_standard",
                    10_000,
                    [_source("pension", IncomeKind.PENSION)],
                )
            ],
        },
        {
            "income_profile": IncomeProfile.INVESTOR.value,
            "weight_basis_points": PROFILE_WEIGHTS[IncomeProfile.INVESTOR],
            "source_bundles": [
                _bundle(
                    "investor_standard",
                    10_000,
                    [
                        _source(
                            "investment_distribution",
                            IncomeKind.INVESTMENT_DISTRIBUTION,
                        )
                    ],
                )
            ],
        },
        {
            "income_profile": IncomeProfile.MIXED.value,
            "weight_basis_points": PROFILE_WEIGHTS[IncomeProfile.MIXED],
            "source_bundles": [
                _bundle(
                    "mixed_standard",
                    10_000,
                    [
                        _source("mixed_salary", IncomeKind.SALARY),
                        _source("mixed_self_employment", IncomeKind.SELF_EMPLOYMENT),
                    ],
                )
            ],
        },
        {
            "income_profile": IncomeProfile.UNEMPLOYED.value,
            "weight_basis_points": PROFILE_WEIGHTS[IncomeProfile.UNEMPLOYED],
            "source_bundles": [_bundle("unemployed_none", 10_000, [])],
        },
    ]


def _v3_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.3",
        "scenario": {
            "name": "income_diversity_test",
            "start_date": "2024-01-01",
            "default_months": 24,
        },
        "customer": {
            "currency": "BRL",
            "primary_account_ref": "checking",
        },
        "customer_factory": {
            "income_profiles": _profile_payloads(),
            "behavior_profiles": [
                {
                    "behavior_profile": BehaviorProfile.LOW_SPENDING.value,
                    "weight_basis_points": BEHAVIOR_WEIGHTS[BehaviorProfile.LOW_SPENDING],
                    "spending_multiplier_basis_points": 7_500,
                    "saving_multiplier_basis_points": 12_500,
                },
                {
                    "behavior_profile": BehaviorProfile.BALANCED.value,
                    "weight_basis_points": BEHAVIOR_WEIGHTS[BehaviorProfile.BALANCED],
                    "spending_multiplier_basis_points": 10_000,
                    "saving_multiplier_basis_points": 10_000,
                },
                {
                    "behavior_profile": BehaviorProfile.HIGH_SPENDING.value,
                    "weight_basis_points": BEHAVIOR_WEIGHTS[BehaviorProfile.HIGH_SPENDING],
                    "spending_multiplier_basis_points": 15_000,
                    "saving_multiplier_basis_points": 5_000,
                },
            ],
            "wealth_profiles": [
                {
                    "wealth_band": WealthBand.LOW.value,
                    "weight_basis_points": WEALTH_WEIGHTS[WealthBand.LOW],
                    "deposit_balance_multiplier_basis_points": 5_000,
                    "investment_balance_multiplier_basis_points": 2_500,
                },
                {
                    "wealth_band": WealthBand.MIDDLE.value,
                    "weight_basis_points": WEALTH_WEIGHTS[WealthBand.MIDDLE],
                    "deposit_balance_multiplier_basis_points": 10_000,
                    "investment_balance_multiplier_basis_points": 10_000,
                },
                {
                    "wealth_band": WealthBand.HIGH.value,
                    "weight_basis_points": WEALTH_WEIGHTS[WealthBand.HIGH],
                    "deposit_balance_multiplier_basis_points": 25_000,
                    "investment_balance_multiplier_basis_points": 30_000,
                },
            ],
        },
        "institutions": [
            {
                "institution_ref": "bank",
                "institution_id": "fictional-bank",
                "institution_name": "Fictional Bank",
            }
        ],
        "accounts": [
            {
                "account_ref": "checking",
                "institution_ref": "bank",
                "account_label": "Primary checking",
                "account_type": "CHECKING",
                "opening_balance_minor": 100_000,
            }
        ],
        "fixed_expenses": [],
        "variable_expenses": {
            "count_min": 0,
            "count_max": 0,
            "amount_min_minor": 1,
            "amount_max_minor": 1,
            "day_min": 1,
            "day_max": 1,
            "merchants": [{"entity": "unused", "description": "UNUSED"}],
            "source_account_ref": "checking",
        },
        "own_transfers": [],
        "credit_cards": [],
        "card_purchase_rules": [],
        "loans": [],
        "investments": [],
        "investment_contribution_rules": [],
        "investment_redemption_rules": [],
    }


def _forced_profile_payload(
    income_profile: IncomeProfile,
    *,
    source_mutation: dict[str, Any] | None = None,
    spending_multiplier: int = 10_000,
    saving_multiplier: int = 10_000,
    deposit_multiplier: int = 10_000,
    investment_multiplier: int = 10_000,
) -> dict[str, Any]:
    payload = _v3_payload()
    selected = next(
        item
        for item in payload["customer_factory"]["income_profiles"]
        if item["income_profile"] == income_profile.value
    )
    selected["weight_basis_points"] = 10_000
    selected["source_bundles"] = [selected["source_bundles"][0]]
    selected["source_bundles"][0]["weight_basis_points"] = 10_000
    for source in selected["source_bundles"][0]["sources"]:
        source["amount_distribution"] = {
            "minimum_minor": 100_000,
            "maximum_minor": 100_000,
            "step_minor": 1,
        }
        source["payment_probability_basis_points"] = 10_000
        source["volatility_basis_points"] = 0
        source["seasonality_basis_points"] = [10_000] * 12
        source["occurrences"] = 24
        if source_mutation is not None:
            source.update(deepcopy(source_mutation))
    payload["customer_factory"]["income_profiles"] = [selected]
    payload["customer_factory"]["behavior_profiles"] = [
        {
            "behavior_profile": BehaviorProfile.BALANCED.value,
            "weight_basis_points": 10_000,
            "spending_multiplier_basis_points": spending_multiplier,
            "saving_multiplier_basis_points": saving_multiplier,
        }
    ]
    payload["customer_factory"]["wealth_profiles"] = [
        {
            "wealth_band": WealthBand.MIDDLE.value,
            "weight_basis_points": 10_000,
            "deposit_balance_multiplier_basis_points": deposit_multiplier,
            "investment_balance_multiplier_basis_points": investment_multiplier,
        }
    ]
    return payload


def _with_scalable_products(
    *,
    spending_multiplier: int,
    saving_multiplier: int,
    deposit_multiplier: int,
    investment_multiplier: int,
) -> ScenarioConfigV3:
    payload = _forced_profile_payload(
        IncomeProfile.SALARIED,
        spending_multiplier=spending_multiplier,
        saving_multiplier=saving_multiplier,
        deposit_multiplier=deposit_multiplier,
        investment_multiplier=investment_multiplier,
    )
    payload["accounts"][0]["opening_balance_minor"] = 1
    payload["accounts"].append(
        {
            "account_ref": "savings",
            "institution_ref": "bank",
            "account_label": "Savings",
            "account_type": "SAVINGS",
            "opening_balance_minor": 2,
        }
    )
    payload["fixed_expenses"] = [
        {
            "rule_id": "fixed",
            "category": "test",
            "amount_minor": 1,
            "day_of_month": 10,
            "payee": "Fixed payee",
            "description": "FIXED EXPENSE",
            "source_account_ref": "checking",
        }
    ]
    payload["variable_expenses"].update(
        {
            "count_min": 1,
            "count_max": 1,
            "amount_min_minor": 1,
            "amount_max_minor": 3,
        }
    )
    payload["own_transfers"] = [
        {
            "rule_id": "save",
            "source_account_ref": "checking",
            "destination_account_ref": "savings",
            "amount_minor": 1,
            "day_of_month": 5,
            "outgoing_description": "SAVE OUT",
            "incoming_description": "SAVE IN",
        }
    ]
    payload["credit_cards"] = [
        {
            "card_ref": "card",
            "institution_ref": "bank",
            "card_label": "Card",
            "credit_limit_minor": 100_000,
            "statement_close_day": 20,
            "payment_due_day": 5,
            "payment_account_ref": "checking",
            "payment_description": "CARD PAYMENT",
            "payment_policy": "FULL_AUTOPAY",
            "utilization_policy": {
                "maximum_basis_points": 10_000,
                "on_exceed": "DECLINE",
            },
        }
    ]
    payload["card_purchase_rules"] = [
        {
            "rule_id": "card_purchase",
            "card_ref": "card",
            "merchant": "Merchant",
            "description": "CARD PURCHASE",
            "amount_minor": 3,
            "day_of_month": 8,
            "start_month_index": 0,
            "interval_months": 1,
            "occurrences": 1,
            "installment_count": 3,
        }
    ]
    payload["investments"] = [
        {
            "investment_ref": "investment",
            "institution_ref": "bank",
            "investment_label": "Investment",
            "investment_type": "FIXED_INCOME",
            "opening_balance_minor": 1,
            "monthly_return_basis_points": 0,
            "return_description": "RETURN",
        }
    ]
    payload["investment_contribution_rules"] = [
        {
            "rule_id": "contribution",
            "investment_ref": "investment",
            "account_ref": "savings",
            "amount_minor": 1,
            "day_of_month": 7,
            "start_month_index": 0,
            "interval_months": 1,
            "occurrences": 1,
            "description": "CONTRIBUTION",
        }
    ]
    payload["investment_redemption_rules"] = [
        {
            "rule_id": "redemption",
            "investment_ref": "investment",
            "account_ref": "checking",
            "amount_minor": 1,
            "day_of_month": 9,
            "start_month_index": 0,
            "interval_months": 1,
            "occurrences": 1,
            "description": "REDEMPTION",
        }
    ]
    return ScenarioConfigV3.model_validate(payload)


@pytest.fixture(scope="module")
def v3_config() -> ScenarioConfigV3:
    return ScenarioConfigV3.model_validate(_v3_payload())


@pytest.fixture(scope="module")
def factory(v3_config: ScenarioConfigV3) -> CustomerFactory:
    return CustomerFactory(v3_config.customer_factory, seed=42)


@pytest.fixture(scope="module")
def factory_population(factory: CustomerFactory) -> tuple[Any, ...]:
    return factory.sample(20_000)


@pytest.fixture(scope="module")
def income_diverse_config(project_root: Path) -> ScenarioConfigV3:
    loaded = load_scenario_config(project_root / "configs" / "scenarios" / "income_diverse.yaml")
    assert isinstance(loaded, ScenarioConfigV3)
    return loaded


@pytest.fixture(scope="module")
def generated_income_diverse(income_diverse_config: ScenarioConfigV3) -> GeneratedScenario:
    return generate_scenario(income_diverse_config, seed=42, months=24)


def _assert_distribution(
    counts: Counter[Any],
    weights: dict[Any, int],
    population_size: int,
) -> None:
    assert set(counts) == set(weights)
    for value, weight in weights.items():
        probability = weight / 10_000
        expected = population_size * probability
        standard_deviation = math.sqrt(population_size * probability * (1 - probability))
        assert abs(counts[value] - expected) <= max(10, 7 * standard_deviation)


def test_bundled_income_diverse_scenario_selects_expected_seed_42_customer(
    income_diverse_config: ScenarioConfigV3,
    generated_income_diverse: GeneratedScenario,
) -> None:
    run = generated_income_diverse.simulation
    twin = run.customer_twin

    assert income_diverse_config.schema_version == "1.3"
    assert run.profile == V3_PROFILE
    assert isinstance(twin, CustomerTwinV3)
    assert twin.income_profile is IncomeProfile.MIXED
    assert twin.behavior_profile is BehaviorProfile.BALANCED
    assert twin.wealth_band is WealthBand.HIGH
    assert [(source.source_ref, source.base_amount_minor) for source in run.income_sources] == [
        ("consulting_receipts", 330_000),
        ("fund_distributions", 240_000),
    ]
    assert [account.opening_balance_minor for account in twin.accounts] == [625_000, 250_000]
    assert [investment.opening_balance_minor for investment in run.investments] == [200_000]
    assert sum(event.economic_type is EconomicType.INCOME for event in run.events) == 30
    assert (
        sum(
            event.amount_minor for event in run.events if event.economic_type is EconomicType.INCOME
        )
        == 9_343_380
    )
    assert len(run.events) == 699
    assert len(run.ledger_entries) == 673
    assert generated_income_diverse.ground_truth.balance_sheets[-1].net_worth_minor == (-2_602_515)


def test_v3_configuration_supports_all_income_profiles_and_strict_nesting(
    v3_config: ScenarioConfigV3,
) -> None:
    assert {item.income_profile for item in v3_config.customer_factory.income_profiles} == set(
        IncomeProfile
    )
    assert {item.behavior_profile for item in v3_config.customer_factory.behavior_profiles} == set(
        BehaviorProfile
    )
    assert {item.wealth_band for item in v3_config.customer_factory.wealth_profiles} == set(
        WealthBand
    )

    payload = _v3_payload()
    payload["customer_factory"]["income_profiles"][0]["source_bundles"][0]["unknown"] = 1
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScenarioConfigV3.model_validate(payload)


@pytest.mark.parametrize(
    ("frequency", "interval"),
    (
        (IncomeFrequency.MONTHLY, 1),
        (IncomeFrequency.EVERY_TWO_MONTHS, 2),
        (IncomeFrequency.QUARTERLY, 3),
        (IncomeFrequency.SEMIANNUALLY, 6),
        (IncomeFrequency.ANNUALLY, 12),
    ),
)
def test_income_frequency_has_exact_calendar_month_interval(
    frequency: IncomeFrequency,
    interval: int,
) -> None:
    assert frequency.interval_months == interval


@pytest.mark.parametrize(
    "case",
    (
        "profile_weights",
        "duplicate_profile",
        "bundle_weights",
        "duplicate_bundle",
        "duplicate_source",
        "missing_required_kind",
        "mixed_one_kind",
        "unemployed_source",
        "inverted_amount_range",
        "unaligned_amount_range",
        "short_seasonality",
        "unknown_destination",
        "too_many_sources",
    ),
)
def test_v3_configuration_rejects_invalid_distributions_and_income_sources(
    case: str,
) -> None:
    payload = _v3_payload()
    profiles = payload["customer_factory"]["income_profiles"]
    salary_bundles = profiles[0]["source_bundles"]
    salary_source = salary_bundles[0]["sources"][0]

    if case == "profile_weights":
        profiles[0]["weight_basis_points"] -= 1
    elif case == "duplicate_profile":
        profiles[1]["income_profile"] = profiles[0]["income_profile"]
    elif case == "bundle_weights":
        salary_bundles[0]["weight_basis_points"] -= 1
    elif case == "duplicate_bundle":
        profiles[1]["source_bundles"][0]["source_bundle_ref"] = salary_bundles[0][
            "source_bundle_ref"
        ]
    elif case == "duplicate_source":
        salary_bundles[0]["sources"].append(deepcopy(salary_source))
    elif case == "missing_required_kind":
        salary_source["income_kind"] = IncomeKind.OTHER.value
    elif case == "mixed_one_kind":
        profiles[5]["source_bundles"][0]["sources"][1]["income_kind"] = IncomeKind.SALARY.value
    elif case == "unemployed_source":
        profiles[6]["source_bundles"][0]["sources"].append(
            _source("invalid_unemployed", IncomeKind.OTHER)
        )
    elif case == "inverted_amount_range":
        salary_source["amount_distribution"].update(
            {"minimum_minor": 200, "maximum_minor": 100, "step_minor": 10}
        )
    elif case == "unaligned_amount_range":
        salary_source["amount_distribution"].update(
            {"minimum_minor": 100, "maximum_minor": 111, "step_minor": 10}
        )
    elif case == "short_seasonality":
        salary_source["seasonality_basis_points"] = [10_000] * 11
    elif case == "unknown_destination":
        salary_source["destination_account_ref"] = "missing"
    elif case == "too_many_sources":
        salary_bundles[0]["sources"] = [
            _source(f"salary_{index}", IncomeKind.SALARY) for index in range(9)
        ]
    else:  # pragma: no cover - parametrization guard
        raise AssertionError(case)

    with pytest.raises(ValidationError):
        ScenarioConfigV3.model_validate(payload)


@dataclass(frozen=True)
class _WeightedItem:
    name: str
    weight_basis_points: int


class _FixedTicket:
    def __init__(self, ticket: int) -> None:
        self.ticket = ticket

    def randint(self, lower_bound: int, upper_bound: int) -> int:
        assert (lower_bound, upper_bound) == (0, 9_999)
        return self.ticket


@pytest.mark.parametrize(
    ("ticket", "expected"),
    ((0, "alpha"), (2_999, "alpha"), (3_000, "beta"), (9_999, "beta")),
)
def test_weighted_choice_uses_exact_half_open_basis_point_intervals(
    ticket: int,
    expected: str,
) -> None:
    values = (_WeightedItem("beta", 7_000), _WeightedItem("alpha", 3_000))
    selected = _weighted_choice(
        values,
        rng=_FixedTicket(ticket),  # type: ignore[arg-type]
        stable_key=lambda item: item.name,
    )
    assert selected.name == expected


def test_factory_population_approximates_marginal_and_conditional_distributions(
    factory_population: tuple[Any, ...],
) -> None:
    population = factory_population

    _assert_distribution(
        Counter(member.income_profile for member in population),
        PROFILE_WEIGHTS,
        len(population),
    )
    _assert_distribution(
        Counter(member.behavior_profile for member in population),
        BEHAVIOR_WEIGHTS,
        len(population),
    )
    _assert_distribution(
        Counter(member.wealth_band for member in population),
        WEALTH_WEIGHTS,
        len(population),
    )

    salaried = [member for member in population if member.income_profile is IncomeProfile.SALARIED]
    _assert_distribution(
        Counter(member.source_bundle_ref for member in salaried),
        {"salary_standard": 3_000, "salary_plus_other": 7_000},
        len(salaried),
    )

    joint_counts = Counter(
        (member.income_profile, member.behavior_profile, member.wealth_band)
        for member in population
    )
    expected_combinations = {
        (income_profile, behavior_profile, wealth_band)
        for income_profile in PROFILE_WEIGHTS
        for behavior_profile in BEHAVIOR_WEIGHTS
        for wealth_band in WEALTH_WEIGHTS
    }
    assert set(joint_counts) == expected_combinations
    for combination in expected_combinations:
        income_profile, behavior_profile, wealth_band = combination
        probability = (
            PROFILE_WEIGHTS[income_profile]
            * BEHAVIOR_WEIGHTS[behavior_profile]
            * WEALTH_WEIGHTS[wealth_band]
            / 10_000**3
        )
        expected = len(population) * probability
        standard_deviation = math.sqrt(len(population) * probability * (1 - probability))
        assert abs(joint_counts[combination] - expected) <= max(
            10,
            5 * standard_deviation,
        )


def test_factory_allows_unusual_independent_combinations_and_zero_income(
    factory_population: tuple[Any, ...],
) -> None:
    population = factory_population

    assert any(
        member.income_profile is IncomeProfile.UNEMPLOYED
        and member.wealth_band is WealthBand.HIGH
        and member.behavior_profile is BehaviorProfile.HIGH_SPENDING
        for member in population
    )
    unemployed = [
        member for member in population if member.income_profile is IncomeProfile.UNEMPLOYED
    ]
    assert unemployed
    assert all(member.income_sources == () for member in unemployed)
    assert {
        (member.income_profile, member.wealth_band, member.behavior_profile)
        for member in population
    } >= {
        (IncomeProfile.UNEMPLOYED, WealthBand.HIGH, BehaviorProfile.HIGH_SPENDING),
        (IncomeProfile.SALARIED, WealthBand.LOW, BehaviorProfile.LOW_SPENDING),
        (IncomeProfile.INVESTOR, WealthBand.LOW, BehaviorProfile.HIGH_SPENDING),
    }


def test_factory_sources_follow_profile_rules_and_uniform_amount_grid(
    factory_population: tuple[Any, ...],
) -> None:
    population = factory_population[:5_000]
    required_kind = {
        IncomeProfile.SALARIED: IncomeKind.SALARY,
        IncomeProfile.SELF_EMPLOYED: IncomeKind.SELF_EMPLOYMENT,
        IncomeProfile.BUSINESS_OWNER: IncomeKind.BUSINESS_PROFIT,
        IncomeProfile.RETIRED: IncomeKind.PENSION,
        IncomeProfile.INVESTOR: IncomeKind.INVESTMENT_DISTRIBUTION,
    }

    sampled_amounts: set[int] = set()
    for member in population:
        kinds = {source.income_kind for source in member.income_sources}
        if member.income_profile in required_kind:
            assert required_kind[member.income_profile] in kinds
        elif member.income_profile is IncomeProfile.MIXED:
            assert len(kinds) >= 2
        else:
            assert member.income_sources == ()
        for source in member.income_sources:
            assert 100_000 <= source.base_amount_minor <= 300_000
            assert (source.base_amount_minor - 100_000) % 10_000 == 0
            sampled_amounts.add(source.base_amount_minor)

    assert len(sampled_amounts) >= 15


def test_factory_base_amount_bins_approximate_stepped_uniform_distribution() -> None:
    payload = _forced_profile_payload(
        IncomeProfile.SALARIED,
        source_mutation={
            "amount_distribution": {
                "minimum_minor": 100_000,
                "maximum_minor": 300_000,
                "step_minor": 10_000,
            }
        },
    )
    config = ScenarioConfigV3.model_validate(payload)
    population_size = 21_000
    population = CustomerFactory(config.customer_factory, seed=42).sample(population_size)
    counts = Counter(member.income_sources[0].base_amount_minor for member in population)
    expected_bins = set(range(100_000, 300_001, 10_000))
    assert set(counts) == expected_bins

    probability = 1 / len(expected_bins)
    expected = population_size * probability
    standard_deviation = math.sqrt(population_size * probability * (1 - probability))
    for amount_minor in expected_bins:
        assert abs(counts[amount_minor] - expected) <= 5 * standard_deviation


def test_factory_is_addressable_prefix_stable_and_seeded(factory: CustomerFactory) -> None:
    small = factory.sample(32)
    large = factory.sample(256)

    assert factory.sample(0) == ()
    assert small == large[: len(small)]
    assert large == tuple(factory.sample_one(index) for index in range(len(large)))
    assert tuple(factory.sample_one(index) for index in reversed(range(32))) == tuple(
        reversed(small)
    )

    other_seed = CustomerFactory(factory._settings, seed=43)  # type: ignore[attr-defined]
    assert other_seed.sample(256) != large


def test_factory_is_invariant_to_weighted_option_and_source_order(
    v3_config: ScenarioConfigV3,
) -> None:
    payload = _v3_payload()
    customer_factory = payload["customer_factory"]
    customer_factory["income_profiles"].reverse()
    customer_factory["behavior_profiles"].reverse()
    customer_factory["wealth_profiles"].reverse()
    for profile in customer_factory["income_profiles"]:
        profile["source_bundles"].reverse()
        for bundle in profile["source_bundles"]:
            bundle["sources"].reverse()
    reordered = ScenarioConfigV3.model_validate(payload)

    expected = CustomerFactory(v3_config.customer_factory, seed=42).sample(1_000)
    actual = CustomerFactory(reordered.customer_factory, seed=42).sample(1_000)
    assert actual == expected


def _without_behavior(member: Any) -> tuple[Any, ...]:
    return (
        member.customer_index,
        member.income_profile,
        member.source_bundle_ref,
        member.wealth_band,
        member.deposit_balance_multiplier_basis_points,
        member.investment_balance_multiplier_basis_points,
        member.income_sources,
    )


def _without_wealth(member: Any) -> tuple[Any, ...]:
    return (
        member.customer_index,
        member.income_profile,
        member.source_bundle_ref,
        member.behavior_profile,
        member.spending_multiplier_basis_points,
        member.saving_multiplier_basis_points,
        member.income_sources,
    )


def test_behavior_and_wealth_axes_use_independent_rng_streams(
    v3_config: ScenarioConfigV3,
) -> None:
    base = CustomerFactory(v3_config.customer_factory, seed=42).sample(2_000)

    behavior_payload = _v3_payload()
    behavior_weights = (8_000, 1_000, 1_000)
    for item, weight in zip(
        behavior_payload["customer_factory"]["behavior_profiles"],
        behavior_weights,
        strict=True,
    ):
        item["weight_basis_points"] = weight
    behavior_config = ScenarioConfigV3.model_validate(behavior_payload)
    changed_behavior = CustomerFactory(behavior_config.customer_factory, seed=42).sample(2_000)
    assert [_without_behavior(item) for item in changed_behavior] == [
        _without_behavior(item) for item in base
    ]
    assert [item.behavior_profile for item in changed_behavior] != [
        item.behavior_profile for item in base
    ]

    wealth_payload = _v3_payload()
    wealth_weights = (1_000, 1_000, 8_000)
    for item, weight in zip(
        wealth_payload["customer_factory"]["wealth_profiles"],
        wealth_weights,
        strict=True,
    ):
        item["weight_basis_points"] = weight
    wealth_config = ScenarioConfigV3.model_validate(wealth_payload)
    changed_wealth = CustomerFactory(wealth_config.customer_factory, seed=42).sample(2_000)
    assert [_without_wealth(item) for item in changed_wealth] == [
        _without_wealth(item) for item in base
    ]
    assert [item.wealth_band for item in changed_wealth] != [item.wealth_band for item in base]


def test_adding_source_does_not_perturb_existing_source_draws(
    v3_config: ScenarioConfigV3,
) -> None:
    base_factory = CustomerFactory(v3_config.customer_factory, seed=42)
    payload = _v3_payload()
    payload["customer_factory"]["income_profiles"][0]["source_bundles"][0]["sources"].append(
        _source("salary_new_other", IncomeKind.OTHER)
    )
    extended_config = ScenarioConfigV3.model_validate(payload)
    extended_factory = CustomerFactory(extended_config.customer_factory, seed=42)

    compared = 0
    for index in range(2_000):
        before = base_factory.sample_one(index)
        after = extended_factory.sample_one(index)
        if before.source_bundle_ref != "salary_standard":
            continue
        before_by_ref = {source.source_ref: source for source in before.income_sources}
        after_by_ref = {source.source_ref: source for source in after.income_sources}
        assert after_by_ref["salary_standard"] == before_by_ref["salary_standard"]
        compared += 1
    assert compared >= 100


@pytest.mark.parametrize("invalid", (True, 1.5, "1", None))
def test_factory_rejects_non_integer_count_and_index(
    invalid: Any,
    factory: CustomerFactory,
) -> None:
    with pytest.raises(TypeError, match="count must be an integer"):
        factory.sample(invalid)
    with pytest.raises(TypeError, match="index must be an integer"):
        factory.sample_one(invalid)


def test_factory_rejects_negative_and_excessive_work(factory: CustomerFactory) -> None:
    with pytest.raises(ValueError, match="count must be non-negative"):
        factory.sample(-1)
    with pytest.raises(ValueError, match="less than or equal"):
        factory.sample(MAX_FACTORY_SAMPLE_COUNT + 1)
    with pytest.raises(ValueError, match="index must be non-negative"):
        factory.sample_one(-1)
    with pytest.raises(ValueError, match="index must be less than 1000000"):
        factory.sample_one(1_000_000)


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    ((0, 2, 0), (1, 3, 0), (2, 3, 1), (1, 2, 1), (3, 2, 2)),
)
def test_nonnegative_ratio_rounding_is_exact_half_up(
    numerator: int,
    denominator: int,
    expected: int,
) -> None:
    assert round_half_up_ratio(numerator, denominator) == expected


def test_income_and_dimension_scaling_use_one_rounding_step() -> None:
    assert scale_minor_amount(1, 4_999) == 0
    assert scale_minor_amount(1, 5_000) == 1
    assert realize_income_amount(1, 4_999, 0) == 0
    assert realize_income_amount(1, 5_000, 0) == 1

    # One-step result is 0.5. Rounding base*seasonality first would lose it.
    assert realize_income_amount(1, 2_500, 10_000) == 1


def test_behavior_and_wealth_scaling_reconciles_rounding_and_product_minima() -> None:
    config = _with_scalable_products(
        spending_multiplier=5_000,
        saving_multiplier=5_000,
        deposit_multiplier=5_000,
        investment_multiplier=5_000,
    )
    member = CustomerFactory(config.customer_factory, seed=42).sample_one()
    scaled = _scaled_config(config, member)

    assert [item.opening_balance_minor for item in scaled.accounts] == [1, 1]
    assert [item.amount_minor for item in scaled.fixed_expenses] == [1]
    assert scaled.variable_expenses.amount_min_minor == 1
    assert scaled.variable_expenses.amount_max_minor == 2
    assert [item.amount_minor for item in scaled.own_transfers] == [1]
    assert [item.amount_minor for item in scaled.card_purchase_rules] == [3]
    assert [item.opening_balance_minor for item in scaled.investments] == [1]
    assert [item.amount_minor for item in scaled.investment_contribution_rules] == [1]
    assert [item.amount_minor for item in scaled.investment_redemption_rules] == [1]


def test_zero_behavior_and_wealth_multipliers_omit_scaled_rules_and_attempts() -> None:
    config = _with_scalable_products(
        spending_multiplier=0,
        saving_multiplier=0,
        deposit_multiplier=0,
        investment_multiplier=0,
    )
    member = CustomerFactory(config.customer_factory, seed=42).sample_one()
    scaled = _scaled_config(config, member)

    assert [item.opening_balance_minor for item in scaled.accounts] == [0, 0]
    assert scaled.fixed_expenses == []
    assert scaled.variable_expenses.count_min == 0
    assert scaled.variable_expenses.count_max == 0
    assert scaled.own_transfers == []
    assert scaled.card_purchase_rules == []
    assert [item.opening_balance_minor for item in scaled.investments] == [0]
    assert scaled.investment_contribution_rules == []
    assert [item.amount_minor for item in scaled.investment_redemption_rules] == [1]


@pytest.mark.parametrize("income_profile", tuple(IncomeProfile))
def test_every_income_profile_generates_consistent_hidden_and_monthly_truth(
    income_profile: IncomeProfile,
) -> None:
    config = ScenarioConfigV3.model_validate(_forced_profile_payload(income_profile))
    generated = generate_scenario(config, seed=42, months=2)
    run = generated.simulation

    assert run.profile == V3_PROFILE
    assert isinstance(run.customer_twin, CustomerTwinV3)
    assert run.customer_twin.income_profile is income_profile
    assert generated.ground_truth.customers[0].income_profile is income_profile
    assert generated.ground_truth.customers[0].income_source_ids == tuple(
        source.income_source_id for source in run.income_sources
    )
    assert len(generated.ground_truth.income_sources) == len(run.income_sources)

    income_events = [event for event in run.events if event.economic_type is EconomicType.INCOME]
    expected_source_count = (
        0
        if income_profile is IncomeProfile.UNEMPLOYED
        else (2 if income_profile is IncomeProfile.MIXED else 1)
    )
    assert len(run.income_sources) == expected_source_count
    assert len(income_events) == expected_source_count * 2
    assert [month.true_income_minor for month in generated.ground_truth.customer_months] == [
        expected_source_count * 100_000,
        expected_source_count * 100_000,
    ]
    assert [month.income_event_count for month in generated.ground_truth.customer_months] == [
        expected_source_count,
        expected_source_count,
    ]

    entries_by_event: dict[str, list[Any]] = defaultdict(list)
    for entry in run.ledger_entries:
        entries_by_event[entry.event_id].append(entry)
    known_source_ids = {source.income_source_id for source in run.income_sources}
    for event in income_events:
        assert event.income_source_id in known_source_ids
        assert len(entries_by_event[event.event_id]) == 1
        entry = entries_by_event[event.event_id][0]
        assert entry.direction is Direction.CREDIT
        assert entry.account_id == event.destination_entity
        assert entry.amount_minor == event.amount_minor


def test_frequency_seasonality_and_month_end_clamping_follow_calendar_months() -> None:
    seasonality = [10_000] * 12
    seasonality[1] = 5_000
    seasonality[3] = 15_000
    seasonality[5] = 20_000
    seasonality[7] = 2_500
    payload = _forced_profile_payload(
        IncomeProfile.SALARIED,
        source_mutation={
            "amount_distribution": {
                "minimum_minor": 100,
                "maximum_minor": 100,
                "step_minor": 1,
            },
            "day_of_month": 31,
            "frequency": IncomeFrequency.EVERY_TWO_MONTHS.value,
            "start_month_index": 1,
            "occurrences": 4,
            "seasonality_basis_points": seasonality,
        },
    )
    generated = generate_scenario(ScenarioConfigV3.model_validate(payload), seed=42, months=10)
    events = [
        event for event in generated.simulation.events if event.economic_type is EconomicType.INCOME
    ]

    assert [event.occurred_at for event in events] == [
        date(2024, 2, 29),
        date(2024, 4, 30),
        date(2024, 6, 30),
        date(2024, 8, 31),
    ]
    assert [event.amount_minor for event in events] == [50, 150, 200, 25]
    assert [month.true_income_minor for month in generated.ground_truth.customer_months] == [
        0,
        50,
        0,
        150,
        0,
        200,
        0,
        25,
        0,
        0,
    ]


def test_zero_payment_probability_supports_zero_income_without_fake_events() -> None:
    payload = _forced_profile_payload(
        IncomeProfile.SALARIED,
        source_mutation={"payment_probability_basis_points": 0},
    )
    generated = generate_scenario(ScenarioConfigV3.model_validate(payload), seed=42, months=24)

    assert generated.simulation.income_sources
    assert not any(
        event.economic_type is EconomicType.INCOME for event in generated.simulation.events
    )
    assert all(month.true_income_minor == 0 for month in generated.ground_truth.customer_months)
    assert all(month.income_event_count == 0 for month in generated.ground_truth.customer_months)


def _income_signature(generated: GeneratedScenario, source_ref: str) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            event.occurred_at,
            event.amount_minor,
            event.description,
        )
        for event in generated.simulation.events
        if event.economic_type is EconomicType.INCOME and event.metadata["source_ref"] == source_ref
    )


def test_income_sources_and_run_prefixes_use_isolated_addressable_streams() -> None:
    payload = _forced_profile_payload(IncomeProfile.MIXED)
    for source in payload["customer_factory"]["income_profiles"][0]["source_bundles"][0]["sources"]:
        source["amount_distribution"] = {
            "minimum_minor": 100_000,
            "maximum_minor": 100_000,
            "step_minor": 1,
        }
        source["volatility_basis_points"] = 5_000
        source["payment_probability_basis_points"] = 8_000
    config = ScenarioConfigV3.model_validate(payload)
    short = generate_scenario(config, seed=42, months=12)
    long = generate_scenario(config, seed=42, months=24)

    assert short.simulation.factory_member == long.simulation.factory_member
    for source_ref in ("mixed_salary", "mixed_self_employment"):
        assert _income_signature(short, source_ref) == tuple(
            item
            for item in _income_signature(long, source_ref)
            if item[0] <= short.simulation.end_date
        )

    changed_payload = deepcopy(payload)
    changed_sources = changed_payload["customer_factory"]["income_profiles"][0]["source_bundles"][
        0
    ]["sources"]
    changed_sources[0]["volatility_basis_points"] = 10_000
    changed_sources[0]["payment_probability_basis_points"] = 1_000
    changed = generate_scenario(
        ScenarioConfigV3.model_validate(changed_payload),
        seed=42,
        months=24,
    )
    assert _income_signature(changed, "mixed_self_employment") == _income_signature(
        long,
        "mixed_self_employment",
    )
    assert _income_signature(changed, "mixed_salary") != _income_signature(
        long,
        "mixed_salary",
    )


def test_maximum_volatility_produces_bounded_highly_variable_income() -> None:
    payload = _forced_profile_payload(
        IncomeProfile.SALARIED,
        source_mutation={
            "amount_distribution": {
                "minimum_minor": 10_000,
                "maximum_minor": 10_000,
                "step_minor": 1,
            },
            "volatility_basis_points": 10_000,
            "occurrences": 1_200,
        },
    )
    generated = generate_scenario(ScenarioConfigV3.model_validate(payload), seed=42, months=1_200)
    amounts = [
        event.amount_minor
        for event in generated.simulation.events
        if event.economic_type is EconomicType.INCOME
    ]

    assert amounts
    assert min(amounts) >= 1
    assert max(amounts) <= 20_000
    assert min(amounts) < 2_500
    assert max(amounts) > 17_500
    assert len(set(amounts)) > 500


def test_v3_account_monthly_truth_and_balance_sheets_reconcile_for_many_seeds(
    v3_config: ScenarioConfigV3,
) -> None:
    sampled_profiles: set[IncomeProfile] = set()
    for seed in range(20):
        generated = generate_scenario(v3_config, seed=seed, months=24)
        run = generated.simulation
        sampled_profiles.add(run.customer_twin.income_profile)

        entries_by_account: dict[str, list[Any]] = defaultdict(list)
        for entry in run.ledger_entries:
            entries_by_account[entry.account_id].append(entry)
        for account in run.customer_twin.accounts:
            balance = account.opening_balance_minor
            for entry in entries_by_account[account.account_id]:
                balance += entry.amount_minor * (1 if entry.direction is Direction.CREDIT else -1)
                assert entry.balance_after_minor == balance
            observed_closing = next(
                item.balance_minor
                for item in reversed(generated.observations.balances)
                if item.account_id == account.account_id
            )
            assert observed_closing == balance

        income_by_month: Counter[str] = Counter()
        income_count_by_month: Counter[str] = Counter()
        for event in run.events:
            if event.economic_type is EconomicType.INCOME:
                month = event.occurred_at.strftime("%Y-%m")
                income_by_month[month] += event.amount_minor
                income_count_by_month[month] += 1
        for month in generated.ground_truth.customer_months:
            assert month.true_income_minor == income_by_month[month.month]
            assert month.income_event_count == income_count_by_month[month.month]

        for month, sheet in zip(
            generated.ground_truth.customer_months,
            generated.ground_truth.balance_sheets,
            strict=True,
        ):
            assert sheet.total_assets_minor == (
                sheet.total_deposit_balance_minor + sheet.total_investment_balance_minor
            )
            assert sheet.total_liabilities_minor == (
                sheet.total_card_outstanding_minor + sheet.total_loan_principal_minor
            )
            assert sheet.net_worth_minor == (
                sheet.total_assets_minor - sheet.total_liabilities_minor
            )
            assert sheet.net_worth_minor - sheet.opening_net_worth_minor == (
                month.true_income_minor
                - month.true_expenses_minor
                - month.loan_interest_paid_minor
                + month.investment_return_minor
            )

    assert sampled_profiles == set(IncomeProfile)


def test_investor_income_and_portfolio_return_keep_distinct_economic_types() -> None:
    payload = _forced_profile_payload(IncomeProfile.INVESTOR)
    payload["investments"] = [
        {
            "investment_ref": "portfolio",
            "institution_ref": "bank",
            "investment_label": "Portfolio",
            "investment_type": "FIXED_INCOME",
            "opening_balance_minor": 10_000,
            "monthly_return_basis_points": 100,
            "return_description": "PORTFOLIO RETURN",
        }
    ]
    generated = generate_scenario(ScenarioConfigV3.model_validate(payload), seed=42, months=2)
    income_events = [
        event for event in generated.simulation.events if event.economic_type is EconomicType.INCOME
    ]
    return_events = [
        event
        for event in generated.simulation.events
        if event.economic_type is EconomicType.INVESTMENT_RETURN
    ]

    assert [event.amount_minor for event in income_events] == [100_000, 100_000]
    assert [event.amount_minor for event in return_events] == [100, 101]
    assert all(event.income_source_id is not None for event in income_events)
    assert all(event.income_source_id is None for event in return_events)
    assert [month.true_income_minor for month in generated.ground_truth.customer_months] == [
        100_000,
        100_000,
    ]
    assert [month.investment_return_minor for month in generated.ground_truth.customer_months] == [
        100,
        101,
    ]


def _loan_payload(principal_minor: int) -> dict[str, Any]:
    return {
        "loan_ref": "credit_confounder",
        "institution_ref": "bank",
        "loan_label": "Credit confounder",
        "loan_type": "PERSONAL",
        "principal_minor": principal_minor,
        "annual_interest_basis_points": 1_200,
        "term_months": 1,
        "amortization_system": "CONSTANT_PRINCIPAL",
        "disbursement_account_ref": "checking",
        "disbursement_month_index": 0,
        "disbursement_day_of_month": 20,
        "payment_account_ref": "checking",
        "payment_day_of_month": 20,
        "disbursement_description": "CREDIT RECEIPT",
        "payment_description": "CREDIT PAYMENT",
        "payment_policy": "FULL_AUTOPAY",
    }


def test_total_observed_credits_are_not_a_perfect_true_income_formula() -> None:
    higher_income_payload = _forced_profile_payload(
        IncomeProfile.SALARIED,
        source_mutation={
            "amount_distribution": {
                "minimum_minor": 150_000,
                "maximum_minor": 150_000,
                "step_minor": 1,
            }
        },
    )
    lower_income_payload = _forced_profile_payload(IncomeProfile.SALARIED)
    lower_income_payload["loans"] = [_loan_payload(50_000)]

    higher = generate_scenario(
        ScenarioConfigV3.model_validate(higher_income_payload),
        seed=42,
        months=1,
    )
    lower = generate_scenario(
        ScenarioConfigV3.model_validate(lower_income_payload),
        seed=42,
        months=1,
    )

    def observed_credits(generated: GeneratedScenario) -> int:
        return sum(
            transaction.amount_minor
            for transaction in generated.observations.transactions
            if transaction.direction is Direction.CREDIT
        )

    assert observed_credits(higher) == observed_credits(lower) == 150_000
    assert higher.ground_truth.customer_months[0].true_income_minor == 150_000
    assert lower.ground_truth.customer_months[0].true_income_minor == 100_000
    assert any(
        transaction.economic_type is EconomicType.LOAN_DISBURSEMENT
        for transaction in lower.ground_truth.transactions
    )


def test_bundled_credit_descriptions_do_not_perfectly_label_true_income(
    generated_income_diverse: GeneratedScenario,
) -> None:
    truth_by_entry_id = {
        transaction.entry_id: transaction
        for transaction in generated_income_diverse.ground_truth.transactions
    }
    income_descriptions = {
        transaction.description
        for transaction in generated_income_diverse.observations.transactions
        if transaction.direction is Direction.CREDIT
        and truth_by_entry_id[transaction.transaction_id].economic_type is EconomicType.INCOME
    }
    non_income_credit_descriptions = {
        transaction.description
        for transaction in generated_income_diverse.observations.transactions
        if transaction.direction is Direction.CREDIT
        and truth_by_entry_id[transaction.transaction_id].economic_type is not EconomicType.INCOME
    }

    assert income_descriptions & non_income_credit_descriptions >= {
        "CONSULTING SERVICE RECEIPT",
        "FUND CASH DISTRIBUTION",
    }


FORBIDDEN_OBSERVED_NAMES = {
    "behavior_profile",
    "caused_by_event_id",
    "deposit_balance_multiplier_basis_points",
    "destination_entity",
    "economic_type",
    "event_id",
    "income_kind",
    "income_profile",
    "income_source_id",
    "investment_balance_multiplier_basis_points",
    "metadata",
    "payment_probability_basis_points",
    "saving_multiplier_basis_points",
    "seasonality_basis_points",
    "source_bundle_ref",
    "source_entity",
    "spending_multiplier_basis_points",
    "true_income_minor",
    "volatility_basis_points",
    "wealth_band",
}


def _all_mapping_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for nested in value.values() for key in _all_mapping_keys(nested)}
    if isinstance(value, list):
        return {key for nested in value for key in _all_mapping_keys(nested)}
    return set()


def _assert_no_factory_truth(field_names: set[str]) -> None:
    assert not field_names.intersection(FORBIDDEN_OBSERVED_NAMES)
    assert not any(name.startswith("true_") for name in field_names)


def test_all_v3_observation_contracts_exclude_factory_and_income_truth() -> None:
    for name in observed_v3.__all__:
        model = getattr(observed_v3, name)
        if isinstance(model, type) and issubclass(model, BaseModel):
            _assert_no_factory_truth(set(model.model_fields))


def _output_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _output_digest(root: Path) -> str:
    """Hash a tree with unambiguous path and payload delimiters."""

    digest = hashlib.sha256()
    for relative_path, payload in _output_tree(root).items():
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


@pytest.mark.parametrize(
    ("scenario", "run_id", "digest"),
    (
        (
            "salaried_basic",
            "run_9e93a533dbe45c3eb8475801a1ad7783",
            "2fe5c4815c6eab6287d550558dad1bf6016a10daa8ca5edc9674fb3d3d469d37",
        ),
        (
            "salaried_multi_account_card",
            "run_ebaac2f476ea54a1b7cb260739bc49f9",
            "8f4f93f5638c435732fb2767706ab80d14a0456a11cda875e582be64f09e6020",
        ),
        (
            "salaried_loans_investments",
            "run_a3f411ac77a159ffb0fa9246113c3686",
            "e508c4a6b1de93f93734df394469478cc8ab4b8d92692f4d5c4af8e5ea47fccd",
        ),
        (
            "income_diverse",
            "run_9fb37c9f832d5afa87dbd50130691b09",
            "de1bc5010f36c74117fb05a28bfe4f60c0c349a0c0c7e1c45f948ebb3f4d2887",
        ),
        (
            "life_events",
            "run_9f1659f3111052c592d5dc48930ab188",
            "1e2a0995bb02a2e0fe8ea6f83be0d92c5aa78e4e3a9b1bc708ffa5045e3f62a7",
        ),
    ),
)
def test_all_versioned_reference_trees_remain_byte_identical(
    scenario: str,
    run_id: str,
    digest: str,
    project_root: Path,
    tmp_path: Path,
) -> None:
    config = load_scenario_config(project_root / "configs" / "scenarios" / f"{scenario}.yaml")
    generated = generate_scenario(config, seed=42, months=24)
    output = tmp_path / scenario
    write_run(generated, output)

    assert generated.simulation.run_id == run_id
    assert _output_digest(output) == digest
    committed = project_root / "examples" / "generated" / f"{scenario}_seed_42"
    assert _output_tree(output) == _output_tree(committed)


def test_v3_output_is_deterministic_versioned_separated_and_leak_free(
    tmp_path: Path,
) -> None:
    config = ScenarioConfigV3.model_validate(_forced_profile_payload(IncomeProfile.MIXED))
    first = generate_scenario(config, seed=42, months=3)
    second = generate_scenario(config, seed=42, months=3)
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    write_run(first, first_output)
    write_run(second, second_output)

    assert first == second
    assert _output_tree(first_output) == _output_tree(second_output)
    manifest = json.loads((first_output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["simulator_version"] == "0.4.0"
    assert manifest["contract_schema_version"] == "1.3"
    assert set(manifest["datasets"]["observed"]) == {
        "accounts",
        "balances",
        "credit_card_invoice_items",
        "credit_card_invoices",
        "credit_card_transactions",
        "credit_cards",
        "credit_limits",
        "investment_balances",
        "investment_transactions",
        "investments",
        "loan_balances",
        "loan_payments",
        "loans",
        "transactions",
    }
    assert set(manifest["datasets"]["private"]) == {
        "balance_sheet_ground_truth",
        "credit_card_transaction_ground_truth",
        "customer_ground_truth",
        "customer_month_ground_truth",
        "income_source_ground_truth",
        "investment_transaction_ground_truth",
        "loan_payment_ground_truth",
        "transaction_ground_truth",
    }
    for path in (first_output / "observed").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            assert record["schema_version"] == "1.3"
            _assert_no_factory_truth(_all_mapping_keys(record))
    private_sources = [
        json.loads(line)
        for line in (first_output / "private" / "income_source_ground_truth.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(private_sources) == 2
    assert all(FORBIDDEN_OBSERVED_NAMES.intersection(source) for source in private_sources)


def test_config_loader_dispatches_v3_without_changing_runtime_v0_alias(
    tmp_path: Path,
) -> None:
    import yaml

    path = tmp_path / "v3.yaml"
    path.write_text(yaml.safe_dump(_v3_payload(), sort_keys=False), encoding="utf-8")
    loaded = load_scenario_config(path)

    assert isinstance(loaded, ScenarioConfigV3)
    assert ScenarioConfig is not ScenarioConfigV3
    assert ScenarioConfig.model_fields["schema_version"].annotation.__args__ == ("1.0",)


@pytest.mark.parametrize(
    "case",
    ("unknown_source", "non_income_attribution", "wrong_direction", "wrong_amount"),
)
def test_income_invariant_validator_rejects_corrupted_causal_linkage(case: str) -> None:
    config = ScenarioConfigV3.model_validate(_forced_profile_payload(IncomeProfile.SALARIED))
    run = generate_scenario(config, seed=42, months=1).simulation
    events = list(run.events)
    entries = list(run.ledger_entries)
    event_index = next(
        index for index, event in enumerate(events) if event.economic_type is EconomicType.INCOME
    )
    event = events[event_index]
    entry_index = next(
        index for index, entry in enumerate(entries) if entry.event_id == event.event_id
    )

    if case == "unknown_source":
        events[event_index] = event.model_copy(update={"income_source_id": "inc_missing"})
    elif case == "non_income_attribution":
        events[event_index] = event.model_copy(update={"economic_type": EconomicType.OTHER})
    elif case == "wrong_direction":
        entries[entry_index] = entries[entry_index].model_copy(
            update={"direction": Direction.DEBIT}
        )
    elif case == "wrong_amount":
        entries[entry_index] = entries[entry_index].model_copy(
            update={"amount_minor": entries[entry_index].amount_minor + 1}
        )
    else:  # pragma: no cover - parametrization guard
        raise AssertionError(case)

    with pytest.raises(InvariantViolation):
        validate_income_simulation(
            sources=run.income_sources,
            events=events,
            entries=entries,
        )


def test_factory_constructor_and_upper_index_boundary(v3_config: ScenarioConfigV3) -> None:
    with pytest.raises(TypeError, match="settings must be CustomerFactorySettings"):
        CustomerFactory({}, seed=42)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="seed must be an integer"):
        CustomerFactory(v3_config.customer_factory, seed=True)

    member = CustomerFactory(v3_config.customer_factory, seed=42).sample_one(999_999)
    assert member.customer_index == 999_999


def test_cli_generates_v3_output_and_manifest(
    project_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "v3-cli"
    exit_code = main(
        [
            "generate",
            "--config",
            str(project_root / "configs" / "scenarios" / "income_diverse.yaml"),
            "--seed",
            "42",
            "--months",
            "1",
            "--output",
            str(output),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Generated run" in captured.out
    assert not captured.err
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["months"] == 1
    assert manifest["simulator_version"] == "0.4.0"
    assert manifest["contract_schema_version"] == "1.3"
    assert (output / "private" / "income_source_ground_truth.jsonl").is_file()


def test_writer_retries_brief_permission_errors_during_atomic_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FlakyStagingDirectory:
        attempts = 0

        def replace(self, target: Path) -> None:
            assert target == tmp_path / "published"
            self.attempts += 1
            if self.attempts < 3:
                raise PermissionError("brief scanner lock")

    staging = FlakyStagingDirectory()
    delays: list[float] = []
    monkeypatch.setattr(writer_module.time, "sleep", delays.append)

    writer_module._publish_staged_directory(  # type: ignore[arg-type]
        staging,
        tmp_path / "published",
    )

    assert staging.attempts == 3
    assert delays == [0.01, 0.025]
