"""Fit conformal intervals for the routed estimate and measure them on untouched customers.

ADR 0006 repairs three defects in the protocol this script used to implement.

The frozen capacity model is calibrated directly. Its training population and the calibration
population are customer-disjoint, so its residuals here are already out of sample and the per-fold
refitting ADR 0003 introduced was solving a leakage problem that did not exist. Worse, it solved it
by calibrating models trained on roughly three times the shipped model's data.

Residuals are taken around the estimate `combine_month` actually publishes, including its routing
choice, because an interval is a claim about the number the product shows.

Coverage is gated one-sided. Under-coverage understates risk and is a failure; exceeding nominal is
not, and on a suite whose point estimate is often exact no interval width can avoid it. Width is
judged separately by the Winkler interval score, which is what keeps a one-sided gate honest.

ADR 0007 makes promotion mean complete promotion. The artifact publishes every band
unconditionally and the final test decides only whether that artifact promotes, so a band can no
longer buy a passing gate by withholding itself. Each band corrects each tail of its learned
quantile pair on its own scores, which is what lets the lower bound be a `p10` claim and the upper
bound a `p90` claim rather than two halves of one `80%` claim that either tail may be paying for.
Sharpness is gated against the fixed-band conformal model measured on the same final rows, so
coverage bought by widening cannot pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections.abc import Sequence
from pathlib import Path
from statistics import fmean, median

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config

from income_estimator.models.capacity import GradientBoostedCapacityModel
from income_estimator.models.ensemble import combine_month
from income_estimator.models.quantiles import (
    CALIBRATION_METHOD,
    CONFIDENCE_BAND_FLOORS,
    BandAdjustment,
    BandOffsets,
    ConformalCalibrationArtifact,
    ConformalIntervalModel,
    confidence_band,
    empirical_quantile,
)
from income_estimator.models.uncertainty import ResidualQuantileModel
from training.capacity_datasets import (
    CAPACITY_DATASET_VERSION,
    build_capacity_dataset,
)
from training.uncertainty_boosting import (
    conformal_tail_adjustment,
    conformal_widening,
    conformity_scores,
    fit_residual_quantile_model,
    residual_rows,
    tail_conformity_scores,
)

CALIBRATION_VERSION = "adaptive-intervals-0.9.0"
ARTIFACT_STEM = "quantile-calibration-0.9.0"
DEFAULT_LOWER_QUANTILE = 0.1
DEFAULT_UPPER_QUANTILE = 0.9
ZERO_GATE_CERTAIN_BASIS_POINTS = 1_000
COVERAGE_TOLERANCE = 0.05

# ADR 0007. Each tail carries half the interval's miss budget, so it is gated at half the interval's
# base tolerance. Stated separately from `COVERAGE_TOLERANCE` because a joint coverage figure can
# sit inside its floor while one tail misses twice as often as it claims to.
TAIL_MISS_TOLERANCE = COVERAGE_TOLERANCE / 2

# ADR 0005. A band fits its own offsets only when its tail quantiles rest on enough observations to
# mean something; below this it falls back to the global pair.
MINIMUM_BAND_RESIDUALS = 100

# A segment is gated only when its sample can resolve a miss at all. Counted in customers, because
# that is the unit the population draw uses.
MINIMUM_GATED_CUSTOMERS = 15

COVERAGE_BOOTSTRAP_DRAWS = 2_000

# ADR 0006. Customer-disjoint populations: the capacity model trains on 110_000+, so nothing here
# may reuse those seeds, and the population that fits offsets may not be the one that gates them.
CALIBRATION_SUITES = (
    ("income_diverse.yaml", 410_000),
    ("life_events.yaml", 420_000),
    ("incomplete_observation.yaml", 430_000),
)
UNCERTAINTY_SUITES = (
    ("income_diverse.yaml", 210_000),
    ("life_events.yaml", 220_000),
    ("incomplete_observation.yaml", 230_000),
)
FINAL_TEST_SUITES = (
    ("income_diverse.yaml", 510_000),
    ("life_events.yaml", 520_000),
    ("incomplete_observation.yaml", 530_000),
)


def _populations(
    project_root: Path,
    suites: tuple[tuple[str, int], ...],
    population_size: int,
    months: int,
    workers: int,
) -> dict[str, tuple]:
    scenario_root = project_root / "finances_simulator/configs/scenarios"
    return {
        scenario: build_capacity_dataset(
            (
                generate_population(
                    load_scenario_config(scenario_root / scenario),
                    population_size=population_size,
                    seed=seed,
                    months=months,
                    workers=workers,
                ),
            )
        )
        for scenario, seed in suites
    }


def _routed(row, capacity: GradientBoostedCapacityModel):
    """The month exactly as `combine_month` would publish it, routing included."""

    realized = int(row.features.get("income_1m_minor") or 0)
    return combine_month(
        realized,
        row.features,
        capacity,
        realized_components={"recurring_streams_0_2": realized},
        realized_selected="recurring_streams_0_2",
    )


def _clustered_standard_errors(
    totals_by_customer: dict[str, tuple[int, ...]],
    counts_by_customer: dict[str, int],
    *,
    seed: int = 12345,
) -> tuple[float | None, ...]:
    """Standard errors of several rates when the sampling unit is a customer, not a month.

    Rows are customer-months and one customer supplies twelve of them, so treating rows as
    independent understates the noise by roughly a factor of two.

    Every rate shares one set of customer draws, which is both correct and what separate calls
    already did: the rates are measured over the same customers, so the same seed drew the same
    resample for each. Carrying per-customer numerators and denominators instead of rebuilding the
    resampled row vector makes a draw `O(customers)` rather than `O(rows)`.
    """

    customers = sorted(counts_by_customer)
    if len(customers) < 2:
        return (None,) * len(next(iter(totals_by_customer.values()), ()))
    width = len(next(iter(totals_by_customer.values())))
    rng = random.Random(seed)
    estimates: list[list[float]] = [[] for _ in range(width)]
    size = len(customers)
    for _ in range(COVERAGE_BOOTSTRAP_DRAWS):
        drawn_rows = 0
        sums = [0] * width
        for _ in range(size):
            customer = customers[rng.randrange(size)]
            drawn_rows += counts_by_customer[customer]
            totals = totals_by_customer[customer]
            for index in range(width):
                sums[index] += totals[index]
        if drawn_rows:
            for index in range(width):
                estimates[index].append(sums[index] / drawn_rows)
    errors: list[float | None] = []
    for series in estimates:
        if not series:
            errors.append(None)
            continue
        mean = fmean(series)
        errors.append(
            math.sqrt(sum((value - mean) ** 2 for value in series) / len(series))
        )
    return tuple(errors)


def _winkler(lower: int, upper: int, truth: int, alpha: float) -> int:
    """Interval score: width, plus a penalty for how far outside the truth falls.

    An exact prediction wrapped in a degenerate interval scores zero, which is the correct reward
    for being right. A vacuously wide interval scores badly even though it covers everything, which
    is what stops a one-sided coverage gate from being gamed by widening.
    """

    score = upper - lower
    if truth < lower:
        score += round(2 * (lower - truth) / alpha)
    elif truth > upper:
        score += round(2 * (truth - upper) / alpha)
    return score


def _coverage_metrics(rows, capacity, intervals) -> dict[str, object]:
    """Measure the published interval around the routed estimate.

    Both tails are counted separately as well as jointly. ADR 0007: an `80%` interval that holds
    while its lower bound is missed twice as often as `p10` promises is not two correct quantiles,
    and a joint figure cannot tell the difference.

    A row the artifact declines to publish is counted as withheld and excluded from every rate,
    which is what makes `published_rows != row_count` a promotion failure. ADR 0005's option to
    measure a withheld band as if it were published is gone with the withholding it existed to
    expose: this writer publishes every band, so a `None` here can only come from loading an older
    artifact, and scoring it anyway would hide exactly the gap the gate is looking for.
    """

    if not rows:
        return {"count": 0, "withheld_count": 0, "no_point_estimate_count": 0}
    alpha = 1.0 - (DEFAULT_UPPER_QUANTILE - DEFAULT_LOWER_QUANTILE)
    covered = 0
    lower_missed = 0
    upper_missed = 0
    widths: list[int] = []
    scores: list[int] = []
    errors: list[int] = []
    truths: list[int] = []
    rows_by_customer: dict[str, int] = {}
    totals_by_customer: dict[str, list[int]] = {}
    withheld = 0
    unestimated = 0
    for row in rows:
        routed = _routed(row, capacity)
        point = routed.sustainable_income_minor
        if point is None:
            unestimated += 1
            continue
        score = routed.confidence_score_basis_points
        bounds = intervals.interval_minor(
            point,
            positive_basis_points=capacity.predict_positive_basis_points(row.features),
            confidence_basis_points=score,
            features=row.features,
        )
        if bounds is None:
            withheld += 1
            continue
        lower, upper = bounds
        truth = row.sustainable_monthly_income_minor
        hit = int(lower <= truth <= upper)
        below = int(truth < lower)
        above = int(truth > upper)
        covered += hit
        lower_missed += below
        upper_missed += above
        rows_by_customer[row.customer_id] = rows_by_customer.get(row.customer_id, 0) + 1
        totals = totals_by_customer.setdefault(row.customer_id, [0, 0, 0])
        totals[0] += hit
        totals[1] += below
        totals[2] += above
        widths.append(upper - lower)
        scores.append(_winkler(lower, upper, truth, alpha))
        errors.append(abs(point - truth))
        truths.append(truth)
    if not widths:
        return {
            "count": 0,
            "withheld_count": withheld,
            "no_point_estimate_count": unestimated,
        }
    truth_total = sum(truths)
    mean_truth = fmean(truths)
    clustered, lower_error, upper_error = _clustered_standard_errors(
        {customer: tuple(totals) for customer, totals in totals_by_customer.items()},
        rows_by_customer,
    )
    return {
        "count": len(widths),
        "withheld_count": withheld,
        "no_point_estimate_count": unestimated,
        "customer_count": len(rows_by_customer),
        "empirical_coverage": round(covered / len(widths), 6),
        "clustered_standard_error": round(clustered, 6) if clustered is not None else None,
        "lower_tail_miss_rate": round(lower_missed / len(widths), 6),
        "upper_tail_miss_rate": round(upper_missed / len(widths), 6),
        "lower_tail_standard_error": round(lower_error, 6) if lower_error is not None else None,
        "upper_tail_standard_error": round(upper_error, 6) if upper_error is not None else None,
        "mean_interval_width_minor": round(fmean(widths), 4),
        "median_interval_width_minor": round(float(median(widths)), 4),
        "mean_interval_score_minor": round(fmean(scores), 4),
        "interval_score_over_mean_truth": (
            round(fmean(scores) / mean_truth, 6) if mean_truth else None
        ),
        "mean_absolute_error_minor": round(fmean(errors), 4),
        "wape": round(sum(errors) / truth_total, 8) if truth_total else None,
    }


def _segmented(rows, capacity, intervals, classifier) -> dict[str, dict[str, object]]:
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(classifier(row), []).append(row)
    return {
        name: _coverage_metrics(items, capacity, intervals)
        for name, items in sorted(grouped.items())
    }


def _undercoverage_failure(
    label: str,
    metrics: dict[str, object],
    nominal: float,
) -> tuple[dict[str, object], str | None]:
    """Judge one segment on under-coverage only. ADR 0006."""

    count = int(metrics.get("count") or 0)
    if not count:
        return {"count": 0, "gated": False, "passed": True}, None
    customers = int(metrics["customer_count"])
    coverage = float(metrics["empirical_coverage"])
    error = metrics["clustered_standard_error"] or math.sqrt(
        max(1e-12, nominal * (1 - nominal) / count)
    )
    tolerance = round(max(COVERAGE_TOLERANCE, 2 * error), 6)
    gated = customers >= MINIMUM_GATED_CUSTOMERS
    passed = coverage >= nominal - tolerance
    gate = {
        "count": count,
        "withheld_count": metrics.get("withheld_count"),
        "customer_count": customers,
        "empirical_coverage": coverage,
        "clustered_standard_error": round(error, 6),
        "tolerance": tolerance,
        "floor": round(nominal - tolerance, 6),
        "gated": gated,
        "passed": passed,
        "interval_score_over_mean_truth": metrics.get("interval_score_over_mean_truth"),
    }
    if gated and not passed:
        return gate, (
            f"{label} coverage {coverage:.4f} is below the floor "
            f"{nominal - tolerance:.4f} on {customers} customers"
        )
    return gate, None


def _tail_failures(
    label: str,
    metrics: dict[str, object],
    nominal_miss: float,
) -> tuple[dict[str, object], list[str]]:
    """Judge each tail on its own miss rate. ADR 0007.

    `p10` promises the truth falls below the lower bound at most a tenth of the time and `p90`
    promises the same above the upper bound. A joint `80%` figure is satisfied by a lower tail
    missing `0.02` and an upper tail missing `0.18`, which is not the pair of quantiles the contract
    publishes. Each tail is therefore gated against its own budget, one-sided: missing less often
    than promised is not a failure, and the interval score is what charges for the width that buys.
    """

    count = int(metrics.get("count") or 0)
    if not count:
        return {"count": 0, "gated": False, "passed": True}, []
    customers = int(metrics["customer_count"])
    gated = customers >= MINIMUM_GATED_CUSTOMERS
    gate: dict[str, object] = {
        "count": count,
        "customer_count": customers,
        "nominal_miss_rate": nominal_miss,
        "gated": gated,
    }
    failures: list[str] = []
    for tail in ("lower", "upper"):
        rate = float(metrics[f"{tail}_tail_miss_rate"])
        error = metrics[f"{tail}_tail_standard_error"] or math.sqrt(
            max(1e-12, nominal_miss * (1 - nominal_miss) / count)
        )
        tolerance = round(max(TAIL_MISS_TOLERANCE, 2 * error), 6)
        ceiling = round(nominal_miss + tolerance, 6)
        passed = rate <= ceiling
        gate[tail] = {
            "miss_rate": rate,
            "clustered_standard_error": round(error, 6),
            "tolerance": tolerance,
            "ceiling": ceiling,
            "passed": passed,
        }
        if gated and not passed:
            failures.append(
                f"{label} {tail}-tail miss rate {rate:.4f} exceeds the ceiling "
                f"{ceiling:.4f} on {customers} customers"
            )
    gate["passed"] = all(gate[tail]["passed"] for tail in ("lower", "upper"))
    return gate, failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument(
        "--capacity-model",
        type=Path,
        default=Path(__file__).parent / "artifacts/capacity-estimator-0.6.0.json",
    )
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "artifacts")
    parser.add_argument("--population-size-per-suite", type=int, default=240)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)

    project_root = args.project_root.resolve()
    capacity_path = args.capacity_model.resolve()
    capacity = GradientBoostedCapacityModel.from_path(capacity_path)

    calibration_by_suite = _populations(
        project_root,
        CALIBRATION_SUITES,
        args.population_size_per_suite,
        args.months,
        args.workers,
    )
    final_by_suite = _populations(
        project_root,
        FINAL_TEST_SUITES,
        args.population_size_per_suite,
        args.months,
        args.workers,
    )
    uncertainty_by_suite = _populations(
        project_root,
        UNCERTAINTY_SUITES,
        args.population_size_per_suite,
        args.months,
        args.workers,
    )
    calibration_rows = tuple(row for rows in calibration_by_suite.values() for row in rows)
    final_rows = tuple(row for rows in final_by_suite.values() for row in rows)
    uncertainty_rows = tuple(row for rows in uncertainty_by_suite.values() for row in rows)

    calibration_customers = {row.customer_id for row in calibration_rows}
    final_customers = {row.customer_id for row in final_rows}
    uncertainty_customers = {row.customer_id for row in uncertainty_rows}
    for left, right, label in (
        (calibration_customers, final_customers, "calibration and final-test"),
        (uncertainty_customers, calibration_customers, "uncertainty and calibration"),
        (uncertainty_customers, final_customers, "uncertainty and final-test"),
    ):
        shared = left & right
        if shared:
            raise ValueError(f"{label} populations share {len(shared)} customers")

    # The quantile pair learns this row's own residual band, on customers no other stage used.
    quantile_artifact = fit_residual_quantile_model(
        residual_rows(
            uncertainty_rows,
            lambda row: _routed(row, capacity).sustainable_income_minor,
        ),
        lower_quantile=DEFAULT_LOWER_QUANTILE,
        upper_quantile=DEFAULT_UPPER_QUANTILE,
    )

    # One routing pass over the calibration population. Every downstream quantity, the residuals,
    # the bands, and the conformity scores, is derived from the same published estimate.
    routed_by_key = {
        (row.customer_id, row.reference_month): _routed(row, capacity)
        for row in calibration_rows
    }
    band_by_key = {
        key: confidence_band(routed.confidence_score_basis_points)
        for key, routed in routed_by_key.items()
    }
    calibration_residuals = residual_rows(
        calibration_rows,
        lambda row: routed_by_key[
            (row.customer_id, row.reference_month)
        ].sustainable_income_minor,
    )
    if not calibration_residuals:
        raise ValueError("no positive residuals to calibrate on")

    residuals = [row.log_residual for row in calibration_residuals]
    residual_bands = [
        band_by_key[(row.customer_id, row.reference_month)] for row in calibration_residuals
    ]
    residuals_by_band: dict[str, list[float]] = {}
    for band, residual in zip(residual_bands, residuals):
        residuals_by_band.setdefault(band, []).append(residual)

    quantile_model = ResidualQuantileModel(quantile_artifact)

    # The joint score and its single widening stay in the artifact as the documented fallback for a
    # band too thin to fit its own pair. ADR 0007 refuses to promote on it.
    scores = conformity_scores(calibration_residuals, quantile_model)
    widening = round(
        conformal_widening(scores, DEFAULT_UPPER_QUANTILE - DEFAULT_LOWER_QUANTILE), 12
    )

    # ADR 0007. Each band corrects each tail on its own scores. The single widening was `-0.0077`:
    # high and medium supplied 92% of the mass and both over-covered, so the one constant they
    # chose shrank the low band that was already under its floor.
    lower_scores, upper_scores = tail_conformity_scores(calibration_residuals, quantile_model)
    lower_scores_by_band: dict[str, list[float]] = {}
    upper_scores_by_band: dict[str, list[float]] = {}
    for band, lower_score, upper_score in zip(residual_bands, lower_scores, upper_scores):
        lower_scores_by_band.setdefault(band, []).append(lower_score)
        upper_scores_by_band.setdefault(band, []).append(upper_score)

    lower_tail_coverage = 1.0 - DEFAULT_LOWER_QUANTILE
    upper_tail_coverage = DEFAULT_UPPER_QUANTILE

    band_offsets: dict[str, BandOffsets] = {}
    band_fallbacks: dict[str, int] = {}
    band_adjustments: dict[str, BandAdjustment] = {}
    adjustment_fallbacks: dict[str, int] = {}
    for band, _ in CONFIDENCE_BAND_FLOORS:
        values = residuals_by_band.get(band, ())
        if len(values) < MINIMUM_BAND_RESIDUALS:
            band_fallbacks[band] = len(values)
        else:
            band_offsets[band] = BandOffsets(
                lower_log_offset=round(
                    min(0.0, empirical_quantile(values, DEFAULT_LOWER_QUANTILE)), 12
                ),
                upper_log_offset=round(
                    max(0.0, empirical_quantile(values, DEFAULT_UPPER_QUANTILE)), 12
                ),
                residual_count=len(values),
            )
        band_lower = lower_scores_by_band.get(band, ())
        band_upper = upper_scores_by_band.get(band, ())
        if len(band_lower) < MINIMUM_BAND_RESIDUALS:
            adjustment_fallbacks[band] = len(band_lower)
            continue
        band_adjustments[band] = BandAdjustment(
            lower_adjustment=round(conformal_tail_adjustment(band_lower, lower_tail_coverage), 12),
            upper_adjustment=round(conformal_tail_adjustment(band_upper, upper_tail_coverage), 12),
            score_count=len(band_lower),
        )

    nominal = DEFAULT_UPPER_QUANTILE - DEFAULT_LOWER_QUANTILE
    nominal_miss = DEFAULT_LOWER_QUANTILE
    all_bands = tuple(band for band, _ in CONFIDENCE_BAND_FLOORS)

    def build(*, adaptive: bool) -> ConformalCalibrationArtifact:
        """The candidate, or the fixed-band conformal model it has to beat on sharpness.

        ADR 0007. `published_bands` is always every band. The final test decides whether this
        artifact promotes; it never decides the artifact's shape, because a band that withholds
        itself when it misses is a band that cannot fail.
        """

        return ConformalCalibrationArtifact(
            calibration_version=(
                CALIBRATION_VERSION if adaptive else f"{CALIBRATION_VERSION}-fixed-band-baseline"
            ),
            method=CALIBRATION_METHOD,
            capacity_model_version=capacity.artifact.model_version,
            capacity_artifact_sha256=hashlib.sha256(capacity_path.read_bytes()).hexdigest(),
            out_of_fold_version="frozen-capacity-disjoint-population-v1",
            fold_count=2,
            nominal_lower_quantile=DEFAULT_LOWER_QUANTILE,
            nominal_upper_quantile=DEFAULT_UPPER_QUANTILE,
            lower_log_offset=round(
                min(0.0, empirical_quantile(residuals, DEFAULT_LOWER_QUANTILE)), 12
            ),
            upper_log_offset=round(
                max(0.0, empirical_quantile(residuals, DEFAULT_UPPER_QUANTILE)), 12
            ),
            band_offsets=band_offsets,
            published_bands=all_bands,
            residual_quantiles=quantile_artifact if adaptive else None,
            conformal_widening=widening if adaptive else None,
            band_adjustments=band_adjustments if adaptive else {},
            zero_gate_certain_basis_points=ZERO_GATE_CERTAIN_BASIS_POINTS,
            calibration_row_count=len(calibration_rows),
            calibration_customer_count=len(calibration_customers),
        )

    artifact = build(adaptive=True)
    intervals = ConformalIntervalModel(artifact)
    baseline_artifact = build(adaptive=False)
    baseline = ConformalIntervalModel(baseline_artifact)

    failures: list[str] = []

    # ADR 0007. A band too thin to fit its own tail pair falls back to the joint widening, which is
    # a safety valve and not a calibration. It stays in the artifact and it blocks promotion.
    for band, count in sorted(adjustment_fallbacks.items()):
        failures.append(
            f"{band}-confidence band fitted no tail adjustment on {count} calibration scores "
            f"and falls back to the joint widening, which cannot promote"
        )

    confidence_bands = _segmented(
        final_rows,
        capacity,
        intervals,
        lambda row: confidence_band(_routed(row, capacity).confidence_score_basis_points),
    )
    ordered_bands = [band for band in all_bands if confidence_bands.get(band, {}).get("count")]

    band_gate: dict[str, dict[str, object]] = {}
    band_tail_gate: dict[str, dict[str, object]] = {}
    for band in ordered_bands:
        gate, failure = _undercoverage_failure(
            f"{band}-confidence", confidence_bands[band], nominal
        )
        band_gate[band] = gate
        if failure:
            failures.append(failure)
        tail_gate, tail_failures = _tail_failures(
            f"{band}-confidence", confidence_bands[band], nominal_miss
        )
        band_tail_gate[band] = tail_gate
        failures.extend(tail_failures)

    overall = _coverage_metrics(final_rows, capacity, intervals)
    published_rows = int(overall.get("count") or 0)
    published_bands = list(artifact.published_bands)
    if set(published_bands) != set(all_bands):
        failures.append(
            "the artifact publishes "
            + (", ".join(sorted(published_bands)) or "no band")
            + f" rather than every band {sorted(all_bands)}"
        )
    if published_rows != len(final_rows):
        failures.append(
            f"{published_rows} of {len(final_rows)} final-test rows receive an interval; "
            f"complete promotion requires every supported row to publish "
            f"({overall.get('withheld_count')} withheld, "
            f"{overall.get('no_point_estimate_count')} without a point estimate)"
        )

    overall_gate, overall_failure = _undercoverage_failure("published", overall, nominal)
    if overall_failure:
        failures.append(overall_failure)
    overall_tail_gate, overall_tail_failures = _tail_failures("published", overall, nominal_miss)
    failures.extend(overall_tail_failures)

    # ADR 0007. Sharpness is mandatory and has no configurable ceiling. The candidate is measured
    # against the fixed-band conformal model on the same final rows, so coverage bought by widening
    # cannot pass a one-sided coverage gate.
    suite_gate: dict[str, dict[str, object]] = {}
    suite_metrics: dict[str, dict[str, object]] = {}
    sharpness_gate: dict[str, dict[str, object]] = {}
    for scenario, rows in final_by_suite.items():
        metrics = _coverage_metrics(rows, capacity, intervals)
        suite_metrics[scenario] = metrics
        gate, failure = _undercoverage_failure(scenario, metrics, nominal)
        gate["mean_interval_score_minor"] = metrics.get("mean_interval_score_minor")
        gate["mean_interval_width_minor"] = metrics.get("mean_interval_width_minor")
        gate["wape"] = metrics.get("wape")
        suite_gate[scenario] = gate
        if failure:
            failures.append(failure)
        tail_gate, tail_failures = _tail_failures(scenario, metrics, nominal_miss)
        gate["tails"] = tail_gate
        failures.extend(tail_failures)

        baseline_metrics = _coverage_metrics(rows, capacity, baseline)
        candidate_score = metrics.get("mean_interval_score_minor")
        baseline_score = baseline_metrics.get("mean_interval_score_minor")
        ratio = (
            round(candidate_score / baseline_score, 6)
            if candidate_score is not None and baseline_score
            else None
        )
        sharp_passed = ratio is not None and ratio <= 1.0
        sharpness_gate[scenario] = {
            "baseline_calibration": baseline_artifact.calibration_version,
            "baseline_mean_interval_score_minor": baseline_score,
            "candidate_mean_interval_score_minor": candidate_score,
            "candidate_over_baseline": ratio,
            "baseline_mean_interval_width_minor": baseline_metrics.get(
                "mean_interval_width_minor"
            ),
            "candidate_mean_interval_width_minor": metrics.get("mean_interval_width_minor"),
            "baseline_empirical_coverage": baseline_metrics.get("empirical_coverage"),
            "candidate_empirical_coverage": metrics.get("empirical_coverage"),
            "baseline_wape": baseline_metrics.get("wape"),
            "candidate_wape": metrics.get("wape"),
            "gated": bool(gate.get("gated")),
            "passed": sharp_passed,
        }
        if gate.get("gated") and not sharp_passed:
            failures.append(
                f"{scenario} interval score {candidate_score} is worse than the fixed-band "
                f"baseline {baseline_score} (ratio {ratio})"
            )
        # The interval never moves the point estimate, so a WAPE difference here would mean the two
        # models were measured on different rows.
        if metrics.get("wape") != baseline_metrics.get("wape"):
            failures.append(
                f"{scenario} point WAPE {metrics.get('wape')} differs from the baseline "
                f"{baseline_metrics.get('wape')} on the same rows"
            )

    zero_truth = _coverage_metrics(
        tuple(row for row in final_rows if row.sustainable_monthly_income_minor == 0),
        capacity,
        intervals,
    )
    zero_gate, zero_failure = _undercoverage_failure("zero-truth", zero_truth, nominal)
    if zero_failure:
        failures.append(zero_failure)

    band_errors = [
        confidence_bands[band]["wape"]
        for band in ordered_bands
        if confidence_bands[band]["wape"] is not None
    ]
    if band_errors != sorted(band_errors):
        failures.append(
            "relative error is not monotonic across confidence bands: "
            + ", ".join(f"{band}={error:.4f}" for band, error in zip(ordered_bands, band_errors))
        )

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact_path = output / f"{ARTIFACT_STEM}.json"
    artifact_bytes = (artifact.model_dump_json(indent=2) + "\n").encode()
    artifact_path.write_bytes(artifact_bytes)

    report = {
        "schema_version": "1.3",
        "artifact_schema_version": artifact.schema_version,
        "calibration_version": CALIBRATION_VERSION,
        "method": CALIBRATION_METHOD,
        "protocol": "adr-0007-complete-adaptive-promotion",
        "dataset_version": CAPACITY_DATASET_VERSION,
        "capacity_model_version": capacity.artifact.model_version,
        "capacity_artifact_sha256": artifact.capacity_artifact_sha256,
        "population_size_per_suite": args.population_size_per_suite,
        "months": args.months,
        "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "populations": {
            "calibration_suites": [
                {"scenario": scenario, "seed": seed} for scenario, seed in CALIBRATION_SUITES
            ],
            "final_test_suites": [
                {"scenario": scenario, "seed": seed} for scenario, seed in FINAL_TEST_SUITES
            ],
            "uncertainty_suites": [
                {"scenario": scenario, "seed": seed} for scenario, seed in UNCERTAINTY_SUITES
            ],
            "calibration_customers": len(calibration_customers),
            "uncertainty_customers": len(uncertainty_customers),
            "final_test_customers": len(final_customers),
            "shared_customers": 0,
        },
        "calibration": {
            "row_count": len(calibration_rows),
            "positive_residual_count": len(residuals),
            "lower_log_offset": artifact.lower_log_offset,
            "upper_log_offset": artifact.upper_log_offset,
            "minimum_band_residuals": MINIMUM_BAND_RESIDUALS,
            "band_offsets": {
                band: {
                    "lower_log_offset": offsets.lower_log_offset,
                    "upper_log_offset": offsets.upper_log_offset,
                    "residual_count": offsets.residual_count,
                }
                for band, offsets in sorted(artifact.band_offsets.items())
            },
            "bands_using_global_fallback": dict(sorted(band_fallbacks.items())),
            "residual_quantile_version": quantile_artifact.model_version,
            "residual_quantile_trees": [
                len(quantile_artifact.lower_trees),
                len(quantile_artifact.upper_trees),
            ],
            "residual_quantile_training_rows": quantile_artifact.training_row_count,
            "residual_quantile_training_customers": quantile_artifact.training_customer_count,
            "conformal_widening": widening,
            "conformity_score_count": len(scores),
            "lower_tail_coverage": lower_tail_coverage,
            "upper_tail_coverage": upper_tail_coverage,
            "band_adjustments": {
                band: {
                    "lower_adjustment": adjustment.lower_adjustment,
                    "upper_adjustment": adjustment.upper_adjustment,
                    "score_count": adjustment.score_count,
                }
                for band, adjustment in sorted(artifact.band_adjustments.items())
            },
            "bands_using_joint_widening_fallback": dict(sorted(adjustment_fallbacks.items())),
            "published_bands": list(artifact.published_bands),
        },
        "final_test": {
            "nominal_coverage": nominal,
            "nominal_tail_miss_rate": nominal_miss,
            "published_rows": published_rows,
            "row_count": len(final_rows),
            "overall_published": overall,
            "by_confidence_band": confidence_bands,
            "by_suite": suite_metrics,
            "zero_truth": zero_truth,
        },
        "promotion": {
            "status": "PROMOTED" if not failures else "NOT_PROMOTED",
            "failures": failures,
            "coverage_tolerance": COVERAGE_TOLERANCE,
            "tail_miss_tolerance": TAIL_MISS_TOLERANCE,
            "gate": "one-sided-undercoverage-and-per-tail-miss",
            "requires_every_band_published": True,
            "requires_every_row_published": True,
            "minimum_gated_customers": MINIMUM_GATED_CUSTOMERS,
            "overall": overall_gate,
            "overall_tails": overall_tail_gate,
            "zero_truth": zero_gate,
            "by_confidence_band": band_gate,
            "by_confidence_band_tails": band_tail_gate,
            "by_suite": suite_gate,
            "sharpness": sharpness_gate,
        },
    }
    report_path = output / f"{ARTIFACT_STEM}-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Artifact: {artifact_path}")
    print(f"Report: {report_path}")
    print(
        f"Published coverage: {overall.get('empirical_coverage')} nominal {nominal:.2f} "
        f"floor {overall_gate.get('floor')} on {published_rows}/{len(final_rows)} rows"
    )
    print(
        f"  tails lower={overall.get('lower_tail_miss_rate')} "
        f"upper={overall.get('upper_tail_miss_rate')} nominal {nominal_miss:.2f}"
    )
    for band in ordered_bands:
        gate = band_gate[band]
        tails = band_tail_gate[band]
        print(
            f"  {band:6} coverage={gate['empirical_coverage']:.4f} "
            f"floor={gate['floor']:.4f} customers={gate['customer_count']} "
            f"lower={tails['lower']['miss_rate']:.4f}/{tails['lower']['ceiling']:.4f} "
            f"upper={tails['upper']['miss_rate']:.4f}/{tails['upper']['ceiling']:.4f}"
        )
    for scenario, gate in sorted(suite_gate.items()):
        if not gate.get("count"):
            continue
        sharp = sharpness_gate[scenario]
        print(
            f"  {scenario:28} coverage={gate['empirical_coverage']:.4f} "
            f"floor={gate['floor']:.4f} width={gate['mean_interval_width_minor']} "
            f"score={gate['mean_interval_score_minor']} "
            f"vs baseline={sharp['baseline_mean_interval_score_minor']} "
            f"ratio={sharp['candidate_over_baseline']} wape={gate['wape']}"
        )
    print(
        f"  zero-truth coverage={zero_gate.get('empirical_coverage')} rows={zero_gate.get('count')}"
    )
    print(f"Promotion: {report['promotion']['status']}")
    for failure in failures:
        print(f"  - {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
