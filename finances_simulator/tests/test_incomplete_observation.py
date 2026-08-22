"""Incomplete-observation acceptance tests for contract schema 1.6."""

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
)
from finances_simulator.config_v6 import ScenarioConfigV6
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.outputs import write_run
from finances_simulator.simulation.primitives import V6_PROFILE


@pytest.fixture(scope="module")
def v6_config_path(project_root: Path) -> Path:
    return project_root / "configs" / "scenarios" / "incomplete_observation.yaml"


@pytest.fixture(scope="module")
def v6_payload(v6_config_path: Path) -> dict[str, Any]:
    return yaml.safe_load(v6_config_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def v6_config(v6_config_path: Path) -> ScenarioConfigV6:
    loaded = load_scenario_config(v6_config_path)
    assert isinstance(loaded, ScenarioConfigV6)
    return loaded


@pytest.fixture(scope="module")
def generated_v6(v6_config: ScenarioConfigV6) -> GeneratedScenario:
    return generate_scenario(v6_config, seed=42)


def test_v6_profile_and_standard_coverage_levels(
    generated_v6: GeneratedScenario,
) -> None:
    assert generated_v6.simulation.profile == V6_PROFILE
    metrics = generated_v6.observations.observation_coverage
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
    generated_v6: GeneratedScenario,
) -> None:
    account_name_by_id = {
        item.account_id: item.account_label for item in generated_v6.observations.accounts
    }
    coverage_by_label = {
        account_name_by_id[item.account_id]: item.configured_coverage_percent
        for item in generated_v6.observations.observation_coverage
    }
    assert coverage_by_label == {
        "Primary checking account": 100,
        "Partial savings account": 70,
        "Limited reserve account": 40,
    }


def test_degradation_changes_only_observations(v6_config: ScenarioConfigV6) -> None:
    degraded = generate_scenario(v6_config, seed=42)
    complete_settings = ObservationDegradationSettings(
        consent=ConsentCoverageSettings(default_coverage_percent=100)
    )
    complete_config = v6_config.model_copy(
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
    generated_v6: GeneratedScenario,
) -> None:
    metrics = generated_v6.observations.observation_coverage
    assert sum(item.missing_record_count for item in metrics) > 0
    assert sum(item.late_record_count for item in metrics) > 0
    assert sum(item.duplicate_record_count for item in metrics) > 0
    assert sum(item.reversal_record_count for item in metrics) > 0
    assert sum(item.repost_record_count for item in metrics) == sum(
        item.reversal_record_count for item in metrics
    )

    records = generated_v6.observations.transactions
    originals = {
        item.transaction_id: item
        for item in records
        if item.duplicate_of_transaction_id is None
        and item.reversal_of_transaction_id is None
        and item.repost_of_transaction_id is None
    }
    reversal_by_original = {
        item.reversal_of_transaction_id: item
        for item in records
        if item.reversal_of_transaction_id is not None
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
        if item.repost_of_transaction_id is not None:
            source = originals[item.repost_of_transaction_id]
            assert item.account_id == source.account_id
            assert item.amount_minor == source.amount_minor
            assert item.direction == source.direction
            assert item.description == source.description
            assert item.balance_after_minor == source.balance_after_minor
            assert item.observed_at >= reversal_by_original[
                item.repost_of_transaction_id
            ].observed_at


def test_every_artifact_reversal_is_corrected_by_exactly_one_repost(
    generated_v6: GeneratedScenario,
) -> None:
    """ADR 0004: a reversal without its correction is the defect schema 1.6 removes."""

    records = generated_v6.observations.transactions
    reversed_ids = [
        item.reversal_of_transaction_id
        for item in records
        if item.reversal_of_transaction_id is not None
    ]
    reposted_ids = [
        item.repost_of_transaction_id
        for item in records
        if item.repost_of_transaction_id is not None
    ]
    assert reversed_ids
    assert sorted(reversed_ids) == sorted(reposted_ids)
    assert len(set(reposted_ids)) == len(reposted_ids)


def test_descriptions_are_provider_specific(generated_v6: GeneratedScenario) -> None:
    institution_by_account = {
        item.account_id: item.institution_id for item in generated_v6.observations.accounts
    }
    expected_prefix = {
        "fictional-bank-100": "FCB | ",
        "fictional-bank-070": "PCB | ",
    }
    assert all(
        item.description.startswith(expected_prefix[institution_by_account[item.account_id]])
        for item in generated_v6.observations.transactions
    )


def test_consent_degrades_card_loan_and_investment_streams(project_root: Path) -> None:
    payload = yaml.safe_load(
        (project_root / "configs" / "scenarios" / "income_diverse.yaml").read_text(
            encoding="utf-8"
        )
    )
    payload["schema_version"] = "1.6"
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
    degraded_config = ScenarioConfigV6.model_validate(payload)
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


def test_v6_observations_do_not_leak_private_labels(
    generated_v6: GeneratedScenario,
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
    for bundle_field in fields(generated_v6.observations):
        for record in getattr(generated_v6.observations, bundle_field.name):
            assert forbidden.isdisjoint(record.model_dump())


def test_v6_generation_is_reproducible(v6_config: ScenarioConfigV6) -> None:
    assert generate_scenario(v6_config, seed=42) == generate_scenario(v6_config, seed=42)
    assert (
        generate_scenario(v6_config, seed=43).simulation.run_id
        != generate_scenario(v6_config, seed=42).simulation.run_id
    )


def test_v6_configuration_is_strict_and_validates_references(
    v6_payload: dict[str, Any],
) -> None:
    payload = deepcopy(v6_payload)
    payload["observation_degradation"]["unknown"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScenarioConfigV6.model_validate(payload)

    payload = deepcopy(v6_payload)
    payload["observation_degradation"]["consent"]["accounts"][0][
        "coverage_percent"
    ] = 50
    with pytest.raises(ValidationError, match="Input should be 100, 70 or 40"):
        ScenarioConfigV6.model_validate(payload)

    payload = deepcopy(v6_payload)
    payload["observation_degradation"]["consent"]["institutions"][0][
        "institution_ref"
    ] = "missing"
    with pytest.raises(ValidationError, match="unknown institution"):
        ScenarioConfigV6.model_validate(payload)


def test_writer_emits_coverage_dataset_and_manifest_summary(
    generated_v6: GeneratedScenario,
    tmp_path: Path,
) -> None:
    manifest_path = write_run(generated_v6, tmp_path / "run")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["simulator_version"] == "0.7.0"
    assert manifest["contract_schema_version"] == "1.6"
    assert "observation_coverage" in manifest["datasets"]["observed"]
    assert manifest["observation_quality"]["eligible_record_count"] > 0
    assert manifest["world_config_sha256"] == generated_v6.simulation.world_config_sha256
    assert (manifest_path.parent / "observed" / "observation_coverage.jsonl").is_file()


def test_v6_reference_tree_remains_byte_identical(
    generated_v6: GeneratedScenario,
    project_root: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "reference"
    write_run(generated_v6, output)
    committed = project_root / "examples" / "generated" / "incomplete_observation_seed_42"

    def output_tree(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    assert output_tree(output) == output_tree(committed)
