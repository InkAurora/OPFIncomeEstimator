"""Pre-register the conditioning feature for the cell selector, inside uncertainty-training only.

The width-slope recalibrator failed because the regimes overlap in raw width: at the same learned
width an `income_diverse` row needs more of it and a `life_events` row needs less, and a monotone
function of that width cannot tell them apart. The remedy is to condition the correction on
something that can. Which feature that is has to be chosen somewhere, and where it is chosen decides
whether the answer means anything.

Ranking features against the final-test population was tried and is disqualifying: it selects a
model on the data the gate then measures, so the gate stops being a test of anything. This module
ranks them inside the uncertainty-training population instead, seeds `210_000`+, which no gate ever
reads. **It must never load a final-test population, and it does not.**

The criterion is per-suite worst tail miss, because that is what the gate judges. That makes the
*selection* suite-aware while the *model* stays suite-agnostic: the artifact this eventually
produces carries a feature name, cut points, and per-cell corrections, and has no way to ask which
scenario a row came from.

Two levels of splitting, both over customers:

- outer, to get honest learned bands. The residual quantile model is refitted once per fold and
  predicts only on customers it did not see, because a band the model produced for its own training
  rows is optimistic about exactly the rows the correction then has to cover;
- inner, to get honest corrections. Cells fit their tail corrections on half the customers and are
  scored on the other half, which is what split-conformal does at calibration time. Repeated over
  several seeds, because one split is one draw.

Selection is on the **worst** seed rather than the median, tie-broken on the median. A conditioner
that only holds on a lucky split is not a conditioner.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean

from income_estimator.features.schema import FEATURE_NAMES
from income_estimator.models.capacity import GradientBoostedCapacityModel
from income_estimator.models.quantiles import (
    ConformalIntervalModel,
    confidence_band,
    empirical_quantile,
)
from income_estimator.models.uncertainty import ResidualQuantileModel
from training.calibrate_quantiles import (
    DEFAULT_LOWER_QUANTILE,
    DEFAULT_UPPER_QUANTILE,
    UNCERTAINTY_SUITES,
    WIDTH_RECALIBRATION_FOLDS,
    _populations,
    _routed,
)
from training.out_of_fold import customer_fold
from training.uncertainty_boosting import fit_residual_quantile_model, residual_rows

SELECTION_VERSION = "conditioner-preregistration-1.0"

BRANCHES: tuple[str, ...] = ("adaptive", "fixed")

# Quartiles of the conditioner, crossed with the confidence band. Four buckets is the coarsest cut
# that can express "low, middling, high, extreme" and still leave every cell enough customers to
# fit its own pair on.
CONDITIONER_QUANTILES: tuple[float, ...] = (0.25, 0.5, 0.75)

# A cell below this many rows on either side of the inner split is not judged; it cannot fit a
# correction that means anything, and counting it would reward features that shatter the population.
MINIMUM_CELL_ROWS = 40

INNER_SPLIT_SEEDS = 10


@dataclass(frozen=True, slots=True)
class SelectionRow:
    """One out-of-fold learned band, the fixed band, and the residual both had to bracket."""

    customer_id: str
    suite: str
    band: str
    features: dict[str, float | int | None]
    log_residual: float
    adaptive: tuple[float, float]
    fixed: tuple[float, float]

    def conformity(self, branch: str, tail: str) -> float:
        """How far outside this branch's band the residual fell, positive when outside."""

        lower, upper = getattr(self, branch)
        return lower - self.log_residual if tail == "lower" else self.log_residual - upper

    def corrected_width(self, branch: str, lower_correction: float, upper_correction: float):
        lower, upper = getattr(self, branch)
        return (upper + upper_correction) - (lower - lower_correction)


