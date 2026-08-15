"""Focused tests for deterministic primitives and architectural boundaries."""

from datetime import date
from pathlib import Path

import pytest

from finances_simulator.cli import main
from finances_simulator.config import ScenarioConfig
from finances_simulator.domain.accounts import Direction, LedgerEntry
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.ground_truth import project_ground_truth
from finances_simulator.outputs import OutputWriteError, write_run
from finances_simulator.outputs import writer as writer_module
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import make_rng, scheduled_date


def test_versioned_rng_has_stable_known_sequence() -> None:
    first = make_rng(42)
    second = make_rng(42)

    first_values = [first.randint(-10, 10) for _ in range(8)]
    second_values = [second.randint(-10, 10) for _ in range(8)]

    assert first_values == second_values == [-6, 4, 9, 9, -1, 9, -6, -7]


def test_calendar_schedule_clamps_to_leap_month_end() -> None:
    assert scheduled_date(date(2024, 2, 1), 31) == date(2024, 2, 29)
    assert scheduled_date(date(2025, 2, 1), 31) == date(2025, 2, 28)


def test_zero_month_override_is_rejected(scenario_config: ScenarioConfig) -> None:
    with pytest.raises(ValueError, match="months must be between 1 and 1200"):
        generate_scenario(scenario_config, seed=42, months=0)


def test_non_income_credit_changes_balance_without_becoming_income(
    scenario_config: ScenarioConfig,
) -> None:
    base_run = generate_scenario(scenario_config, seed=42, months=1).simulation
    account = base_run.customer_twin.primary_account
    event = FinancialEvent(
        event_id="evt_loan_test",
        customer_id=base_run.customer_twin.customer_id,
        occurred_at=date(2024, 1, 15),
        economic_type=EconomicType.LOAN_DISBURSEMENT,
        amount_minor=100_000,
        currency=account.currency,
        source_entity="fictional-lender",
        destination_entity=account.account_id,
        description="LOAN DISBURSEMENT",
    )
    entry = LedgerEntry(
        entry_id="ent_loan_test",
        event_id=event.event_id,
        account_id=account.account_id,
        posted_at=event.occurred_at,
        direction=Direction.CREDIT,
        amount_minor=event.amount_minor,
        balance_after_minor=account.opening_balance_minor + event.amount_minor,
        description=event.description,
    )
    run = SimulationRun(
        run_id="run_non_income_credit_test",
        seed=42,
        months=1,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        config_sha256=base_run.config_sha256,
        customer_twin=base_run.customer_twin,
        events=(event,),
        ledger_entries=(entry,),
    )

    truth = project_ground_truth(run)

    assert truth.customer_months[0].true_income_minor == 0
    assert truth.customer_months[0].closing_balance_minor == (
        account.opening_balance_minor + event.amount_minor
    )
    assert truth.transactions[0].economic_type is EconomicType.LOAN_DISBURSEMENT


def test_failed_write_leaves_no_partial_output(
    tmp_path: Path,
    generated_seed_42: GeneratedScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_directory = tmp_path / "atomic-output"
    real_write_bytes = Path.write_bytes

    def fail_on_second_private_dataset(path: Path, payload: bytes) -> int:
        if path.name == "customer_month_ground_truth.jsonl":
            raise OSError("simulated disk failure")
        return real_write_bytes(path, payload)

    monkeypatch.setattr(writer_module.Path, "write_bytes", fail_on_second_private_dataset)

    with pytest.raises(OutputWriteError, match="simulated disk failure"):
        write_run(generated_seed_42, output_directory)

    assert not output_directory.exists()
    assert not tuple(tmp_path.glob(".atomic-output.staging-*"))


def test_writer_atomically_replaces_an_existing_empty_directory(
    tmp_path: Path,
    generated_seed_42: GeneratedScenario,
) -> None:
    output_directory = tmp_path / "empty-output"
    output_directory.mkdir()

    manifest_path = write_run(generated_seed_42, output_directory)

    assert manifest_path == output_directory / "run_manifest.json"
    assert manifest_path.is_file()
    assert not tuple(tmp_path.glob(".empty-output.staging-*"))


def test_cli_reports_malformed_yaml_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "malformed.yaml"
    config_path.write_text("scenario: [unterminated", encoding="utf-8")

    exit_code = main(
        [
            "generate",
            "--config",
            str(config_path),
            "--seed",
            "42",
            "--output",
            str(tmp_path / "output"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error: Invalid YAML" in captured.err
    assert "line 1" in captured.err
    assert "Traceback" not in captured.err


def test_cli_reports_missing_configuration_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_path = tmp_path / "missing.yaml"

    exit_code = main(
        [
            "generate",
            "--config",
            str(missing_path),
            "--seed",
            "42",
            "--output",
            str(tmp_path / "output"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error: Unable to read scenario configuration" in captured.err
    assert "Traceback" not in captured.err


def test_cli_reports_output_file_without_overwriting_it(
    tmp_path: Path,
    example_config_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_file = tmp_path / "existing.txt"
    output_file.write_text("keep", encoding="utf-8")

    exit_code = main(
        [
            "generate",
            "--config",
            str(example_config_path),
            "--seed",
            "42",
            "--output",
            str(output_file),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error: Output path is not a directory" in captured.err
    assert "Traceback" not in captured.err
    assert output_file.read_text(encoding="utf-8") == "keep"
