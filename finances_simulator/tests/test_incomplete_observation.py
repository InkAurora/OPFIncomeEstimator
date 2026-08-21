"""Phase-6 incomplete-observation acceptance tests."""

from copy import deepcopy
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from finances_simulator.config import load_scenario_config
from finances_simulator.config_v5 import (
    ConsentCoverageSettings,
    ObservationDegradationSettings,
    ScenarioConfigV5,
)
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.outputs import write_run
from finances_simulator.simulation.primitives import V5_PROFILE


@pytest.fixture(scope="module")
def v5_config_path(project_root: Path) -> Path:
    return project_root / "configs" / "scenarios" / "incomplete_observation.yaml"


@pytest.fixture(scope="module")
def v5_payload(v5_config_path: Path) -> dict[str, Any]:
    return yaml.safe_load(v5_config_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def v5_config(v5_config_path: Path) -> ScenarioConfigV5:
    loaded = load_scenario_config(v5_config_path)
    assert isinstance(loaded, ScenarioConfigV5)
    return loaded


@pytest.fixture(scope="module")
def generated_v5(v5_config: ScenarioConfigV5) -> GeneratedScenario:
    return generate_scenario(v5_config, seed=42)


def test_v5_profile_and_standard_coverage_levels(
    generated_v5: GeneratedScenario,
) -> None:
    assert generated_v5.simulation.profile == V5_PROFILE
    metrics = generated_v5.observations.observation_coverage
    assert {item.configured_coverage_percent for item in metrics} == {100, 70, 40}
    for metric in metrics:
        assert metric.consented_record_count == (
            metric.eligible_record_count * metric.configured_coverage_percent + 50
        ) // 100
        assert metric.observed_original_record_count == (
            metric.consented_record_count - metric.missing_record_count
        )
        assert metric.effective_coverage_basis_points == (
            metric.observed_original_record_count * 10_000
            + metric.eligible_record_count // 2
        ) // metric.eligible_record_count


def test_account_coverage_overrides_institution_coverage(
    generated_v5: GeneratedScenario,
) -> None:
    account_name_by_id = {
        item.account_id: item.account_label for item in generated_v5.observations.accounts
    }
    coverage_by_label = {
        account_name_by_id[item.account_id]: item.configured_coverage_percent
        for item in generated_v5.observations.observation_coverage
    }
    assert coverage_by_label == {
        "Primary checking account": 100,
        "Partial savings account": 70,
        "Limited reserve account": 40,
    }


def test_degradation_changes_only_observations(v5_config: ScenarioConfigV5) -> None:
    degraded = generate_scenario(v5_config, seed=42)
    complete_settings = ObservationDegradationSettings(
        consent=ConsentCoverageSettings(default_coverage_percent=100)
    )
    complete_config = v5_config.model_copy(
        update={"observation_degradation": complete_settings}
    )
    complete = generate_scenario(complete_config, seed=42)

    for field_name in (
        "customer_twin",
        "events",
        "ledger_entries",
        "cards",
        "card_purchases",
        "card_installments",
        "card_invoices",
        "credit_limit_snapshots",
        "loans",
        "loan_payments",
        "loan_balance_snapshots",
        "investments",
        "investment_transactions",
        "investment_balance_snapshots",
        "factory_member",
        "income_sources",
        "life_event_transitions",
        "anomalies",
    ):
        assert getattr(degraded.simulation, field_name) == getattr(
            complete.simulation,
            field_name,
        )
    assert degraded.ground_truth == complete.ground_truth
    assert degraded.observations != complete.observations


def test_missing_late_duplicate_and_reversal_records_are_measurable_and_traceable(
    generated_v5: GeneratedScenario,
) -> None:
    metrics = generated_v5.observations.observation_coverage
    assert sum(item.missing_record_count for item in metrics) > 0
    assert sum(item.late_record_count for item in metrics) > 0
    assert sum(item.duplicate_record_count for item in metrics) > 0
    assert sum(item.reversal_record_count for item in metrics) > 0

    records = generated_v5.observations.transactions
    originals = {
        item.transaction_id: item
        for item in records
        if item.duplicate_of_transaction_id is None
        and item.reversal_of_transaction_id is None
    }
    for item in records:
        if item.duplicate_of_transaction_id is not None:
            source = originals[item.duplicate_of_transaction_id]
            assert item.account_id == source.account_id
            assert item.amount_minor == source.amount_minor
            assert item.direction == source.direction
        if item.reversal_of_transaction_id is not None:
            source = originals[item.reversal_of_transaction_id]
            assert item.account_id == source.account_id
            assert item.amount_minor == source.amount_minor
            assert item.direction != source.direction


def test_descriptions_are_provider_specific(generated_v5: GeneratedScenario) -> None:
    institution_by_account = {
        item.account_id: item.institution_id for item in generated_v5.observations.accounts
    }
    expected_prefix = {
        "fictional-bank-100": "FCB | ",
        "fictional-bank-070": "PCB | ",
    }
    assert all(
        item.description.startswith(expected_prefix[institution_by_account[item.account_id]])
        for item in generated_v5.observations.transactions
    )


def test_consent_degrades_card_loan_and_investment_streams(project_root: Path) -> None:
    payload = yaml.safe_load(
        (project_root / "configs" / "scenarios" / "income_diverse.yaml").read_text(
            encoding="utf-8"
        )
    )
    payload["schema_version"] = "1.5"
    payload["observation_degradation"] = {
        "consent": {
            "default_coverage_percent": 100,
            "institutions": [
                {"institution_ref": "bank_primary", "coverage_percent": 70},
                {"institution_ref": "bank_reserve", "coverage_percent": 40},
            ],
        },
        "institution_descriptions": [
            {"institution_ref": "bank_primary", "description_prefix": "PRIMARY"},
            {"institution_ref": "bank_reserve", "description_prefix": "RESERVE"},
        ],
    }
    degraded_config = ScenarioConfigV5.model_validate(payload)
    complete_config = degraded_config.model_copy(
        update={"observation_degradation": ObservationDegradationSettings()}
    )
    degraded = generate_scenario(degraded_config, seed=42)
    complete = generate_scenario(complete_config, seed=42)

    for dataset_name in (
        "credit_limits",
        "credit_card_transactions",
        "credit_card_invoices",
        "credit_card_invoice_items",
        "loan_payments",
        "loan_balances",
        "investment_transactions",
        "investment_balances",
    ):
        assert len(getattr(degraded.observations, dataset_name)) < len(
            getattr(complete.observations, dataset_name)
        )
    assert all(
        item.description.startswith("PRIMARY | ")
        for item in degraded.observations.credit_card_transactions
    )
    assert all(
        item.description.startswith("RESERVE | ")
        for item in degraded.observations.investment_transactions
    )


def test_v5_observations_do_not_leak_private_labels(
    generated_v5: GeneratedScenario,
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
    for bundle_field in fields(generated_v5.observations):
        for record in getattr(generated_v5.observations, bundle_field.name):
            assert forbidden.isdisjoint(record.model_dump())


def test_v5_generation_is_reproducible(v5_config: ScenarioConfigV5) -> None:
    assert generate_scenario(v5_config, seed=42) == generate_scenario(v5_config, seed=42)
    assert (
        generate_scenario(v5_config, seed=43).simulation.run_id
        != generate_scenario(v5_config, seed=42).simulation.run_id
    )


def test_v5_configuration_is_strict_and_validates_references(
    v5_payload: dict[str, Any],
) -> None:
    payload = deepcopy(v5_payload)
    payload["observation_degradation"]["unknown"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScenarioConfigV5.model_validate(payload)

    payload = deepcopy(v5_payload)
    payload["observation_degradation"]["consent"]["accounts"][0][
        "coverage_percent"
    ] = 50
    with pytest.raises(ValidationError, match="Input should be 100, 70 or 40"):
        ScenarioConfigV5.model_validate(payload)

    payload = deepcopy(v5_payload)
    payload["observation_degradation"]["consent"]["institutions"][0][
        "institution_ref"
    ] = "missing"
    with pytest.raises(ValidationError, match="unknown institution"):
        ScenarioConfigV5.model_validate(payload)


def test_writer_emits_coverage_dataset_and_manifest_summary(
    generated_v5: GeneratedScenario,
    tmp_path: Path,
) -> None:
    manifest_path = write_run(generated_v5, tmp_path / "run")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["simulator_version"] == "0.6.0"
    assert manifest["contract_schema_version"] == "1.5"
    assert "observation_coverage" in manifest["datasets"]["observed"]
    assert manifest["observation_quality"]["eligible_record_count"] > 0
    assert manifest["world_config_sha256"] == generated_v5.simulation.world_config_sha256
    assert (manifest_path.parent / "observed" / "observation_coverage.jsonl").is_file()


def test_v5_reference_tree_remains_byte_identical(
    generated_v5: GeneratedScenario,
    project_root: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "reference"
    write_run(generated_v5, output)
    committed = project_root / "examples" / "generated" / "incomplete_observation_seed_42"

    def output_tree(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    assert output_tree(output) == output_tree(committed)