def build_selection_rows(
    project_root: Path,
    capacity: GradientBoostedCapacityModel,
    fixed_offsets,
    *,
    population_size: int,
    months: int,
    workers: int,
) -> tuple[SelectionRow, ...]:
    """Out-of-fold learned bands on the uncertainty population, beside the fixed band."""

    by_suite = _populations(project_root, UNCERTAINTY_SUITES, population_size, months, workers)
    routed_by_key = {}
    suite_by_key = {}
    for suite, rows in by_suite.items():
        for row in rows:
            key = (row.customer_id, row.reference_month)
            routed_by_key[key] = _routed(row, capacity)
            suite_by_key[key] = suite

    residuals = residual_rows(
        tuple(row for rows in by_suite.values() for row in rows),
        lambda row: routed_by_key[
            (row.customer_id, row.reference_month)
        ].sustainable_income_minor,
    )

    selection: list[SelectionRow] = []
    for fold in range(WIDTH_RECALIBRATION_FOLDS):
        training = [
            row
            for row in residuals
            if customer_fold(row.customer_id, WIDTH_RECALIBRATION_FOLDS) != fold
        ]
        held_out = [
            row
            for row in residuals
            if customer_fold(row.customer_id, WIDTH_RECALIBRATION_FOLDS) == fold
        ]
        if not training or not held_out:
            continue
        model = ResidualQuantileModel(
            fit_residual_quantile_model(
                training,
                lower_quantile=DEFAULT_LOWER_QUANTILE,
                upper_quantile=DEFAULT_UPPER_QUANTILE,
            )
        )
        for row in held_out:
            key = (row.customer_id, row.reference_month)
            score = routed_by_key[key].confidence_score_basis_points
            selection.append(
                SelectionRow(
                    customer_id=row.customer_id,
                    suite=suite_by_key[key],
                    band=confidence_band(score),
                    features=row.features,
                    log_residual=row.log_residual,
                    adaptive=model.predict_bounds(row.features),
                    fixed=fixed_offsets(score),
                )
            )
    return tuple(selection)


def _cut_points(rows: Sequence[SelectionRow], name: str) -> tuple[float, ...] | None:
    values = [
        float(row.features[name]) for row in rows if row.features.get(name) is not None
    ]
    if len(values) < len(rows) // 2 or len(set(values)) < 4:
        return None
    cuts = tuple(empirical_quantile(values, quantile) for quantile in CONDITIONER_QUANTILES)
    return cuts if len(set(cuts)) == len(cuts) else None


def _cell(row: SelectionRow, name: str, cuts: Sequence[float]) -> tuple[str, str]:
    value = row.features.get(name)
    bucket = (
        "unknown" if value is None else f"q{sum(1 for cut in cuts if float(value) > cut) + 1}"
    )
    return bucket, row.band


def _corrections(rows: Sequence[SelectionRow], branch: str) -> tuple[float, float]:
    """One cell's two tail corrections, each the `0.90` quantile of its own conformity scores."""

    return tuple(
        empirical_quantile([row.conformity(branch, tail) for row in rows], DEFAULT_UPPER_QUANTILE)
        for tail in ("lower", "upper")
    )


def choose_branch(fitted: Sequence[SelectionRow]) -> str:
    """Pick the branch whose corrected band is narrower on the rows it was fitted on.

    Both branches are corrected to hold their tails first, so this compares like with like: what is
    left is width, which is the sharpness pressure the interval score applies later. Chosen on the
    fitting half alone, so the selector never reads the rows it is judged against.
    """

    def cost(branch: str) -> float:
        lower_correction, upper_correction = _corrections(fitted, branch)
        return fmean(
            row.corrected_width(branch, lower_correction, upper_correction) for row in fitted
        )

    return min(BRANCHES, key=cost)


