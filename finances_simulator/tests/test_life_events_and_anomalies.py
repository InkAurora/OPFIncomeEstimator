"""Phase-5 life-event, seasonality, and anomaly tests."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from finances_simulator.config import load_scenario_config
from finances_simulator.config_v4 import ScenarioConfigV4
from finances_simulator.domain.accounts import Direction
from finances_simulator.domain.customer import CustomerTwinV4
from finances_simulator.domain.events import EconomicType
from finances_simulator.domain.life_events import (
    AnomalyType,
    EmploymentStatus,
    LifeEventType,
    MaritalStatus,
)
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.outputs import write_run
from finances_simulator.simulation.primitives import V4_PROFILE
from finances_simulator.simulation.v4 import (
    LifeEventSimulationError,
    realize_v4_income_amount,
)


@pytest.fixture(scope="module")
def v4_path(project_root: Path) -> Path:
    return project_root / "configs" / "scenarios" / "life_events.yaml"


@pytest.fixture(scope="module")
def v4_payload(v4_path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(v4_path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture(scope="module")
def v4_config(v4_path: Path) -> ScenarioConfigV4:
    loaded = load_scenario_config(v4_path)
    assert isinstance(loaded, ScenarioConfigV4)
    return loaded


@pytest.fixture(scope="module")
def generated_v4(v4_config: ScenarioConfigV4) -> GeneratedScenario:
    return generate_scenario(v4_config, seed=42)


def test_bundled_v4_scenario_covers_phase_five_taxonomy(
    v4_config: ScenarioConfigV4,
    generated_v4: GeneratedScenario,
) -> None:
    assert v4_config.schema_version == "1.4"
    assert generated_v4.simulation.profile == V4_PROFILE
    assert {LifeEventType(item.event_type) for item in v4_config.life_events} == set(LifeEventType)
    assert {AnomalyType(item.anomaly_type) for item in v4_config.anomalies} == set(AnomalyType)
    assert len(generated_v4.simulation.life_event_transitions) == 14
    assert len(generated_v4.simulation.anomalies) == 4


def test_raise_promotion_job_loss_and_job_change_apply_on_effective_date(
    generated_v4: GeneratedScenario,
) -> None:
    income_by_date = {
        event.occurred_at.isoformat(): event
        for event in generated_v4.simulation.events
        if event.economic_type is EconomicType.INCOME
        and event.metadata.get("life_event_type") is None
    }
    assert income_by_date["2024-03-05"].amount_minor == 500_000
    assert income_by_date["2024-04-05"].amount_minor == 550_000
    assert income_by_date["2024-06-05"].amount_minor == 605_000
    assert "2024-08-05" not in income_by_date
    assert "2024-09-05" not in income_by_date
    assert income_by_date["2024-10-05"].amount_minor == 700_000
    assert income_by_date["2024-10-05"].source_entity == "Example Second Employer Ltd."
    assert income_by_date["2024-10-05"].description == "NEW EMPLOYER PAYROLL CREDIT"


def test_transition_truth_captures_income_before_and_after(
    generated_v4: GeneratedScenario,
) -> None:
    truth_by_ref = {item.life_event_ref: item for item in generated_v4.ground_truth.life_events}
    raise_truth = truth_by_ref["annual_raise"]
    assert raise_truth.annualized_base_income_before_minor == 6_000_000
    assert raise_truth.annualized_base_income_after_minor == 6_600_000
    assert raise_truth.income_sources_before[0].base_amount_minor == 500_000
    assert raise_truth.income_sources_after[0].base_amount_minor == 550_000

    loss_truth = truth_by_ref["job_loss"]
    assert loss_truth.annualized_base_income_before_minor == 7_260_000
    assert loss_truth.annualized_base_income_after_minor == 0
    assert loss_truth.state_before.employment_status is EmploymentStatus.SALARIED
    assert loss_truth.state_after.employment_status is EmploymentStatus.UNEMPLOYED

    change_truth = truth_by_ref["job_change"]
    assert change_truth.annualized_base_income_before_minor == 0
    assert change_truth.annualized_base_income_after_minor == 8_400_000
    assert change_truth.income_sources_after[0].payer == "Example Second Employer Ltd."


def test_household_state_transitions_are_continuous(
    generated_v4: GeneratedScenario,
) -> None:
    twin = generated_v4.simulation.customer_twin
    assert isinstance(twin, CustomerTwinV4)
    assert twin.initial_life_state.marital_status is MaritalStatus.SINGLE
    assert twin.final_life_state.marital_status is MaritalStatus.DIVORCED
    assert twin.final_life_state.dependent_count == 0
    assert twin.final_life_state.property_count == 1
    assert twin.final_life_state.vehicle_count == 1
    transitions = generated_v4.simulation.life_event_transitions
    for previous, current in zip(transitions, transitions[1:], strict=False):
        assert current.state_before == previous.state_after
        assert current.income_sources_before == previous.income_sources_after


def test_bonus_inheritance_and_exceptional_expenses_keep_economic_meaning(
    generated_v4: GeneratedScenario,
) -> None:
    event_by_life_ref = {
        event.metadata["life_event_ref"]: event
        for event in generated_v4.simulation.events
        if "life_event_ref" in event.metadata
    }
    assert event_by_life_ref["annual_bonus"].economic_type is EconomicType.INCOME
    assert event_by_life_ref["annual_bonus"].income_source_id is not None
    assert event_by_life_ref["inheritance"].economic_type is EconomicType.GIFT
    for event_ref in (
        "vehicle_purchase",
        "medical_expense",
        "property_purchase",
        "vacation",
    ):
        assert event_by_life_ref[event_ref].economic_type is EconomicType.EXPENSE
        assert event_by_life_ref[event_ref].income_source_id is None


def test_income_and_expense_seasonality_are_calendar_based(
    generated_v4: GeneratedScenario,
) -> None:
    recurring_income = {
        event.occurred_at.isoformat(): event.amount_minor
        for event in generated_v4.simulation.events
        if event.economic_type is EconomicType.INCOME
        and event.metadata.get("life_event_type") is None
    }
    assert recurring_income["2025-06-05"] == 700_000
    assert recurring_income["2025-07-05"] == 630_000
    # Source December multiplier 1.1 and scenario multiplier 1.2 combine once.
    assert recurring_income["2025-12-05"] == 924_000

    housing = {
        event.occurred_at.isoformat(): event.amount_minor
        for event in generated_v4.simulation.events
        if event.metadata.get("rule_id") == "housing"
    }
    assert housing["2024-01-10"] == 160_000
    assert housing["2024-07-10"] == 208_000
    assert housing["2024-12-10"] == 240_000


@pytest.mark.parametrize(
    ("base", "source_factor", "scenario_factor", "shock", "expected"),
    [
        (100, 10_000, 10_000, 0, 100),
        (100, 15_000, 12_000, 0, 180),
        (101, 10_000, 10_000, 0, 101),
        (100, 10_000, 10_000, -10_000, 0),
        (100, 10_000, 10_000, 5_000, 150),
    ],
)
def test_v4_income_realization_uses_one_half_up_rounding_step(
    base: int,
    source_factor: int,
    scenario_factor: int,
    shock: int,
    expected: int,
) -> None:
    assert realize_v4_income_amount(base, source_factor, scenario_factor, shock) == expected


def test_anomaly_labels_preserve_expected_economic_types(
    generated_v4: GeneratedScenario,
) -> None:
    expected = {
        AnomalyType.LARGE_PIX_TRANSFER: EconomicType.OWN_TRANSFER,
        AnomalyType.REFUND: EconomicType.REFUND,
        AnomalyType.ASSET_SALE: EconomicType.ASSET_SALE,
        AnomalyType.INVESTMENT_REDEMPTION: EconomicType.INVESTMENT_REDEMPTION,
    }
    event_by_id = {event.event_id: event for event in generated_v4.simulation.events}
    for anomaly in generated_v4.simulation.anomalies:
        assert anomaly.economic_type is expected[anomaly.anomaly_type]
        assert (
            event_by_id[anomaly.financial_event_id].economic_type is expected[anomaly.anomaly_type]
        )


def test_large_pix_is_a_balanced_own_transfer(generated_v4: GeneratedScenario) -> None:
    anomaly = next(
        item
        for item in generated_v4.simulation.anomalies
        if item.anomaly_type is AnomalyType.LARGE_PIX_TRANSFER
    )
    entries = [
        entry
        for entry in generated_v4.simulation.ledger_entries
        if entry.event_id == anomaly.financial_event_id
    ]
    assert len(entries) == 2
    assert {entry.direction for entry in entries} == {Direction.CREDIT, Direction.DEBIT}
    assert {entry.amount_minor for entry in entries} == {300_000}
    assert len({entry.transfer_group_id for entry in entries}) == 1


def test_refund_asset_sale_and_redemption_are_not_income(
    generated_v4: GeneratedScenario,
) -> None:
    anomaly_event_ids = {item.financial_event_id for item in generated_v4.simulation.anomalies}
    anomaly_events = [
        event for event in generated_v4.simulation.events if event.event_id in anomaly_event_ids
    ]
    assert all(event.economic_type is not EconomicType.INCOME for event in anomaly_events)
    assert all(event.income_source_id is None for event in anomaly_events)
    redemption = next(
        item
        for item in generated_v4.simulation.investment_transactions
        if item.rule_id == "anomaly.reserve_redemption"
    )
    assert redemption.amount_minor == 150_000
    assert redemption.event_id in anomaly_event_ids


def test_month_truth_tracks_external_inflows_and_state(generated_v4: GeneratedScenario) -> None:
    months = {item.month: item for item in generated_v4.ground_truth.customer_months}
    assert months["2024-05"].external_inflows_minor == 45_000
    assert months["2024-12"].external_inflows_minor == 600_000
    assert months["2025-05"].external_inflows_minor == 180_000
    assert months["2024-08"].employment_status is EmploymentStatus.UNEMPLOYED
    assert months["2024-08"].active_income_source_ids == ()
    assert months["2024-10"].employment_status is EmploymentStatus.SALARIED
    assert len(months["2024-10"].active_income_source_ids) == 1


def test_observations_do_not_leak_private_phase_five_labels(
    generated_v4: GeneratedScenario,
) -> None:
    forbidden = {
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
    for bundle_field in fields(generated_v4.observations):
        collection = getattr(generated_v4.observations, bundle_field.name)
        for record in collection:
            assert forbidden.isdisjoint(record.model_dump())


def test_v4_generation_is_reproducible(v4_config: ScenarioConfigV4) -> None:
    first = generate_scenario(v4_config, seed=42)
    second = generate_scenario(v4_config, seed=42)
    assert first == second
    assert generate_scenario(v4_config, seed=43).simulation.run_id != first.simulation.run_id


def test_short_run_omits_future_transitions_and_anomalies(
    v4_config: ScenarioConfigV4,
) -> None:
    generated = generate_scenario(v4_config, seed=42, months=6)
    assert [item.life_event_ref for item in generated.simulation.life_event_transitions] == [
        "marriage",
        "first_dependent",
        "annual_raise",
        "promotion",
    ]
    assert [item.anomaly_ref for item in generated.simulation.anomalies] == [
        "large_pix",
        "merchant_refund",
    ]


def test_v4_configuration_is_strict_and_validates_references(
    v4_payload: dict[str, Any],
) -> None:
    payload = deepcopy(v4_payload)
    payload["seasonality"]["unknown"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScenarioConfigV4.model_validate(payload)

    payload = deepcopy(v4_payload)
    payload["life_events"][2]["amount_multiplier_basis_points"] = 11_000
    with pytest.raises(ValidationError, match="exactly one"):
        ScenarioConfigV4.model_validate(payload)

    payload = deepcopy(v4_payload)
    payload["life_events"][0]["life_event_ref"] = payload["life_events"][1]["life_event_ref"]
    with pytest.raises(ValidationError, match="life_event_ref values must be unique"):
        ScenarioConfigV4.model_validate(payload)

    payload = deepcopy(v4_payload)
    payload["life_events"][2]["income_source_ref"] = "missing"
    with pytest.raises(ValidationError, match="unknown income source"):
        ScenarioConfigV4.model_validate(payload)


def test_unfunded_redemption_anomaly_fails_explicitly(
    v4_config: ScenarioConfigV4,
) -> None:
    anomalies = [
        item.model_copy(update={"amount_minor": 10**12})
        if item.anomaly_type == "INVESTMENT_REDEMPTION"
        else item
        for item in v4_config.anomalies
    ]
    invalid = v4_config.model_copy(update={"anomalies": anomalies})
    with pytest.raises(LifeEventSimulationError, match="was not accepted"):
        generate_scenario(invalid, seed=42)


def test_writer_emits_v4_private_datasets_and_manifest(
    generated_v4: GeneratedScenario,
    tmp_path: Path,
) -> None:
    manifest_path = write_run(generated_v4, tmp_path / "run")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["simulator_version"] == "0.5.0"
    assert manifest["contract_schema_version"] == "1.4"
    assert set(manifest["datasets"]["private"]) >= {
        "life_event_ground_truth",
        "anomaly_ground_truth",
        "income_source_ground_truth",
    }
    assert (manifest_path.parent / "private" / "life_event_ground_truth.jsonl").is_file()
    assert (manifest_path.parent / "private" / "anomaly_ground_truth.jsonl").is_file()
    assert not (manifest_path.parent / "observed" / "life_events.jsonl").exists()


def test_v4_balance_sheet_net_worth_bridge_includes_external_inflows(
    generated_v4: GeneratedScenario,
) -> None:
    months = {item.month: item for item in generated_v4.ground_truth.customer_months}
    sheets = {item.month: item for item in generated_v4.ground_truth.balance_sheets}
    for month_key, month in months.items():
        sheet = sheets[month_key]
        assert sheet.net_worth_minor - sheet.opening_net_worth_minor == (
            month.true_income_minor
            + month.external_inflows_minor
            - month.true_expenses_minor
            - month.loan_interest_paid_minor
            + month.investment_return_minor
        )
