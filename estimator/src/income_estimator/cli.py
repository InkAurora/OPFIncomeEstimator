"""Command-line JSON adapter for estimator input contracts 1.0, 1.1, and 1.2.

Two surfaces, deliberately separated.

``--bundle`` is the production path: one verified, immutable directory in, one result stamped with
that bundle's digest out. It cannot be combined with the artifact flags, because the whole point of
a bundle is that the pairing was decided and checked in advance rather than assembled on a command
line.

The individual artifact flags are the research path. They accept any combination the runtime will
tolerate, including none at all, which is what makes them useful for comparing candidates and wrong
for a deployment: they answer with a weaker component rather than refusing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from income_estimator.features import build_customer_month_features
from income_estimator.pipeline import (
    EnsembleIncomeEstimator,
    RecurringIncomeEstimator,
    RuleBasedIncomeEstimator,
    SupervisedIncomeEstimator,
)
from income_estimator.production import ProductionIncomeEstimator

_RESEARCH_FLAGS = (
    ("--ensemble", "ensemble"),
    ("--capacity-model", "capacity_model"),
    ("--calibration", "calibration"),
    ("--model", "model"),
    ("--baseline-0.1", "baseline_0_1"),
    ("--features", "features"),
    ("--audit", "audit"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="income-estimator")
    parser.add_argument("input", type=Path, help="Estimator input 1.0, 1.1, or 1.2 JSON file")
    parser.add_argument(
        "--bundle",
        type=Path,
        help=(
            "Production: load the promoted estimator from a verified bundle directory and emit "
            "production result 1.0. Combine only with --explain"
        ),
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Emit the explanation contract 1.0 evidence report; implies --ensemble",
    )
    research = parser.add_argument_group(
        "research and debug",
        "Loose artifacts for candidate comparison. Never use these to deploy: an absent or "
        "unloadable artifact degrades the estimate instead of refusing it.",
    )
    research.add_argument(
        "--audit", action="store_true", help="Emit internal decisions and streams"
    )
    research.add_argument(
        "--baseline-0.1",
        dest="baseline_0_1",
        action="store_true",
        help="Use coverage-scaled rule baseline instead of recurring-stream estimator",
    )
    research.add_argument(
        "--features",
        action="store_true",
        help="Emit the point-in-time customer-month feature table instead of an estimate",
    )
    research.add_argument(
        "--ensemble",
        action="store_true",
        help="Use estimator 0.6 routing and emit output contract 1.1",
    )
    research.add_argument(
        "--capacity-model",
        type=Path,
        help="Capacity model artifact for --ensemble sustainable income",
    )
    research.add_argument(
        "--calibration",
        type=Path,
        help="Conformal calibration artifact for --ensemble sustainable income quantiles",
    )
    research.add_argument(
        "--model",
        type=Path,
        help="Use experimental estimator 0.3 with this JSON model artifact",
    )
    return parser


def _reject_mixed_surfaces(args: argparse.Namespace) -> None:
    conflicting = [flag for flag, attribute in _RESEARCH_FLAGS if getattr(args, attribute)]
    if conflicting:
        raise ValueError(
            f"--bundle is the production path and cannot be combined with {', '.join(conflicting)};"
            " use --explain for the evidence report, or drop --bundle to load loose artifacts"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if args.bundle is not None:
            _reject_mixed_surfaces(args)
            production = ProductionIncomeEstimator.from_bundle(args.bundle)
            result = (
                production.explain_production(payload)
                if args.explain
                else production.estimate_production(payload)
            )
        else:
            if args.ensemble or args.explain:
                estimator = EnsembleIncomeEstimator(
                    args.capacity_model,
                    calibration_path=args.calibration,
                )
            elif args.model is not None:
                estimator = SupervisedIncomeEstimator(args.model)
            elif args.baseline_0_1:
                estimator = RuleBasedIncomeEstimator()
            else:
                estimator = RecurringIncomeEstimator()
            if args.features:
                result = build_customer_month_features(payload, estimator)
            elif args.explain:
                result = estimator.explain_estimate(payload)
            elif args.ensemble:
                result = estimator.estimate_v1_1(payload)
            elif args.audit:
                result = estimator.explain(payload)
            else:
                result = estimator.estimate(payload)
    except (OSError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
