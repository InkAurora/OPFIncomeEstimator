"""Atomic partitioned-Parquet writer for Phase-7 populations."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel

from finances_simulator.batch.generation import GeneratedPopulation
from finances_simulator.integration.contracts import (
    EstimatorInputV1,
    IncomeEstimateV1,
    IncomeEstimator,
)
from finances_simulator.outputs.writer import (
    OutputDirectoryNotEmptyError,
    OutputWriteError,
)
from finances_simulator.validation.v7 import validate_generated_boundary


def _publish_staged_directory(staging_directory: Path, output_directory: Path) -> None:
    retry_delays = (0.01, 0.025, 0.05, 0.1)
    for attempt in range(len(retry_delays) + 1):
        try:
            staging_directory.replace(output_directory)
            return
        except PermissionError:
            if attempt == len(retry_delays):
                raise
            time.sleep(retry_delays[attempt])


def _reject_occupied_destination(output_directory: Path) -> None:
    if output_directory.exists() and not output_directory.is_dir():
        raise OutputDirectoryNotEmptyError(
            f"Output path is not a directory: {output_directory}."
        )
    if output_directory.exists() and any(output_directory.iterdir()):
        raise OutputDirectoryNotEmptyError(
            f"Output directory is not empty: {output_directory}. Choose a new directory."
        )


def _customer_bucket(customer_id: str, partition_count: int) -> str:
    digest = hashlib.sha256(customer_id.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") % partition_count
    width = max(2, len(str(partition_count - 1)))
    return f"{bucket:0{width}d}"


def _arrow_safe(value: Any) -> Any:
    """Keep primitive lists typed; encode open/nested objects as canonical JSON."""

    if isinstance(value, dict):
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    if isinstance(value, list):
        if any(isinstance(item, (dict, list)) for item in value):
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        return value
    return value


def _record_row(
    population: GeneratedPopulation,
    member_index: int,
    record: BaseModel,
    partition_count: int,
) -> dict[str, Any]:
    simulation = population.members[member_index].simulation
    payload = {
        key: _arrow_safe(value)
        for key, value in record.model_dump(mode="json").items()
    }
    return {
        "batch_id": population.batch_id,
        "run_id": simulation.run_id,
        "seed": simulation.seed,
        "customer_bucket": _customer_bucket(
            simulation.customer_twin.customer_id, partition_count
        ),
        **payload,
    }


def _canonical_row_key(row: dict[str, Any]) -> str:
    return json.dumps(
        row,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _write_partitioned_rows(
    *,
    working_directory: Path,
    relative_root: Path,
    rows: list[dict[str, Any]],
    metadata: dict[str, str],
) -> dict[str, Any]:
    if not rows:
        return {"files": [], "record_count": 0, "schema_sha256": None}

    rows.sort(key=lambda row: (row["customer_bucket"], _canonical_row_key(row)))
    table = pa.Table.from_pylist(rows)
    schema_metadata = {
        key.encode("utf-8"): value.encode("utf-8")
        for key, value in sorted(metadata.items())
    }
    table = table.replace_schema_metadata(schema_metadata)
    schema_sha256 = hashlib.sha256(table.schema.serialize().to_pybytes()).hexdigest()
    indexes_by_bucket: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        indexes_by_bucket[row["customer_bucket"]].append(index)

    files: list[dict[str, Any]] = []
    for bucket, indexes in sorted(indexes_by_bucket.items()):
        partition_directory = (
            working_directory / relative_root / f"customer_bucket={bucket}"
        )
        partition_directory.mkdir(parents=True, exist_ok=True)
        path = partition_directory / "part-00000.parquet"
        partition_table = table.take(pa.array(indexes, type=pa.int64()))
        pq.write_table(
            partition_table,
            path,
            compression="zstd",
            use_dictionary=False,
            write_statistics=True,
            data_page_version="1.0",
        )
        payload = path.read_bytes()
        parquet_metadata = pq.ParquetFile(path).metadata
        if parquet_metadata.num_rows != len(indexes):
            raise OutputWriteError(f"Parquet row-count verification failed for {path}")
        files.append(
            {
                "customer_bucket": bucket,
                "path": path.relative_to(working_directory).as_posix(),
                "record_count": len(indexes),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return {
        "files": files,
        "record_count": len(rows),
        "schema_sha256": schema_sha256,
    }


def _write_simulator_datasets(
    population: GeneratedPopulation,
    working_directory: Path,
    partition_count: int,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {"observed": {}, "private": {}}
    for trust, bundle_attribute in (
        ("observed", "observations"),
        ("private", "ground_truth"),
    ):
        field_names = tuple(
            field.name
            for field in fields(getattr(population.members[0], bundle_attribute))
        )
        for dataset_name in field_names:
            rows: list[dict[str, Any]] = []
            model_names: set[str] = set()
            for member_index, member in enumerate(population.members):
                bundle = getattr(member, bundle_attribute)
                for record in getattr(bundle, dataset_name):
                    model_names.add(type(record).__name__)
                    rows.append(
                        _record_row(
                            population,
                            member_index,
                            record,
                            partition_count,
                        )
                    )
            dataset_metadata = _write_partitioned_rows(
                working_directory=working_directory,
                relative_root=Path(trust) / dataset_name,
                rows=rows,
                metadata={
                    "batch_schema_version": population.batch_schema_version,
                    "contract_schema_version": population.members[
                        0
                    ].simulation.profile.contract_schema_version,
                    "dataset": dataset_name,
                    "trust": trust,
                },
            )
            dataset_metadata["models"] = sorted(model_names)
            output[trust][dataset_name] = dataset_metadata
    return output


def _write_evaluation(
    population: GeneratedPopulation,
    working_directory: Path,
    partition_count: int,
    estimator: IncomeEstimator | Callable[[EstimatorInputV1], IncomeEstimateV1],
) -> tuple[dict[str, Any], str]:
    from finances_simulator.integration.evaluation import evaluate_population

    evaluation = evaluate_population(population, estimator)
    rows: list[dict[str, Any]] = []
    customer_id_by_run = {
        member.simulation.run_id: member.simulation.customer_twin.customer_id
        for member in population.members
    }
    seed_by_run = {
        member.simulation.run_id: member.simulation.seed
        for member in population.members
    }
    for estimate in evaluation.estimates:
        bucket = _customer_bucket(
            customer_id_by_run[estimate.run_id], partition_count
        )
        for monthly in estimate.monthly_estimates:
            rows.append(
                {
                    "batch_id": population.batch_id,
                    "run_id": estimate.run_id,
                    "seed": seed_by_run[estimate.run_id],
                    "customer_bucket": bucket,
                    "schema_version": estimate.schema_version,
                    "estimator_version": estimate.estimator_version,
                    "customer_id": estimate.customer_id,
                    "currency": estimate.currency,
                    **monthly.model_dump(mode="json"),
                }
            )
    estimates_metadata = _write_partitioned_rows(
        working_directory=working_directory,
        relative_root=Path("evaluation") / "estimates",
        rows=rows,
        metadata={
            "batch_schema_version": population.batch_schema_version,
            "dataset": "income_estimates",
            "estimator_contract_version": "1.0",
            "trust": "evaluation",
        },
    )
    report_payload = (
        json.dumps(
            evaluation.report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    report_path = working_directory / "evaluation" / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(report_payload)
    return (
        {
            "estimates": estimates_metadata,
            "report": {
                "path": report_path.relative_to(working_directory).as_posix(),
                "sha256": hashlib.sha256(report_payload).hexdigest(),
            },
        },
        evaluation.report.estimator_version,
    )


def _write_population_contents(
    population: GeneratedPopulation,
    working_directory: Path,
    partition_count: int,
    estimator: IncomeEstimator | Callable[[EstimatorInputV1], IncomeEstimateV1],
) -> None:
    for member in population.members:
        validate_generated_boundary(member)
    profiles = {member.simulation.profile for member in population.members}
    if len(profiles) != 1:
        raise OutputWriteError("Population must use one simulator/contract profile")
    profile = next(iter(profiles))
    datasets = _write_simulator_datasets(
        population, working_directory, partition_count
    )
    evaluation, estimator_version = _write_evaluation(
        population,
        working_directory,
        partition_count,
        estimator,
    )
    manifest = {
        "batch_id": population.batch_id,
        "batch_schema_version": population.batch_schema_version,
        "config_sha256": population.config_sha256,
        "contract_schema_version": profile.contract_schema_version,
        "datasets": datasets,
        "estimator_contract_version": "1.0",
        "estimator_version": estimator_version,
        "evaluation": evaluation,
        "months": population.months,
        "partitioning": {
            "algorithm": "sha256-customer-id-modulo",
            "column": "customer_bucket",
            "partition_count": partition_count,
        },
        "population_size": population.population_size,
        "rng_algorithm": profile.rng_algorithm,
        "run_ids": [member.simulation.run_id for member in population.members],
        "seeds": list(population.seeds),
        "simulator_version": population.simulator_version,
        "source_simulator_version": profile.simulator_version,
    }
    manifest_path = working_directory / "population_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_population(
    population: GeneratedPopulation,
    output_directory: Path,
    *,
    partition_count: int = 16,
    estimator: IncomeEstimator
    | Callable[[EstimatorInputV1], IncomeEstimateV1]
    | None = None,
) -> Path:
    """Write population, estimates, and report atomically; return manifest path."""

    if (
        isinstance(partition_count, bool)
        or not isinstance(partition_count, int)
        or not 1 <= partition_count <= 256
    ):
        raise ValueError("partition_count must be an integer between 1 and 256")
    if estimator is None:
        from finances_simulator.integration.baseline import BaselineIncomeEstimator

        estimator = BaselineIncomeEstimator()

    staging_directory: Path | None = None
    removed_empty_destination = False
    output_directory = output_directory.resolve()
    try:
        _reject_occupied_destination(output_directory)
        output_directory.parent.mkdir(parents=True, exist_ok=True)
        staging_directory = Path(
            tempfile.mkdtemp(
                prefix=f".{output_directory.name}.staging-",
                dir=output_directory.parent,
            )
        )
        _write_population_contents(
            population,
            staging_directory,
            partition_count,
            estimator,
        )
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
    except Exception as error:
        if staging_directory is not None:
            shutil.rmtree(staging_directory, ignore_errors=True)
        if removed_empty_destination and not output_directory.exists():
            try:
                output_directory.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        if isinstance(error, OutputWriteError):
            raise
        raise OutputWriteError(
            f"Unable to write population output '{output_directory}': {error}"
        ) from error

    return output_directory / "population_manifest.json"


__all__ = ["write_population"]
