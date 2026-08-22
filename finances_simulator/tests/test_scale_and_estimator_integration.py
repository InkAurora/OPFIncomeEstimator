"""Phase-7 scale, Parquet, boundary, and evaluation acceptance tests."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from finances_simulator.batch import generate_population, write_population
from finances_simulator.cli import main
from finances_simulator.config import load_scenario_config
from finances_simulator.integration import (
    BaselineIncomeEstimator,
    build_estimator_input,
    build_estimator_input_v1_2,
    evaluate_population,
)
from finances_simulator.simulation.primitives import SIMULATOR_VERSION


@pytest.fixture(scope="module")
def phase7_config(project_root: Path):
    return load_scenario_config(
        project_root / "configs" / "scenarios" / "incomplete_observation.yaml"
    )


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_population_is_ordered_unique_and_parallel_deterministic(phase7_config) -> None:
    sequential = generate_population(
        phase7_config,
        population_size=3,
        seed=700,
        months=2,
        workers=1,
    )
    parallel = generate_population(
        phase7_config,
        population_size=3,
        seed=700,
        months=2,
        workers=2,
    )

    assert sequential == parallel
    assert sequential.seeds == (700, 701, 702)
    assert len({item.simulation.run_id for item in sequential.members}) == 3
    assert len(
        {item.simulation.customer_twin.customer_id for item in sequential.members}
    ) == 3


def test_partitioned_parquet_and_reports_are_byte_deterministic(
    phase7_config,
    tmp_path: Path,
) -> None:
    sequential = generate_population(
        phase7_config,
        population_size=3,
        seed=710,
        months=3,
        workers=1,
    )
    parallel = generate_population(
        phase7_config,
        population_size=3,
        seed=710,
        months=3,
        workers=2,
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_manifest_path = write_population(sequential, first, partition_count=4)
    write_population(parallel, second, partition_count=4)

    assert _tree(first) == _tree(second)
    manifest = json.loads(first_manifest_path.read_text(encoding="utf-8"))
    assert manifest["batch_schema_version"] == "1.0"
    assert manifest["simulator_version"] == "0.7.0"
    assert manifest["source_simulator_version"] == "0.6.0"
    assert manifest["contract_schema_version"] == "1.5"
    assert manifest["population_size"] == 3
    transaction_files = manifest["datasets"]["observed"]["transactions"]["files"]
    assert transaction_files
    assert all("customer_bucket=" in item["path"] for item in transaction_files)
    assert sum(item["record_count"] for item in transaction_files) == manifest[
        "datasets"
    ]["observed"]["transactions"]["record_count"]
    parquet = pq.ParquetFile(first / transaction_files[0]["path"])
    assert parquet.metadata.num_rows == transaction_files[0]["record_count"]
    assert parquet.schema_arrow.metadata[b"trust"] == b"observed"
    assert (first / "evaluation" / "report.json").is_file()


def test_estimator_boundary_is_allow_listed_and_report_has_all_required_metrics(
    phase7_config,
) -> None:
    population = generate_population(
        phase7_config,
        population_size=2,
        seed=720,
        months=4,
        workers=1,
    )
    request = build_estimator_input(population.members[0])
    forbidden = {
        "economic_type",
        "income_profile",
        "income_source_id",
        "life_event_id",
        "true_income_minor",
    }
    assert forbidden.isdisjoint(request.model_dump(mode="json"))
    assert all(
        forbidden.isdisjoint(record.model_dump(mode="json"))
        for field in fields(population.members[0].observations)
        for record in getattr(population.members[0].observations, field.name)
    )

    evaluation = evaluate_population(population, BaselineIncomeEstimator())
    report = evaluation.report
    assert report.overall.count == 8
    assert report.by_income_type
    assert report.by_income_range
    assert report.by_consent_coverage
    assert report.life_event_error.outside_events.count == 8
    assert report.confidence_interval_coverage.interval_count == 8
    assert {
        item.economic_type
        for item in report.false_income_classification.by_economic_type
    } == {"OWN_TRANSFER", "LOAN_DISBURSEMENT", "INVESTMENT_REDEMPTION"}


def test_life_event_report_marks_event_neighborhood(project_root: Path) -> None:
    config = load_scenario_config(
        project_root / "configs" / "scenarios" / "life_events.yaml"
    )
    population = generate_population(
        config,
        population_size=1,
        seed=42,
        workers=1,
    )
    report = evaluate_population(population, BaselineIncomeEstimator()).report
    assert report.life_event_error.around_events.count > 0
    assert report.life_event_error.outside_events.count > 0


def test_batch_cli_writes_population_and_rejects_occupied_output(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "population"
    arguments = [
        "generate-batch",
        "--config",
        str(project_root / "configs" / "scenarios" / "incomplete_observation.yaml"),
        "--seed",
        "730",
        "--population-size",
        "1",
        "--months",
        "2",
        "--workers",
        "1",
        "--partitions",
        "2",
        "--output",
        str(output),
    ]
    assert main(arguments) == 0
    assert (output / "population_manifest.json").is_file()
    assert main(arguments) == 2


def test_phase7_reference_population_is_byte_stable(
    phase7_config,
    project_root: Path,
    tmp_path: Path,
) -> None:
    population = generate_population(
        phase7_config,
        population_size=2,
        seed=100,
        months=3,
        workers=1,
    )
    output = tmp_path / "reference"
    write_population(population, output, partition_count=2)
    committed = (
        project_root
        / "examples"
        / "generated"
        / "phase7_population_seed_100_count_2"
    )
    assert _tree(output) == _tree(committed)


def test_phase7_version_and_batch_argument_validation(phase7_config) -> None:
    assert SIMULATOR_VERSION == "0.7.0"
    with pytest.raises(ValueError, match="population_size"):
        generate_population(phase7_config, population_size=0, seed=1)
    with pytest.raises(ValueError, match="workers"):
        generate_population(
            phase7_config,
            population_size=1,
            seed=1,
            workers=True,
        )
    with pytest.raises(ValueError, match="less than or equal"):
        generate_population(phase7_config, population_size=100_001, seed=1)


def test_estimator_input_1_2_exposes_products_without_private_fields(
    project_root: Path,
) -> None:
    config = load_scenario_config(
        project_root / "configs" / "scenarios" / "salaried_loans_investments.yaml"
    )
    population = generate_population(
        config,
        population_size=1,
        seed=42,
        months=12,
        workers=1,
    )
    member = population.members[0]

    request = build_estimator_input_v1_2(member)
    payload = request.model_dump(mode="json")
    forbidden = {
        "economic_type",
        "income_profile",
        "income_source_id",
        "life_event_id",
        "true_income_minor",
        "annual_interest_basis_points",
        "institution_name",
    }

    assert request.schema_version == "1.2"
    assert forbidden.isdisjoint(payload)
    assert all(
        forbidden.isdisjoint(record)
        for collection in payload.values()
        if isinstance(collection, list)
        for record in collection
    )

    observations = member.observations
    assert len(request.credit_cards) == len(observations.credit_cards)
    assert len(request.credit_limits) == len(observations.credit_limits)
    assert len(request.card_transactions) == len(observations.credit_card_transactions)
    assert len(request.card_invoices) == len(observations.credit_card_invoices)
    assert len(request.loan_payments) == len(observations.loan_payments)
    assert len(request.loan_balances) == len(observations.loan_balances)
    assert len(request.investments) == len(observations.investments)
    assert len(request.investment_balances) == len(observations.investment_balances)


def test_estimator_input_1_2_tolerates_contracts_without_products(
    project_root: Path,
) -> None:
    """Schema 1.0 has no product domains; the adapter must still produce a valid request."""

    config = load_scenario_config(
        project_root / "configs" / "scenarios" / "salaried_basic.yaml"
    )
    population = generate_population(
        config,
        population_size=1,
        seed=42,
        months=3,
        workers=1,
    )

    request = build_estimator_input_v1_2(population.members[0])

    assert request.schema_version == "1.2"
    assert request.transactions
    assert request.credit_cards == ()
    assert request.loan_payments == ()
    assert request.investment_balances == ()