def _worst_suite_tail_miss(
    rows: Sequence[SelectionRow],
    name: str,
    cuts: Sequence[float],
    seed: int,
) -> float | None:
    """Worst per-suite tail miss under an honest inner customer split, or `None` if unjudgeable."""

    customers = sorted({row.customer_id for row in rows})
    rng = random.Random(seed)
    rng.shuffle(customers)
    fitting = set(customers[: len(customers) // 2])

    fit_cells: dict[tuple[str, str], list[SelectionRow]] = {}
    score_cells: dict[tuple[str, str], list[SelectionRow]] = {}
    for row in rows:
        target = fit_cells if row.customer_id in fitting else score_cells
        target.setdefault(_cell(row, name, cuts), []).append(row)

    totals: dict[tuple[str, str], list[int]] = {}
    judged = 0
    for cell, scored in score_cells.items():
        fitted = fit_cells.get(cell)
        if not fitted or len(fitted) < MINIMUM_CELL_ROWS or len(scored) < MINIMUM_CELL_ROWS:
            continue
        judged += 1
        branch = choose_branch(fitted)
        corrections = dict(zip(("lower", "upper"), _corrections(fitted, branch)))
        for row in scored:
            for tail, correction in corrections.items():
                entry = totals.setdefault((row.suite, tail), [0, 0])
                entry[0] += row.conformity(branch, tail) > correction
                entry[1] += 1
    if not judged or not totals:
        return None
    return max(missed / count for missed, count in totals.values())


def rank_conditioners(rows: Sequence[SelectionRow]) -> list[dict[str, object]]:
    """Every usable feature, worst-seed first. Selection is on the worst seed, not the median."""

    results: list[dict[str, object]] = []
    for name in FEATURE_NAMES:
        cuts = _cut_points(rows, name)
        if cuts is None:
            continue
        misses = [
            _worst_suite_tail_miss(rows, name, cuts, seed) for seed in range(INNER_SPLIT_SEEDS)
        ]
        judged = [value for value in misses if value is not None]
        if len(judged) < INNER_SPLIT_SEEDS:
            continue
        results.append(
            {
                "feature": name,
                "cut_points": [round(cut, 6) for cut in cuts],
                "worst_seed_tail_miss": round(max(judged), 6),
                "median_seed_tail_miss": round(statistics.median(judged), 6),
                "best_seed_tail_miss": round(min(judged), 6),
            }
        )
    results.sort(key=lambda item: (item["worst_seed_tail_miss"], item["median_seed_tail_miss"]))
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument(
        "--capacity-model",
        type=Path,
        default=Path(__file__).parent / "artifacts/capacity-estimator-0.6.0.json",
    )
    parser.add_argument(
        "--reference-calibration",
        type=Path,
        default=Path(__file__).parent / "artifacts/quantile-calibration-0.9.0.json",
        help="Supplies the fixed-band offsets the selector may choose instead of the learned band",
    )
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "artifacts")
    parser.add_argument("--population-size-per-suite", type=int, default=240)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)

    capacity = GradientBoostedCapacityModel.from_path(args.capacity_model.resolve())
    reference = ConformalIntervalModel.from_path(args.reference_calibration.resolve()).artifact

    rows = build_selection_rows(
        args.project_root.resolve(),
        capacity,
        reference.offsets_for,
        population_size=args.population_size_per_suite,
        months=args.months,
        workers=args.workers,
    )
    ranking = rank_conditioners(rows)
    if not ranking:
        raise ValueError("no feature is usable as a conditioner on this population")
    chosen = ranking[0]

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / "conditioner-preregistration.json"
    record = {
        "schema_version": "1.0",
        "selection_version": SELECTION_VERSION,
        "selected_on": "uncertainty-training",
        "selection_suites": [
            {"scenario": scenario, "seed": seed} for scenario, seed in UNCERTAINTY_SUITES
        ],
        "final_test_inspected": False,
        "criterion": "worst-seed per-suite tail miss under an honest inner customer split",
        "inner_split_seeds": INNER_SPLIT_SEEDS,
        "outer_folds": WIDTH_RECALIBRATION_FOLDS,
        "conditioner_quantiles": list(CONDITIONER_QUANTILES),
        "minimum_cell_rows": MINIMUM_CELL_ROWS,
        "row_count": len(rows),
        "customer_count": len({row.customer_id for row in rows}),
        "selected": chosen,
        "ranking": ranking,
    }
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Pre-registration: {path}")
    print(f"{len(rows)} out-of-fold rows on {record['customer_count']} uncertainty customers")
    print(f"{'feature':<44} {'worst':>8} {'median':>8} {'best':>8}")
    print("-" * 72)
    for item in ranking[:12]:
        print(
            f"{item['feature']:<44} {item['worst_seed_tail_miss']:>8.4f} "
            f"{item['median_seed_tail_miss']:>8.4f} {item['best_seed_tail_miss']:>8.4f}"
        )
    print()
    print(f"Selected: {chosen['feature']} at cuts {chosen['cut_points']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
