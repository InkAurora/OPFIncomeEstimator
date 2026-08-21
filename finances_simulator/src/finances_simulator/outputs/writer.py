"""Write physically separated private and observed JSONL datasets."""

import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from finances_simulator.generation import GeneratedScenario
from finances_simulator.ground_truth.projector_v1 import GroundTruthBundleV1
from finances_simulator.ground_truth.projector_v2 import GroundTruthBundleV2
from finances_simulator.ground_truth.projector_v3 import GroundTruthBundleV3
from finances_simulator.ground_truth.projector_v4 import GroundTruthBundleV4
from finances_simulator.observations.projector_v1 import ObservationBundleV1
from finances_simulator.observations.projector_v2 import ObservationBundleV2
from finances_simulator.observations.projector_v3 import ObservationBundleV3
from finances_simulator.observations.projector_v4 import ObservationBundleV4


class OutputDirectoryNotEmptyError(FileExistsError):
    """Raised instead of overwriting an existing simulator output."""


class OutputWriteError(OSError):
    """Raised when a run cannot be staged or committed safely."""


def _publish_staged_directory(staging_directory: Path, output_directory: Path) -> None:
    """Publish atomically, tolerating brief Windows filesystem locks."""

    retry_delays = (0.01, 0.025, 0.05, 0.1)
    for attempt in range(len(retry_delays) + 1):
        try:
            staging_directory.replace(output_directory)
            return
        except PermissionError:
            if attempt == len(retry_delays):
                raise
            time.sleep(retry_delays[attempt])


