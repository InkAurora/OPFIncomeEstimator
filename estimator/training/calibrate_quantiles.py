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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
from income_estimator.models.uncertainty import (
    CellPolicy,
    ConditionalSelectorArtifact,
    ResidualQuantileModel,
)
from training.capacity_datasets import (
    CAPACITY_DATASET_VERSION,
    build_capacity_dataset,
)
from training.out_of_fold import customer_fold
from training.uncertainty_boosting import (
    conformal_tail_adjustment,
    conformal_widening,
    conformity_scores,
    fit_residual_quantile_model,
    residual_rows,
    tail_conformity_scores,
)

CALIBRATION_VERSION = "conditional-selector-intervals-0.11.0"
ARTIFACT_STEM = "quantile-calibration-0.11.0"
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

# The same rule one partition finer. A selector cell is a quartile of the conditioner crossed with a
# band, so there are more of them and each is smaller; a cell below this many calibration scores
# cannot fit a `0.90` tail quantile worth publishing and takes the pooled pair instead.
MINIMUM_CELL_SCORES = 100

# The `low` band keeps its band-level correction. It holds both its tails already, at `0.1091` and
# `0.0922` against `0.10`, so it is the one part of the model that is not the problem, and leaving
# it alone is what lets its published intervals be checked byte-identical against a selector-free
# artifact.
SELECTOR_BANDS: tuple[str, ...] = ("high", "medium")

# A segment is gated only when its sample can resolve a miss at all. Counted in customers, because
# that is the unit the population draw uses.
MINIMUM_GATED_CUSTOMERS = 15

# Predeclared, and declared here rather than derived from a run, because a margin chosen after
# seeing the difference is not a margin. A candidate may cost this much more Winkler score per row
# than the fixed-band model before the sharpness gate calls it worse, as a fraction of that suite's
# own baseline score: suite scores differ by roughly 4x, so an absolute figure would mean four
# different things. Set where a sharpness regression stops being operationally uninteresting and
# well below what any candidate so far spends.
SHARPNESS_NONINFERIORITY_MARGIN = 0.02

# Folds for the out-of-fold width recalibrator, over customers. The transform is fitted on bands the
# quantile model produced for customers it had not seen, because a transform fitted on the model's
# own training rows would be correcting that model's optimism about itself.
WIDTH_RECALIBRATION_FOLDS = 5

COVERAGE_BOOTSTRAP_DRAWS = 2_000


def _tolerance_error(
    clustered: object,
    *,
    nominal_rate: float,
    count: int,
) -> tuple[float, str, str | None]:
    """Resolve the error bar a tolerance is built from, and say which one it is.

    `0.0` is a measurement, not a missing value. A tail that every customer resample misses zero
    times has a clustered standard error of exactly zero, and that is the number the tolerance
    should be built from: the tolerance then falls back to its fixed floor instead of being widened
    by an error bar the data does not support. Treating `0.0` as absent is what put a row-binomial
    `0.0056` next to a `life_events` lower-tail miss rate of `0.0000` and labelled it clustered.

    The row-level binomial remains only as a last resort, for a segment with too few customers to
    resample at all. It is reported under its own name because it understates the noise by roughly
    the square root of the rows-per-customer ratio, and a gated segment that has to reach for it is
    a protocol failure rather than a measurement.
    """

    if clustered is not None:
        return float(clustered), "customer-bootstrap", None
    fallback = math.sqrt(max(1e-12, nominal_rate * (1 - nominal_rate) / max(1, count)))
    return (
        fallback,
        "row-binomial-fallback",
        "no customer-clustered standard error is available",
    )

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

# Seeds `510_000`-`530_000` have been read across several method-selection rounds. Every look costs
# some of their independence, and no amount of care gives it back, so they are validation seeds
# permanently and the report says so rather than letting a reader infer a lockbox from the name
# "final test". A release lockbox is drawn from seeds no run has touched, once every gate passes on
# validation, and is read exactly once.
FINAL_TEST_ROLE = "validation-not-release-lockbox"
RELEASE_LOCKBOX_SEED_FLOOR = 610_000


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

    `0.0` is a result and `None` is an absence. A rate that is identical in every resample, a tail
    no customer ever misses being the common case, has a standard error of exactly zero; only a
    segment with fewer than two customers has none to report. Callers must not conflate the two.
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


@dataclass(frozen=True, slots=True)
class _PairedRow:
    """One customer-month scored by both models, with the covariates the diagnostics segment on."""

    customer_id: str
    difference: int
    candidate_width: int
    baseline_width: int
    candidate_covered: bool
    baseline_covered: bool
    candidate_missed_low: bool
    candidate_missed_high: bool
    confidence_band: str
    positive_basis_points: int
    features: Mapping[str, float | int | None]
    point_minor: int
    truth_minor: int


