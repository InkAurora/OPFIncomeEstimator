"""Command-line JSON adapter for estimator input contracts 1.0 and 1.1."""

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="income-estimator")
    parser.add_argument("input", type=Path, help="Estimator input 1.0 or 1.1 JSON file")
    parser.add_argument("--audit", action="store_true", help="Emit internal decisions and streams")
    parser.add_argument(
        "--baseline-0.1",
        dest="baseline_0_1",
        action="store_true",
        help="Use coverage-scaled rule baseline instead of recurring-stream estimator",
    )
    parser.add_argument(
        "--features",
        action="store_true",
        help="Emit the point-in-time customer-month feature table instead of an estimate",
    )
    parser.add_argument(
        "--ensemble",
        action="store_true",
        help="Use estimator 0.6 routing and emit output contract 1.1",
    )
    parser.add_argument(
        "--capacity-model",
        type=Path,
        help="Capacity model artifact for --ensemble sustainable income",
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        help="Conformal calibration artifact for --ensemble sustainable income quantiles",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Emit the explanation contract 1.0 evidence report; implies --ensemble",
    )
    parser.add_argument(
        "--model",
        type=Path,
        help="Use experimental estimator 0.3 with this JSON model artifact",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
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