def _encode_record(record: BaseModel) -> str:
    return json.dumps(
        record.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _write_jsonl(
    path: Path,
    records: Iterable[BaseModel],
    *,
    schema_version: str,
) -> dict[str, Any]:
    materialized = tuple(records)
    payload = "".join(f"{_encode_record(record)}\n" for record in materialized).encode("utf-8")
    path.write_bytes(payload)
    return {
        "path": path.as_posix(),
        "record_count": len(materialized),
        "schema_version": schema_version,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_run_contents(generated: GeneratedScenario, working_directory: Path) -> None:
    private_directory = working_directory / "private"
    observed_directory = working_directory / "observed"
    private_directory.mkdir(parents=True, exist_ok=True)
    observed_directory.mkdir(parents=True, exist_ok=True)

    truth = generated.ground_truth
    observations = generated.observations
    profile = generated.simulation.profile
    private_datasets = {
        "customer_ground_truth": _write_jsonl(
            private_directory / "customer_ground_truth.jsonl",
            truth.customers,
            schema_version=profile.contract_schema_version,
        ),
        "customer_month_ground_truth": _write_jsonl(
            private_directory / "customer_month_ground_truth.jsonl",
            truth.customer_months,
            schema_version=profile.contract_schema_version,
        ),
        "transaction_ground_truth": _write_jsonl(
            private_directory / "transaction_ground_truth.jsonl",
            truth.transactions,
            schema_version=profile.contract_schema_version,
        ),
    }
    if isinstance(
        truth,
        GroundTruthBundleV1 | GroundTruthBundleV2 | GroundTruthBundleV3 | GroundTruthBundleV4,
    ):
        private_datasets["credit_card_transaction_ground_truth"] = _write_jsonl(
            private_directory / "credit_card_transaction_ground_truth.jsonl",
            truth.credit_card_transactions,
            schema_version=profile.contract_schema_version,
        )
    if isinstance(truth, GroundTruthBundleV2 | GroundTruthBundleV3 | GroundTruthBundleV4):
        private_datasets.update(
            {
                "loan_payment_ground_truth": _write_jsonl(
                    private_directory / "loan_payment_ground_truth.jsonl",
                    truth.loan_payments,
                    schema_version=profile.contract_schema_version,
                ),
                "investment_transaction_ground_truth": _write_jsonl(
                    private_directory / "investment_transaction_ground_truth.jsonl",
                    truth.investment_transactions,
                    schema_version=profile.contract_schema_version,
                ),
                "balance_sheet_ground_truth": _write_jsonl(
                    private_directory / "balance_sheet_ground_truth.jsonl",
                    truth.balance_sheets,
                    schema_version=profile.contract_schema_version,
                ),
            }
        )
    if isinstance(truth, GroundTruthBundleV3 | GroundTruthBundleV4):
        private_datasets["income_source_ground_truth"] = _write_jsonl(
            private_directory / "income_source_ground_truth.jsonl",
            truth.income_sources,
            schema_version=profile.contract_schema_version,
        )
    if isinstance(truth, GroundTruthBundleV4):
        private_datasets.update(
            {
                "life_event_ground_truth": _write_jsonl(
                    private_directory / "life_event_ground_truth.jsonl",
                    truth.life_events,
                    schema_version=profile.contract_schema_version,
                ),
                "anomaly_ground_truth": _write_jsonl(
                    private_directory / "anomaly_ground_truth.jsonl",
                    truth.anomalies,
                    schema_version=profile.contract_schema_version,
                ),
            }
        )
    observed_datasets = {
        "accounts": _write_jsonl(
            observed_directory / "accounts.jsonl",
            observations.accounts,
            schema_version=profile.contract_schema_version,
        ),
        "balances": _write_jsonl(
            observed_directory / "balances.jsonl",
            observations.balances,
            schema_version=profile.contract_schema_version,
        ),
        "transactions": _write_jsonl(
            observed_directory / "transactions.jsonl",
            observations.transactions,
            schema_version=profile.contract_schema_version,
        ),
    }
    if isinstance(
        observations,
        ObservationBundleV1 | ObservationBundleV2 | ObservationBundleV3 | ObservationBundleV4,
    ):
        observed_datasets.update(
            {
                "credit_cards": _write_jsonl(
                    observed_directory / "credit_cards.jsonl",
                    observations.credit_cards,
                    schema_version=profile.contract_schema_version,
                ),
                "credit_limits": _write_jsonl(
                    observed_directory / "credit_limits.jsonl",
                    observations.credit_limits,
                    schema_version=profile.contract_schema_version,
                ),
                "credit_card_transactions": _write_jsonl(
                    observed_directory / "credit_card_transactions.jsonl",
                    observations.credit_card_transactions,
                    schema_version=profile.contract_schema_version,
                ),
                "credit_card_invoices": _write_jsonl(
                    observed_directory / "credit_card_invoices.jsonl",
                    observations.credit_card_invoices,
                    schema_version=profile.contract_schema_version,
                ),
                "credit_card_invoice_items": _write_jsonl(
                    observed_directory / "credit_card_invoice_items.jsonl",
                    observations.credit_card_invoice_items,
                    schema_version=profile.contract_schema_version,
                ),
            }
        )
    if isinstance(
        observations,
        ObservationBundleV2 | ObservationBundleV3 | ObservationBundleV4,
    ):
        observed_datasets.update(
            {
                "loans": _write_jsonl(
                    observed_directory / "loans.jsonl",
                    observations.loans,
                    schema_version=profile.contract_schema_version,
                ),
                "loan_payments": _write_jsonl(
                    observed_directory / "loan_payments.jsonl",
                    observations.loan_payments,
                    schema_version=profile.contract_schema_version,
                ),
                "loan_balances": _write_jsonl(
                    observed_directory / "loan_balances.jsonl",
                    observations.loan_balances,
                    schema_version=profile.contract_schema_version,
                ),
                "investments": _write_jsonl(
                    observed_directory / "investments.jsonl",
                    observations.investments,
                    schema_version=profile.contract_schema_version,
                ),
                "investment_transactions": _write_jsonl(
                    observed_directory / "investment_transactions.jsonl",
                    observations.investment_transactions,
                    schema_version=profile.contract_schema_version,
                ),
                "investment_balances": _write_jsonl(
                    observed_directory / "investment_balances.jsonl",
                    observations.investment_balances,
                    schema_version=profile.contract_schema_version,
                ),
            }
        )

    for datasets in (private_datasets, observed_datasets):
        for metadata in datasets.values():
            metadata["path"] = Path(metadata["path"]).relative_to(working_directory).as_posix()

    simulation = generated.simulation
    manifest = {
        "config_sha256": simulation.config_sha256,
        "contract_schema_version": profile.contract_schema_version,
        "datasets": {
            "observed": observed_datasets,
            "private": private_datasets,
        },
        "months": simulation.months,
        "rng_algorithm": profile.rng_algorithm,
        "run_id": simulation.run_id,
        "scenario_name": simulation.customer_twin.scenario_name,
        "seed": simulation.seed,
        "simulation_window": {
            "end_date": simulation.end_date.isoformat(),
            "start_date": simulation.start_date.isoformat(),
        },
        "simulator_version": profile.simulator_version,
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
        _publish_staged_directory(staging_directory, output_directory)
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