def _paired_rows(rows, capacity, candidate, baseline) -> tuple[tuple[_PairedRow, ...], int]:
    """Score both models on every row once, and keep the pairing.

    A ratio of two independently reported means is not a comparison. Both models see the same rows,
    so the difference is taken row by row and the shared variance that dominates a Winkler score,
    how hard each customer-month happens to be, cancels. What survives is what the calibration
    choice actually costs.

    A row either model withholds cannot be paired and is excluded from both sides, counted rather
    than dropped silently. This writer publishes every band, so a non-zero count here means one of
    the two artifacts is older than the complete-promotion rule.
    """

    alpha = 1.0 - (DEFAULT_UPPER_QUANTILE - DEFAULT_LOWER_QUANTILE)
    paired: list[_PairedRow] = []
    unpaired = 0
    for row in rows:
        routed = _routed(row, capacity)
        point = routed.sustainable_income_minor
        if point is None:
            unpaired += 1
            continue
        positive = capacity.predict_positive_basis_points(row.features)
        score = routed.confidence_score_basis_points
        pair = [
            model.interval_minor(
                point,
                positive_basis_points=positive,
                confidence_basis_points=score,
                features=row.features,
            )
            for model in (candidate, baseline)
        ]
        if any(bounds is None for bounds in pair):
            unpaired += 1
            continue
        (candidate_lower, candidate_upper), (baseline_lower, baseline_upper) = pair
        truth = row.sustainable_monthly_income_minor
        paired.append(
            _PairedRow(
                customer_id=row.customer_id,
                difference=(
                    _winkler(candidate_lower, candidate_upper, truth, alpha)
                    - _winkler(baseline_lower, baseline_upper, truth, alpha)
                ),
                candidate_width=candidate_upper - candidate_lower,
                baseline_width=baseline_upper - baseline_lower,
                candidate_covered=candidate_lower <= truth <= candidate_upper,
                baseline_covered=baseline_lower <= truth <= baseline_upper,
                candidate_missed_low=truth < candidate_lower,
                candidate_missed_high=truth > candidate_upper,
                confidence_band=confidence_band(score),
                positive_basis_points=positive,
                features=row.features,
                point_minor=point,
                truth_minor=truth,
            )
        )
    return tuple(paired), unpaired


def _paired_statistics(paired: Sequence[_PairedRow]) -> dict[str, object]:
    """Mean paired difference and its customer-clustered error bar.

    The resampling unit is the customer, for the same reason it is everywhere else here: a customer
    supplies roughly twelve of these rows and their differences move together.
    """

    differences_by_customer: dict[str, int] = {}
    counts_by_customer: dict[str, int] = {}
    for item in paired:
        differences_by_customer[item.customer_id] = (
            differences_by_customer.get(item.customer_id, 0) + item.difference
        )
        counts_by_customer[item.customer_id] = counts_by_customer.get(item.customer_id, 0) + 1
    (error,) = _clustered_standard_errors(
        {customer: (total,) for customer, total in differences_by_customer.items()},
        counts_by_customer,
    )
    mean_difference = fmean(item.difference for item in paired)
    return {
        "paired_row_count": len(paired),
        "customer_count": len(counts_by_customer),
        "mean_difference_minor": round(mean_difference, 4),
        "clustered_standard_error_minor": round(error, 4) if error is not None else None,
        "difference_upper_confidence_bound_minor": (
            round(mean_difference + 2 * error, 4) if error is not None else None
        ),
        "candidate_worse_row_share": round(
            sum(1 for item in paired if item.difference > 0) / len(paired), 6
        ),
    }


def _paired_sharpness(rows, capacity, candidate, baseline) -> dict[str, object]:
    """The paired difference the sharpness gate judges."""

    if not rows:
        return {"paired_row_count": 0}
    paired, unpaired = _paired_rows(rows, capacity, candidate, baseline)
    if not paired:
        return {"paired_row_count": 0, "unpaired_row_count": unpaired}
    return {**_paired_statistics(paired), "unpaired_row_count": unpaired}


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
    clustered = metrics["clustered_standard_error"]
    error, basis, degraded = _tolerance_error(clustered, nominal_rate=nominal, count=count)
    tolerance = round(max(COVERAGE_TOLERANCE, 2 * error), 6)
    gated = customers >= MINIMUM_GATED_CUSTOMERS
    passed = coverage >= nominal - tolerance
    gate = {
        "count": count,
        "withheld_count": metrics.get("withheld_count"),
        "customer_count": customers,
        "empirical_coverage": coverage,
        "clustered_standard_error": round(clustered, 6) if clustered is not None else None,
        "standard_error": round(error, 6),
        "standard_error_basis": basis,
        "tolerance": tolerance,
        "floor": round(nominal - tolerance, 6),
        "gated": gated,
        "passed": passed,
        "interval_score_over_mean_truth": metrics.get("interval_score_over_mean_truth"),
    }
    if gated and degraded:
        return gate, f"{label} is gated on {customers} customers but {degraded}"
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
        clustered = metrics[f"{tail}_tail_standard_error"]
        error, basis, degraded = _tolerance_error(
            clustered, nominal_rate=nominal_miss, count=count
        )
        tolerance = round(max(TAIL_MISS_TOLERANCE, 2 * error), 6)
        ceiling = round(nominal_miss + tolerance, 6)
        passed = rate <= ceiling
        gate[tail] = {
            "miss_rate": rate,
            "clustered_standard_error": round(clustered, 6) if clustered is not None else None,
            "standard_error": round(error, 6),
            "standard_error_basis": basis,
            "tolerance": tolerance,
            "ceiling": ceiling,
            "passed": passed,
        }
        if gated and degraded:
            failures.append(f"{label} {tail}-tail is gated on {customers} customers but {degraded}")
        if gated and not passed:
            failures.append(
                f"{label} {tail}-tail miss rate {rate:.4f} exceeds the ceiling "
                f"{ceiling:.4f} on {customers} customers"
            )
    gate["passed"] = all(gate[tail]["passed"] for tail in ("lower", "upper"))
    return gate, failures


