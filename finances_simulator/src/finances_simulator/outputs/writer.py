"""Write physically separated private and observed JSONL datasets."""

import hashlib
import json
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from finances_simulator.generation import GeneratedScenario
from finances_simulator.simulation.primitives import (
    CONTRACT_SCHEMA_VERSION,
    RNG_ALGORITHM_VERSION,
    SIMULATOR_VERSION,
)


class OutputDirectoryNotEmptyError(FileExistsError):
    """Raised instead of overwriting an existing simulator output."""


class OutputWriteError(OSError):
    """Raised when a run cannot be staged or committed safely."""


def _encode_record(record: BaseModel) -> str:
    return json.dumps(
        record.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _write_jsonl(path: Path, records: Iterable[BaseModel]) -> dict[str, Any]:
    materialized = tuple(records)
    payload = "".join(f"{_encode_record(record)}\n" for record in materialized).encode("utf-8")
    path.write_bytes(payload)
    return {
        "path": path.as_posix(),
        "record_count": len(materialized),
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_run_contents(generated: GeneratedScenario, working_directory: Path) -> None:
    private_directory = working_directory / "private"
    observed_directory = working_directory / "observed"
    private_directory.mkdir(parents=True, exist_ok=True)
    observed_directory.mkdir(parents=True, exist_ok=True)

    truth = generated.ground_truth
    observations = generated.observations
    private_datasets = {
        "customer_ground_truth": _write_jsonl(
            private_directory / "customer_ground_truth.jsonl", truth.customers
        ),
        "customer_month_ground_truth": _write_jsonl(
            private_directory / "customer_month_ground_truth.jsonl", truth.customer_months
        ),
        "transaction_ground_truth": _write_jsonl(
            private_directory / "transaction_ground_truth.jsonl", truth.transactions
        ),
    }
    observed_datasets = {
        "accounts": _write_jsonl(observed_directory / "accounts.jsonl", observations.accounts),
        "balances": _write_jsonl(observed_directory / "balances.jsonl", observations.balances),
        "transactions": _write_jsonl(
            observed_directory / "transactions.jsonl", observations.transactions
        ),
    }

    for datasets in (private_datasets, observed_datasets):
        for metadata in datasets.values():
            metadata["path"] = Path(metadata["path"]).relative_to(working_directory).as_posix()

    simulation = generated.simulation
    manifest = {
        "config_sha256": simulation.config_sha256,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "datasets": {
            "observed": observed_datasets,
            "private": private_datasets,
        },
        "months": simulation.months,
        "rng_algorithm": RNG_ALGORITHM_VERSION,
        "run_id": simulation.run_id,
        "scenario_name": simulation.customer_twin.scenario_name,
        "seed": simulation.seed,
        "simulation_window": {
            "end_date": simulation.end_date.isoformat(),
            "start_date": simulation.start_date.isoformat(),
        },
        "simulator_version": SIMULATOR_VERSION,
    }
    manifest_path = working_directory / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _reject_occupied_destination(output_directory: Path) -> None:
    if output_directory.exists() and not output_directory.is_dir():
        raise OutputDirectoryNotEmptyError(f"Output path is not a directory: {output_directory}.")
    if output_directory.exists() and any(output_directory.iterdir()):
        raise OutputDirectoryNotEmptyError(
            f"Output directory is not empty: {output_directory}. Choose a new directory."
        )


def write_run(generated: GeneratedScenario, output_directory: Path) -> Path:
    """Stage a complete run, atomically publish it, and return its manifest path."""

    staging_directory: Path | None = None
    removed_empty_destination = False
    try:
        output_directory = output_directory.resolve()
        _reject_occupied_destination(output_directory)
        output_directory.parent.mkdir(parents=True, exist_ok=True)
        staging_directory = Path(
            tempfile.mkdtemp(
                prefix=f".{output_directory.name}.staging-",
                dir=output_directory.parent,
            )
        )
        _write_run_contents(generated, staging_directory)

        # Check again immediately before commit to avoid overwriting a racing writer.
        _reject_occupied_destination(output_directory)
        if output_directory.exists():
            output_directory.rmdir()
            removed_empty_destination = True
        staging_directory.replace(output_directory)
        staging_directory = None
    except OutputDirectoryNotEmptyError:
        if staging_directory is not None:
            shutil.rmtree(staging_directory, ignore_errors=True)
        raise
    except OSError as error:
        if staging_directory is not None:
            shutil.rmtree(staging_directory, ignore_errors=True)
        if removed_empty_destination and not output_directory.exists():
            try:
                output_directory.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        raise OutputWriteError(
            f"Unable to write simulator output '{output_directory}': {error}"
        ) from error
    except Exception:
        if staging_directory is not None:
            shutil.rmtree(staging_directory, ignore_errors=True)
        raise

    return output_directory / "run_manifest.json"
