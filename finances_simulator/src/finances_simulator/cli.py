"""Command-line interface for deterministic scenario generation."""

import argparse
import importlib
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from finances_simulator.batch import generate_population, write_population
from finances_simulator.config import ConfigurationError, load_scenario_config
from finances_simulator.generation import generate_scenario
from finances_simulator.integration import BaselineIncomeEstimator
from finances_simulator.outputs import OutputDirectoryNotEmptyError, OutputWriteError, write_run
from finances_simulator.simulation.primitives import SIMULATOR_VERSION
from finances_simulator.validation import InvariantViolation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finances-simulator",
        description="Generate deterministic synthetic financial histories.",
    )
    parser.add_argument("--version", action="version", version=SIMULATOR_VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Generate one scenario run.")
    generate_parser.add_argument("--config", required=True, type=Path, help="Scenario YAML file.")
    generate_parser.add_argument("--seed", required=True, type=int, help="Deterministic seed.")
    generate_parser.add_argument(
        "--months",
        type=int,
        help="Number of months; defaults to scenario configuration.",
    )
    generate_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New or empty output directory.",
    )

    batch_parser = subparsers.add_parser(
        "generate-batch",
        help="Generate a deterministic population as partitioned Parquet.",
    )
    batch_parser.add_argument("--config", required=True, type=Path, help="Scenario YAML file.")
    batch_parser.add_argument(
        "--seed",
        required=True,
        type=int,
        help="First deterministic member seed; later members use consecutive seeds.",
    )
    batch_parser.add_argument(
        "--population-size",
        required=True,
        type=int,
        help="Number of synthetic customers.",
    )
    batch_parser.add_argument(
        "--months",
        type=int,
        help="Number of months; defaults to scenario configuration.",
    )
    batch_parser.add_argument(
        "--workers",
        type=int,
        help="Parallel worker count; defaults to available CPUs capped by population size.",
    )
    batch_parser.add_argument(
        "--partitions",
        type=int,
        default=16,
        help="Deterministic customer-bucket count (default: 16).",
    )
    batch_parser.add_argument(
        "--estimator",
        default="baseline",
        help=(
            "Estimator as 'module:attribute', or 'baseline' for the bundled auditable "
            "reference estimator."
        ),
    )
    batch_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New or empty population output directory.",
    )
    return parser


def _load_estimator(specification: str):
    if specification == "baseline":
        return BaselineIncomeEstimator()
    module_name, separator, attribute_name = specification.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("--estimator must be 'baseline' or 'module:attribute'")
    module = importlib.import_module(module_name)
    component = getattr(module, attribute_name)
    if isinstance(component, type):
        component = component()
    if not callable(component) and not callable(getattr(component, "estimate", None)):
        raise TypeError("configured estimator must be callable or expose estimate(request)")
    return component


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_scenario_config(args.config)
        if args.command == "generate-batch":
            population = generate_population(
                config,
                population_size=args.population_size,
                seed=args.seed,
                months=args.months,
                workers=args.workers,
            )
            estimator = _load_estimator(args.estimator)
            manifest_path = write_population(
                population,
                args.output,
                partition_count=args.partitions,
                estimator=estimator,
            )
        else:
            generated = generate_scenario(config, seed=args.seed, months=args.months)
            manifest_path = write_run(generated, args.output)
    except (
        ConfigurationError,
        OutputDirectoryNotEmptyError,
        OutputWriteError,
        InvariantViolation,
        ValidationError,
        ValueError,
        ImportError,
        AttributeError,
        TypeError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.command == "generate-batch":
        print(f"Generated population {population.batch_id} ({population.population_size} members)")
        print(f"Manifest: {manifest_path}")
        print(f"Evaluation report: {manifest_path.parent / 'evaluation' / 'report.json'}")
    else:
        print(f"Generated run {generated.simulation.run_id}")
        print(f"Manifest: {manifest_path}")
        print(f"Observed data: {manifest_path.parent / 'observed'}")
        print(f"Private ground truth: {manifest_path.parent / 'private'}")
    return 0
