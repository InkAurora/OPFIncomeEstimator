"""Command-line interface for deterministic scenario generation."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from finances_simulator.config import ConfigurationError, load_scenario_config
from finances_simulator.generation import generate_scenario
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_scenario_config(args.config)
        generated = generate_scenario(config, seed=args.seed, months=args.months)
        manifest_path = write_run(generated, args.output)
    except (
        ConfigurationError,
        OutputDirectoryNotEmptyError,
        OutputWriteError,
        InvariantViolation,
        ValidationError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"Generated run {generated.simulation.run_id}")
    print(f"Manifest: {manifest_path}")
    print(f"Observed data: {manifest_path.parent / 'observed'}")
    print(f"Private ground truth: {manifest_path.parent / 'private'}")
    return 0
