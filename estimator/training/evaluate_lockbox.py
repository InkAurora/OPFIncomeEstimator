"""Measure a frozen calibration artifact against the release lockbox. Read once.

Seeds `510_000`-`530_000` have been read across several method-selection rounds: a width-slope
candidate, a conditioner scan, a cell selector. Every look spends some of their independence, and
no amount of care gives it back. They are validation, and this is not them.

The lockbox is drawn from `RELEASE_LOCKBOX_SEED_FLOOR` upward and has never been generated before
this run. Nothing here fits anything. The artifact is loaded, its fixed-band comparator is
reconstructed from its own frozen offsets, and the same gates that judged validation are applied
unchanged. If it fails, the honest response is to say so, not to adjust and look again — a lockbox
read twice is a validation set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from income_estimator.models.capacity import GradientBoostedCapacityModel
from income_estimator.models.quantiles import (
    ConformalIntervalModel,
    confidence_band,
    require_capacity_binding,
)
from training.calibrate_quantiles import (
    CAPACITY_DATASET_VERSION,
    DEFAULT_LOWER_QUANTILE,
    DEFAULT_UPPER_QUANTILE,
    RELEASE_LOCKBOX_SEED_FLOOR,
    SHARPNESS_NONINFERIORITY_MARGIN,
    _coverage_metrics,
    _paired_rows,
    _paired_statistics,
    _populations,
    _routed,
    _segmented,
    _sharpness_failure,
    _tail_failures,
    _undercoverage_failure,
    _width_allocation,
)

LOCKBOX_SUITES = (
    ("income_diverse.yaml", RELEASE_LOCKBOX_SEED_FLOOR),
    ("life_events.yaml", RELEASE_LOCKBOX_SEED_FLOOR + 10_000),
    ("incomplete_observation.yaml", RELEASE_LOCKBOX_SEED_FLOOR + 20_000),
)


def fixed_band_comparator(artifact) -> ConformalIntervalModel:
    """The fixed-band model the sharpness gate measures against, from the artifact's own offsets.

    Rebuilt rather than stored. The comparator is fully determined by the band offsets the artifact
    already carries, so reconstructing it here reproduces exactly the model validation compared
    against, with no second file to drift.
    """

    return ConformalIntervalModel(
        artifact.model_copy(
            update={
                "calibration_version": f"{artifact.calibration_version}-fixed-band-baseline",
                "residual_quantiles": None,
                "conformal_widening": None,
                "band_adjustments": {},
                "width_recalibrator": None,
                "conditional_selector": None,
            }
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument(
        "--capacity-model",
        type=Path,
        default=Path(__file__).parent / "artifacts/capacity-estimator-0.6.0.json",
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=Path(__file__).parent / "artifacts/quantile-calibration-0.11.0.json",
    )
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "artifacts")
    parser.add_argument("--population-size-per-suite", type=int, default=240)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)

    capacity_path = args.capacity_model.resolve()
    calibration_path = args.calibration.resolve()
    capacity = GradientBoostedCapacityModel.from_path(capacity_path)
    intervals = ConformalIntervalModel.from_path(calibration_path)
    artifact = intervals.artifact

    # The same binding the runtime enforces. A lockbox measured with the wrong capacity bytes would
    # be evidence about a pair that never ships.
    require_capacity_binding(
        artifact,
        capacity_model_version=capacity.artifact.model_version,
        capacity_artifact_sha256=capacity.artifact_sha256,
    )
    baseline = fixed_band_comparator(artifact)

    by_suite = _populations(
        args.project_root.resolve(),
        LOCKBOX_SUITES,
        args.population_size_per_suite,
        args.months,
        args.workers,
    )
    rows = tuple(row for items in by_suite.values() for row in items)
    nominal = DEFAULT_UPPER_QUANTILE - DEFAULT_LOWER_QUANTILE
    nominal_miss = DEFAULT_LOWER_QUANTILE

    failures: list[str] = []
    overall = _coverage_metrics(rows, capacity, intervals)
    overall_gate, overall_failure = _undercoverage_failure("published", overall, nominal)
    if overall_failure:
        failures.append(overall_failure)
    overall_tails, overall_tail_failures = _tail_failures("published", overall, nominal_miss)
    failures.extend(overall_tail_failures)

    published = int(overall.get("count") or 0)
    refused = int(overall.get("out_of_support_count") or 0)
    unestimated = int(overall.get("no_point_estimate_count") or 0)
    if published + refused + unestimated != len(rows):
        failures.append(
            f"{published} of {len(rows)} lockbox rows receive an interval; complete promotion "
            f"requires every supported row to publish ({overall.get('withheld_count')} withheld, "
            f"{refused} out of support, {unestimated} without a point estimate)"
        )

    bands = _segmented(
        rows,
        capacity,
        intervals,
        lambda row: confidence_band(_routed(row, capacity).confidence_score_basis_points),
    )
    band_gate: dict[str, dict[str, object]] = {}
    band_tails: dict[str, dict[str, object]] = {}
    for band, metrics in sorted(bands.items()):
        gate, failure = _undercoverage_failure(f"{band}-confidence", metrics, nominal)
        band_gate[band] = gate
        if failure:
            failures.append(failure)
        tails, tail_failures = _tail_failures(f"{band}-confidence", metrics, nominal_miss)
        band_tails[band] = tails
        failures.extend(tail_failures)

    suite_gate: dict[str, dict[str, object]] = {}
    sharpness: dict[str, dict[str, object]] = {}
    paired_by_suite: dict[str, tuple] = {}
    for scenario, items in sorted(by_suite.items()):
        metrics = _coverage_metrics(items, capacity, intervals)
        gate, failure = _undercoverage_failure(scenario, metrics, nominal)
        gate["mean_interval_width_minor"] = metrics.get("mean_interval_width_minor")
        gate["mean_interval_score_minor"] = metrics.get("mean_interval_score_minor")
        gate["wape"] = metrics.get("wape")
        if failure:
            failures.append(failure)
        tails, tail_failures = _tail_failures(scenario, metrics, nominal_miss)
        gate["tails"] = tails
        failures.extend(tail_failures)
        suite_gate[scenario] = gate

        baseline_metrics = _coverage_metrics(items, capacity, baseline)
        paired_rows, unpaired = _paired_rows(items, capacity, intervals, baseline)
        paired_by_suite[scenario] = paired_rows
        paired = (
            {**_paired_statistics(paired_rows), "unpaired_row_count": unpaired}
            if paired_rows
            else {"paired_row_count": 0, "unpaired_row_count": unpaired}
        )
        baseline_tails, _ = _tail_failures(f"{scenario} baseline", baseline_metrics, nominal_miss)
        sharp_gate, sharp_failure = _sharpness_failure(
            scenario,
            metrics,
            baseline_metrics,
            paired,
            baseline_tails,
            baseline_calibration=baseline.artifact.calibration_version,
            gated=bool(gate.get("gated")),
        )
        sharpness[scenario] = sharp_gate
        if sharp_failure:
            failures.append(sharp_failure)

    zero_truth = _coverage_metrics(
        tuple(row for row in rows if row.sustainable_monthly_income_minor == 0),
        capacity,
        intervals,
    )
    zero_gate, zero_failure = _undercoverage_failure("zero-truth", zero_truth, nominal)
    if zero_failure:
        failures.append(zero_failure)

    status = "RELEASE_CONFIRMED" if not failures else "RELEASE_BLOCKED"
    report = {
        "schema_version": "1.0",
        "population": "release-lockbox",
        "read_once": True,
        "calibration_version": artifact.calibration_version,
        "artifact_schema_version": artifact.schema_version,
        "artifact_sha256": hashlib.sha256(calibration_path.read_bytes()).hexdigest(),
        "capacity_model_version": capacity.artifact.model_version,
        "capacity_artifact_sha256": capacity.artifact_sha256,
        "dataset_version": CAPACITY_DATASET_VERSION,
        "population_size_per_suite": args.population_size_per_suite,
        "months": args.months,
        "suites": [{"scenario": scenario, "seed": seed} for scenario, seed in LOCKBOX_SUITES],
        "row_count": len(rows),
        "published_rows": published,
        "out_of_support_rows": refused,
        "customer_count": int(overall.get("customer_count") or 0),
        "nominal_coverage": nominal,
        "nominal_tail_miss_rate": nominal_miss,
        "sharpness_noninferiority_margin": SHARPNESS_NONINFERIORITY_MARGIN,
        "overall": overall_gate,
        "overall_tails": overall_tails,
        "by_confidence_band": band_gate,
        "by_confidence_band_tails": band_tails,
        "by_suite": suite_gate,
        "sharpness": sharpness,
        "zero_truth": zero_gate,
        "width_allocation": {
            "overall": _width_allocation(
                tuple(item for items in paired_by_suite.values() for item in items)
            ),
        },
        "failures": failures,
        "status": status,
    }

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"lockbox-{artifact.calibration_version}-report.json"
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"Lockbox report: {path}")
    print(
        f"{artifact.calibration_version} on seeds "
        f"{RELEASE_LOCKBOX_SEED_FLOOR}+, never read before"
    )
    print(
        f"Published coverage: {overall_gate.get('empirical_coverage')} "
        f"floor {overall_gate.get('floor')} on {published}/{len(rows)} rows"
    )
    print(
        f"  tails lower={overall_tails['lower']['miss_rate']:.4f} "
        f"upper={overall_tails['upper']['miss_rate']:.4f} nominal {nominal_miss}"
    )
    for band, gate in band_gate.items():
        tails = band_tails[band]
        print(
            f"  {band:<7}coverage={gate['empirical_coverage']:.4f} floor={gate['floor']:.4f} "
            f"lower={tails['lower']['miss_rate']:.4f}/{tails['lower']['ceiling']:.4f} "
            f"upper={tails['upper']['miss_rate']:.4f}/{tails['upper']['ceiling']:.4f}"
        )
    for scenario, gate in suite_gate.items():
        sharp = sharpness[scenario]
        tails = gate["tails"]
        print(
            f"  {scenario:<28} coverage={gate['empirical_coverage']:.4f} "
            f"floor={gate['floor']:.4f} width={gate['mean_interval_width_minor']} "
            f"lower={tails['lower']['miss_rate']:.4f} upper={tails['upper']['miss_rate']:.4f}"
        )
        print(
            f"  {'':28} sharpness bound="
            f"{sharp['paired']['difference_upper_confidence_bound_minor']} "
            f"margin={sharp['noninferiority_margin_minor']} passed={sharp['passed']}"
        )
    print(f"Lockbox: {status}")
    for failure in failures:
        print(f"  - {failure}")
    return 0 if status == "RELEASE_CONFIRMED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
