"""Command-line JSON adapter for estimator contract 1.0."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from income_estimator.pipeline import RuleBasedIncomeEstimator


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="income-estimator")
    parser.add_argument("input", type=Path, help="EstimatorInputV1 JSON file")
    parser.add_argument("--audit", action="store_true", help="Emit internal decisions and streams")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        estimator = RuleBasedIncomeEstimator()
        result = estimator.explain(payload) if args.audit else estimator.estimate(payload)
    except (OSError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