def _bucket(value: float | int | None, edges: Sequence[tuple[float, str]], absent: str) -> str:
    """Name the band `value` falls in. Missing is its own bucket, never folded into the lowest."""

    if value is None:
        return absent
    for ceiling, name in edges:
        if value < ceiling:
            return name
    return edges[-1][1] if not edges else "high"


def _strata(item: _PairedRow, width_cuts: Sequence[float]) -> dict[str, str]:
    """The buckets one paired row falls in, on each dimension width allocation is diagnosed over.

    These are the covariates already in the feature table. Whether they separate the regimes decides
    whether a conditional selector can be fitted at all, or whether new features are needed first.
    """

    features = item.features
    quartile = sum(1 for cut in width_cuts if item.candidate_width > cut)
    residual = item.truth_minor - item.point_minor
    return {
        "confidence_band": item.confidence_band,
        "candidate_width_quartile": f"q{quartile + 1}",
        "hurdle_probability": _bucket(
            item.positive_basis_points,
            ((5_000, "unsure"), (9_000, "likely")),
            "unknown",
        ),
        "source_count_12m": _bucket(
            features.get("source_count_12m"),
            ((1, "none"), (2, "one"), (3, "two")),
            "unknown",
        ),
        "recurrence_score": _bucket(
            features.get("recurrence_score_mean_12m_basis_points"),
            ((3_000, "irregular"), (7_000, "mixed")),
            "unknown",
        ),
        "data_completeness": _bucket(
            features.get("data_completeness_score_basis_points"),
            ((5_000, "sparse"), (8_000, "partial")),
            "unknown",
        ),
        "months_observed": _bucket(
            features.get("months_observed"),
            ((6, "under-6"), (12, "6-to-11")),
            "unknown",
        ),
        "residual_sign": (
            "under-estimated" if residual > 0 else "over-estimated" if residual < 0 else "exact"
        ),
    }


def _width_allocation(paired: Sequence[_PairedRow]) -> dict[str, object]:
    """Where the candidate spends score relative to the fixed-band model, by stratum.

    A suite-level mean says the candidate is worse without saying on which rows, and the two
    sharpness failures point opposite ways: `income_diverse` needs a wider upper tail while
    `incomplete_observation` needs materially less width overall. Widening globally would trade one
    for the other. This is the breakdown that says whether the features already in hand separate
    those regimes.

    Each bucket carries its own customer-clustered error bar, because a stratum that looks worst may
    just be the one with the fewest customers in it.
    """

    if not paired:
        return {}
    ordered_widths = sorted(item.candidate_width for item in paired)
    width_cuts = [
        ordered_widths[min(len(ordered_widths) - 1, int(quantile * len(ordered_widths)))]
        for quantile in (0.25, 0.5, 0.75)
    ]
    grouped: dict[str, dict[str, list[_PairedRow]]] = {}
    for item in paired:
        for dimension, bucket in _strata(item, width_cuts).items():
            grouped.setdefault(dimension, {}).setdefault(bucket, []).append(item)
    return {
        "candidate_width_quartile_cuts_minor": width_cuts,
        "dimensions": {
            dimension: {
                bucket: {
                    **_paired_statistics(items),
                    "row_share": round(len(items) / len(paired), 6),
                    "mean_candidate_width_minor": round(
                        fmean(item.candidate_width for item in items), 4
                    ),
                    "mean_baseline_width_minor": round(
                        fmean(item.baseline_width for item in items), 4
                    ),
                    "candidate_coverage": round(
                        sum(item.candidate_covered for item in items) / len(items), 6
                    ),
                    "baseline_coverage": round(
                        sum(item.baseline_covered for item in items) / len(items), 6
                    ),
                    "candidate_lower_tail_miss_rate": round(
                        sum(item.candidate_missed_low for item in items) / len(items), 6
                    ),
                    "candidate_upper_tail_miss_rate": round(
                        sum(item.candidate_missed_high for item in items) / len(items), 6
                    ),
                }
                for bucket, items in sorted(buckets.items())
            }
            for dimension, buckets in sorted(grouped.items())
        },
    }


