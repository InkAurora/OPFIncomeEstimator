"""Generate frozen estimator 0.2 held-out artifacts."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from evaluation.benchmark import (
    default_suites,
    render_true_vs_estimated_svg,
    run_benchmark,
    write_report,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "baselines")
    parser.add_argument("--population-size", type=int, default=100)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)

    report, chart_points = run_benchmark(
        default_suites(args.project_root.resolve(), args.population_size),
        workers=args.workers,
    )
    output = args.output.resolve()
    write_report(report, output / "estimator-0.2-heldout-report.json")
    render_true_vs_estimated_svg(
        chart_points["incomplete_observation"],
        output / "estimator-0.2-true-vs-estimated.svg",
        suite_name="incomplete_observation",
    )
    print(f"Report: {output / 'estimator-0.2-heldout-report.json'}")
    print(f"Chart: {output / 'estimator-0.2-true-vs-estimated.svg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