def _choose_branch(items: Sequence[tuple[float, tuple[float, float], tuple[float, float]]]) -> str:
    """Pick the branch whose corrected band is narrower on this cell's out-of-fold rows.

    Both branches are first corrected to hold their own tails at `0.90`, so the comparison is like
    with like: whichever is narrower afterwards is the one that buys the same claim for less width.
    That is the sharpness pressure the interval score applies later, applied here where it can still
    change the answer.

    Each item is `(log_residual, adaptive_bounds, fixed_bounds)`.
    """

    costs: dict[str, float] = {}
    for index, branch in ((1, "adaptive"), (2, "fixed")):
        lower_correction = empirical_quantile(
            [item[index][0] - item[0] for item in items], DEFAULT_UPPER_QUANTILE
        )
        upper_correction = empirical_quantile(
            [item[0] - item[index][1] for item in items], DEFAULT_UPPER_QUANTILE
        )
        costs[branch] = fmean(
            (item[index][1] + upper_correction) - (item[index][0] - lower_correction)
            for item in items
        )
    return min(costs, key=lambda branch: costs[branch])


def _sharpness_failure(
    label: str,
    metrics: dict[str, object],
    baseline_metrics: dict[str, object],
    paired: dict[str, object],
    baseline_tails: dict[str, object],
    *,
    baseline_calibration: str,
    gated: bool,
) -> tuple[dict[str, object], str | None]:
    """Judge sharpness as a one-sided non-inferiority test on the paired difference.

    The old form compared two independently reported means and failed anything above a ratio of
    `1.0`. That treats `1.001` as a regression and `0.999` as an improvement, on a difference whose
    sampling noise was never measured.

    Non-inferiority instead asks whether the whole plausible range of the paired difference sits
    below a margin fixed in advance. A candidate passes by being no more than
    `SHARPNESS_NONINFERIORITY_MARGIN` of the baseline score worse per row, error bar included, and a
    difference with no error bar is refused rather than waved through.

    The gate stays unconditional. `baseline_tails_hold` records whether the baseline holds its own
    tail claims on this suite, because a baseline that under-covers wins on Winkler score by
    declining to buy width it owes. That is worth seeing, and it is not an exemption: excluding
    under-covering baselines would remove exactly the pressure the interval score exists to apply.
    """

    candidate_score = metrics.get("mean_interval_score_minor")
    baseline_score = baseline_metrics.get("mean_interval_score_minor")
    ratio = (
        round(candidate_score / baseline_score, 6)
        if candidate_score is not None and baseline_score
        else None
    )
    margin = round(SHARPNESS_NONINFERIORITY_MARGIN * baseline_score, 4) if baseline_score else None
    bound = paired.get("difference_upper_confidence_bound_minor")
    passed = bound is not None and margin is not None and bound <= margin

    # A non-inferiority test can fail two ways, and they call for opposite responses. Either the
    # candidate really is worse than the margin allows, or the sample cannot resolve a difference
    # that small and nothing would pass. `margin_resolvable` separates them: it asks whether a
    # candidate whose true difference were zero could clear the margin at this error bar. A gate
    # that is not resolvable is a sample-size problem, not a model verdict.
    error = paired.get("clustered_standard_error_minor")
    resolvable = (
        bool(2 * error <= margin) if error is not None and margin is not None else None
    )
    gate: dict[str, object] = {
        "baseline_calibration": baseline_calibration,
        "baseline_mean_interval_score_minor": baseline_score,
        "candidate_mean_interval_score_minor": candidate_score,
        "candidate_over_baseline": ratio,
        "baseline_mean_interval_width_minor": baseline_metrics.get("mean_interval_width_minor"),
        "candidate_mean_interval_width_minor": metrics.get("mean_interval_width_minor"),
        "baseline_empirical_coverage": baseline_metrics.get("empirical_coverage"),
        "candidate_empirical_coverage": metrics.get("empirical_coverage"),
        "baseline_lower_tail_miss_rate": baseline_metrics.get("lower_tail_miss_rate"),
        "baseline_upper_tail_miss_rate": baseline_metrics.get("upper_tail_miss_rate"),
        "baseline_tails_hold": bool(baseline_tails.get("passed")),
        "baseline_wape": baseline_metrics.get("wape"),
        "candidate_wape": metrics.get("wape"),
        "noninferiority_margin_fraction": SHARPNESS_NONINFERIORITY_MARGIN,
        "noninferiority_margin_minor": margin,
        "margin_resolvable": resolvable,
        "paired": paired,
        "gated": gated,
        "passed": passed,
    }
    if not gated or passed:
        return gate, None
    if bound is None:
        return gate, (
            f"{label} sharpness has no paired error bar and cannot be judged "
            f"({paired.get('paired_row_count')} paired rows)"
        )
    unresolvable = "" if resolvable else "; the margin is not resolvable at this error bar"
    return gate, (
        f"{label} sharpness: paired mean interval score difference "
        f"{paired['mean_difference_minor']} (upper bound {bound}) exceeds the predeclared margin "
        f"{margin}, {SHARPNESS_NONINFERIORITY_MARGIN:.0%} of the fixed-band baseline "
        f"{baseline_score}{unresolvable}"
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
        "--preregistration",
        type=Path,
        default=Path(__file__).parent / "artifacts/conditioner-preregistration.json",
        help="Frozen record of the conditioner chosen inside the uncertainty population",
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

    # One routing pass over the uncertainty population, reused by the quantile fit and by the
    # out-of-fold pass that fits the width transform.
    uncertainty_routed = {
        (row.customer_id, row.reference_month): _routed(row, capacity)
        for row in uncertainty_rows
    }
    uncertainty_residuals = residual_rows(
        uncertainty_rows,
        lambda row: uncertainty_routed[
            (row.customer_id, row.reference_month)
        ].sustainable_income_minor,
    )

    # The quantile pair learns this row's own residual band, on customers no other stage used.
    quantile_artifact = fit_residual_quantile_model(
        uncertainty_residuals,
        lower_quantile=DEFAULT_LOWER_QUANTILE,
        upper_quantile=DEFAULT_UPPER_QUANTILE,
    )

    # The conditioner was ranked inside this same population and frozen before this run. Reading it
    # here rather than choosing it here is the whole point: the feature was picked without any
    # final-test population being loaded, and the record says so.
    preregistration_bytes = args.preregistration.resolve().read_bytes()
    preregistration = json.loads(preregistration_bytes.decode("utf-8"))
    if preregistration.get("final_test_inspected"):
        raise ValueError("the conditioner pre-registration records having read final test")
    conditioner = preregistration["selected"]["feature"]
    cut_points = tuple(float(cut) for cut in preregistration["selected"]["cut_points"])

    def bucket_of(features) -> str:
        value = features.get(conditioner)
        if value is None:
            return "unknown"
        return f"q{sum(1 for cut in cut_points if float(value) > cut) + 1}"

    # A fixed band fitted on the uncertainty population, used only to decide which branch each cell
    # prefers. The artifact publishes the calibration-fitted `band_offsets`; what the decision needs
    # from a fixed band is its shape relative to the learned one, not its exact level, and taking it
    # from here keeps the choice inside the population it is allowed to see.
    uncertainty_by_band: dict[str, list[float]] = {}
    for row in uncertainty_residuals:
        band = confidence_band(
            uncertainty_routed[
                (row.customer_id, row.reference_month)
            ].confidence_score_basis_points
        )
        uncertainty_by_band.setdefault(band, []).append(row.log_residual)
    uncertainty_fixed = {
        band: (
            min(0.0, empirical_quantile(values, DEFAULT_LOWER_QUANTILE)),
            max(0.0, empirical_quantile(values, DEFAULT_UPPER_QUANTILE)),
        )
        for band, values in uncertainty_by_band.items()
    }

    # Which branch each cell uses is decided out-of-fold: one quantile refit per fold, and every
    # learned band comes from a model that never saw that customer. No calibration or final-test
    # customer is read here.
    branch_rows: dict[str, list[tuple[float, tuple[float, float], tuple[float, float]]]] = {}
    for fold in range(WIDTH_RECALIBRATION_FOLDS):
        training = [
            row
            for row in uncertainty_residuals
            if customer_fold(row.customer_id, WIDTH_RECALIBRATION_FOLDS) != fold
        ]
        held_out = [
            row
            for row in uncertainty_residuals
            if customer_fold(row.customer_id, WIDTH_RECALIBRATION_FOLDS) == fold
        ]
        if not training or not held_out:
            continue
        fold_model = ResidualQuantileModel(
            fit_residual_quantile_model(
                training,
                lower_quantile=DEFAULT_LOWER_QUANTILE,
                upper_quantile=DEFAULT_UPPER_QUANTILE,
            )
        )
        for row in held_out:
            band = confidence_band(
                uncertainty_routed[
                    (row.customer_id, row.reference_month)
                ].confidence_score_basis_points
            )
            if band not in SELECTOR_BANDS:
                continue
            branch_rows.setdefault(f"{bucket_of(row.features)}/{band}", []).append(
                (
                    row.log_residual,
                    fold_model.predict_bounds(row.features),
                    uncertainty_fixed.get(band, (0.0, 0.0)),
                )
            )
    branch_by_cell = {cell: _choose_branch(items) for cell, items in branch_rows.items()}

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

    # Fitted before the conformity scores rather than after, because the selector's `fixed` branch
    # publishes these and the correction has to be a quantile of the score against what is
    # published.
    band_offsets: dict[str, BandOffsets] = {}
    band_fallbacks: dict[str, int] = {}
    for band, _ in CONFIDENCE_BAND_FLOORS:
        values = residuals_by_band.get(band, ())
        if len(values) < MINIMUM_BAND_RESIDUALS:
            band_fallbacks[band] = len(values)
            continue
        band_offsets[band] = BandOffsets(
            lower_log_offset=round(
                min(0.0, empirical_quantile(values, DEFAULT_LOWER_QUANTILE)), 12
            ),
            upper_log_offset=round(
                max(0.0, empirical_quantile(values, DEFAULT_UPPER_QUANTILE)), 12
            ),
            residual_count=len(values),
        )

    global_lower = round(min(0.0, empirical_quantile(residuals, DEFAULT_LOWER_QUANTILE)), 12)
    global_upper = round(max(0.0, empirical_quantile(residuals, DEFAULT_UPPER_QUANTILE)), 12)

    def cell_of(row) -> str:
        band = band_by_key[(row.customer_id, row.reference_month)]
        return f"{bucket_of(row.features)}/{band}"

    def published_bounds(row) -> tuple[float, float]:
        """The band the runtime would publish for one calibration row, before its correction.

        A conformal correction is a claim about the bound that is actually emitted, so it has to be
        a quantile of the score against the branch this row's cell selected. Scoring every row
        against the learned band and then publishing the fixed one for some of them would correct a
        quantity nothing publishes.
        """

        band = band_by_key[(row.customer_id, row.reference_month)]
        if band not in SELECTOR_BANDS:
            return quantile_model.predict_bounds(row.features)
        if branch_by_cell.get(cell_of(row), "adaptive") == "adaptive":
            return quantile_model.predict_bounds(row.features)
        offsets = band_offsets.get(band)
        if offsets is None:
            return global_lower, global_upper
        return offsets.lower_log_offset, offsets.upper_log_offset

    # The joint score and its single widening stay in the artifact as the documented fallback for a
    # band too thin to fit its own pair. ADR 0007 refuses to promote on it.
    scores = conformity_scores(calibration_residuals, published_bounds)
    widening = round(
        conformal_widening(scores, DEFAULT_UPPER_QUANTILE - DEFAULT_LOWER_QUANTILE), 12
    )

    # ADR 0007. Each band corrects each tail on its own scores. The single widening was `-0.0077`:
    # high and medium supplied 92% of the mass and both over-covered, so the one constant they
    # chose shrank the low band that was already under its floor.
    lower_scores, upper_scores = tail_conformity_scores(calibration_residuals, published_bounds)
    lower_scores_by_band: dict[str, list[float]] = {}
    upper_scores_by_band: dict[str, list[float]] = {}
    for band, lower_score, upper_score in zip(residual_bands, lower_scores, upper_scores):
        lower_scores_by_band.setdefault(band, []).append(lower_score)
        upper_scores_by_band.setdefault(band, []).append(upper_score)

    lower_tail_coverage = 1.0 - DEFAULT_LOWER_QUANTILE
    upper_tail_coverage = DEFAULT_UPPER_QUANTILE

    band_adjustments: dict[str, BandAdjustment] = {}
    adjustment_fallbacks: dict[str, int] = {}
    for band, _ in CONFIDENCE_BAND_FLOORS:
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

    # Each cell's two tail corrections, fitted on calibration customers against the branch that
    # cell selected. Same split-conformal step as the band adjustments, on a finer partition.
    cell_lower: dict[str, list[float]] = {}
    cell_upper: dict[str, list[float]] = {}
    for row, lower_score, upper_score in zip(calibration_residuals, lower_scores, upper_scores):
        if band_by_key[(row.customer_id, row.reference_month)] not in SELECTOR_BANDS:
            continue
        cell = cell_of(row)
        cell_lower.setdefault(cell, []).append(lower_score)
        cell_upper.setdefault(cell, []).append(upper_score)

    # A cell too thin to fit its own pair falls back to its band's pair rather than inventing one,
    # and the gate refuses to promote on that fallback.
    selector_fallback = CellPolicy(
        branch="adaptive",
        lower_adjustment=round(
            conformal_tail_adjustment(lower_scores, lower_tail_coverage), 12
        ),
        upper_adjustment=round(
            conformal_tail_adjustment(upper_scores, upper_tail_coverage), 12
        ),
        score_count=len(lower_scores),
    )
    selector_cells: dict[str, CellPolicy] = {}
    selector_fallbacks: dict[str, int] = {}
    for cell in sorted(set(cell_lower) | set(branch_by_cell)):
        lower_values = cell_lower.get(cell, [])
        upper_values = cell_upper.get(cell, [])
        if len(lower_values) < MINIMUM_CELL_SCORES:
            selector_fallbacks[cell] = len(lower_values)
            selector_cells[cell] = selector_fallback
            continue
        selector_cells[cell] = CellPolicy(
            branch=branch_by_cell.get(cell, "adaptive"),
            lower_adjustment=round(
                conformal_tail_adjustment(lower_values, lower_tail_coverage), 12
            ),
            upper_adjustment=round(
                conformal_tail_adjustment(upper_values, upper_tail_coverage), 12
            ),
            score_count=len(lower_values),
        )
    selector = ConditionalSelectorArtifact(
        feature_name=conditioner,
        cut_points=cut_points,
        applies_to_bands=SELECTOR_BANDS,
        cells=selector_cells,
        fallback=selector_fallback,
        selection_version=preregistration["selection_version"],
        selected_on=preregistration["selected_on"],
        preregistration_sha256=hashlib.sha256(preregistration_bytes).hexdigest(),
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
            width_recalibrator=None,
            conditional_selector=selector if adaptive else None,
            zero_gate_certain_basis_points=ZERO_GATE_CERTAIN_BASIS_POINTS,
            calibration_row_count=len(calibration_rows),
            calibration_customer_count=len(calibration_customers),
        )

    artifact = build(adaptive=True)
    intervals = ConformalIntervalModel(artifact)
    baseline_artifact = build(adaptive=False)
    baseline = ConformalIntervalModel(baseline_artifact)

    # The `low` band is the one part of the model that already holds both its tails, and the
    # selector is declared not to touch it. Declaring that is not the same as it being true, so it
    # is checked against the artifact rather than asserted: the same artifact with the selector
    # removed must publish byte-identical bounds on every low-band row.
    untransformed = ConformalIntervalModel(
        artifact.model_copy(update={"conditional_selector": None})
    )

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
    paired_by_suite: dict[str, tuple[_PairedRow, ...]] = {}
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
        paired_rows, unpaired = _paired_rows(rows, capacity, intervals, baseline)
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
            baseline_calibration=baseline_artifact.calibration_version,
            gated=bool(gate.get("gated")),
        )
        sharpness_gate[scenario] = sharp_gate
        if sharp_failure:
            failures.append(sharp_failure)
        # The interval never moves the point estimate, so a WAPE difference here would mean the two
        # models were measured on different rows.
        if metrics.get("wape") != baseline_metrics.get("wape"):
            failures.append(
                f"{scenario} point WAPE {metrics.get('wape')} differs from the baseline "
                f"{baseline_metrics.get('wape')} on the same rows"
            )

    # ADR-adjacent diagnostic, deliberately not a gate. On suites where the fixed-band model holds
    # its own tails, the comparison is between two models that both make the claim they publish. On
    # the others the candidate is being asked to beat a model that wins on score by under-covering.
    # Reporting the restricted view answers "how much of the failure is that?" without letting it
    # become an exemption: dropping under-covering baselines would remove exactly the pressure the
    # interval score exists to apply, so the gate above still counts every suite.
    valid_baseline_suites = sorted(
        scenario for scenario, gate in sharpness_gate.items() if gate["baseline_tails_hold"]
    )
    valid_baseline_only = {
        "suites": valid_baseline_suites,
        "excluded_suites": sorted(set(sharpness_gate) - set(valid_baseline_suites)),
        "gates_promotion": False,
    }
    if valid_baseline_suites:
        restricted = tuple(
            item for scenario in valid_baseline_suites for item in paired_by_suite[scenario]
        )
        if restricted:
            valid_baseline_only.update(_paired_statistics(restricted))
            valid_baseline_only["would_pass_every_suite"] = all(
                sharpness_gate[scenario]["passed"] for scenario in valid_baseline_suites
            )

    if selector_fallbacks:
        failures.append(
            "selector cells "
            + ", ".join(
                f"{name} ({count} scores)"
                for name, count in sorted(selector_fallbacks.items())
            )
            + " fall back to the pooled pair rather than fitting their own, which cannot promote"
        )

    low_band_divergences = 0
    low_band_rows = 0
    for row in final_rows:
        routed = _routed(row, capacity)
        point = routed.sustainable_income_minor
        score = routed.confidence_score_basis_points
        if point is None or confidence_band(score) != "low":
            continue
        low_band_rows += 1
        positive = capacity.predict_positive_basis_points(row.features)
        with_transform = intervals.interval_minor(
            point,
            positive_basis_points=positive,
            confidence_basis_points=score,
            features=row.features,
        )
        without_transform = untransformed.interval_minor(
            point,
            positive_basis_points=positive,
            confidence_basis_points=score,
            features=row.features,
        )
        if with_transform != without_transform:
            low_band_divergences += 1
    low_band_bypass = {
        "rows": low_band_rows,
        "divergent_rows": low_band_divergences,
        "passed": low_band_divergences == 0,
    }
    if low_band_divergences:
        failures.append(
            f"the conditional selector changes {low_band_divergences} of {low_band_rows} low-band "
            f"intervals; the low band is declared to bypass it exactly"
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

    pooled_paired = tuple(
        item for scenario in sorted(paired_by_suite) for item in paired_by_suite[scenario]
    )

    report = {
        "schema_version": "1.4",
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
        # Diagnostic, never a gate. Where the candidate spends score relative to the fixed-band
        # model, by covariates already in the feature table. The two sharpness failures point
        # opposite ways, so a single global widening cannot fix both; this is what says whether the
        # existing features separate those regimes or whether new ones are needed first.
        "width_allocation": {
            "overall": _width_allocation(pooled_paired),
            "by_suite": {
                scenario: _width_allocation(items)
                for scenario, items in sorted(paired_by_suite.items())
            },
        },
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
            "final_test_role": FINAL_TEST_ROLE,
            "release_lockbox_seed_floor": RELEASE_LOCKBOX_SEED_FLOOR,
            "shared_customers": 0,
        },
        "calibration": {
            "row_count": len(calibration_rows),
            "positive_residual_count": len(residuals),
            "lower_log_offset": artifact.lower_log_offset,
            "upper_log_offset": artifact.upper_log_offset,
            "minimum_band_residuals": MINIMUM_BAND_RESIDUALS,
            "minimum_cell_scores": MINIMUM_CELL_SCORES,
            "conditional_selector": {
                "method": selector.method,
                "feature_name": selector.feature_name,
                "cut_points": list(selector.cut_points),
                "applies_to_bands": list(selector.applies_to_bands),
                "selection_version": selector.selection_version,
                "selected_on": selector.selected_on,
                "preregistration_sha256": selector.preregistration_sha256,
                "preregistration_worst_seed_tail_miss": preregistration["selected"][
                    "worst_seed_tail_miss"
                ],
                "branch_decision_folds": WIDTH_RECALIBRATION_FOLDS,
                "cells": {
                    name: {
                        "branch": cell.branch,
                        "lower_adjustment": cell.lower_adjustment,
                        "upper_adjustment": cell.upper_adjustment,
                        "score_count": cell.score_count,
                    }
                    for name, cell in sorted(selector.cells.items())
                },
                "cells_using_fallback": selector_fallbacks,
            },
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
            "sharpness_noninferiority_margin": SHARPNESS_NONINFERIORITY_MARGIN,
            "sharpness_valid_baseline_only": valid_baseline_only,
            "low_band_bypasses_recalibrator": low_band_bypass,
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
        paired = sharp["paired"]
        print(
            f"  {scenario:28} coverage={gate['empirical_coverage']:.4f} "
            f"floor={gate['floor']:.4f} width={gate['mean_interval_width_minor']} "
            f"score={gate['mean_interval_score_minor']} "
            f"vs baseline={sharp['baseline_mean_interval_score_minor']} wape={gate['wape']}"
        )
        print(
            f"  {'':28} sharpness paired diff="
            f"{paired.get('mean_difference_minor')}"
            f" +/-{paired.get('clustered_standard_error_minor')}"
            f" bound={paired.get('difference_upper_confidence_bound_minor')}"
            f" margin={sharp['noninferiority_margin_minor']}"
            f" baseline_tails_hold={sharp['baseline_tails_hold']}"
        )
    print(
        f"  zero-truth coverage={zero_gate.get('empirical_coverage')} rows={zero_gate.get('count')}"
    )
    branches = {}
    for name, cell in sorted(selector.cells.items()):
        branches.setdefault(cell.branch, []).append(name)
    print(
        f"  selector on {selector.feature_name} cuts "
        f"{'/'.join(f'{cut:g}' for cut in selector.cut_points)} over "
        f"{'/'.join(selector.applies_to_bands)}: "
        + ", ".join(f"{branch}={len(names)}" for branch, names in sorted(branches.items()))
        + f"; fallback cells {len(selector_fallbacks)}"
    )
    for name, cell in sorted(selector.cells.items()):
        print(
            f"    {name:<14} {cell.branch:<9} lower={cell.lower_adjustment:+.4f} "
            f"upper={cell.upper_adjustment:+.4f} n={cell.score_count}"
        )
    print(
        f"  low-band bypass {low_band_bypass['divergent_rows']}/{low_band_bypass['rows']} divergent"
    )
    print(f"Promotion: {report['promotion']['status']}")
    for failure in failures:
        print(f"  - {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
