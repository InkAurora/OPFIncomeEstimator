from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

import pytest
from pydantic import ValidationError

pytest.importorskip("pyarrow")

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config

from income_estimator.features.schema import FEATURE_NAMES
from income_estimator.models.capacity import GradientBoostedCapacityModel
from income_estimator.models.quantiles import (
    CONFIDENCE_BAND_FLOORS,
    CalibrationBindingError,
    ConformalCalibrationArtifact,
    ConformalIntervalModel,
    confidence_band,
    empirical_quantile,
    require_capacity_binding,
)
from income_estimator.models.uncertainty import (
    ConditionalSelectorArtifact,
    ResidualQuantileArtifact,
    ResidualQuantileModel,
    SupportEnvelopeArtifact,
    WidthRecalibratorArtifact,
)
from income_estimator.pipeline import EnsembleIncomeEstimator
from training.calibrate_quantiles import (
    SHARPNESS_NONINFERIORITY_MARGIN,
    TAIL_MISS_TOLERANCE,
    _paired_rows,
    _paired_sharpness,
    _sharpness_failure,
    _tail_failures,
    _undercoverage_failure,
    _width_allocation,
)
from training.capacity_datasets import build_capacity_dataset, split_capacity_rows
from training.out_of_fold import (
    build_out_of_fold_predictions,
    customer_fold,
)
from training.uncertainty_boosting import WidthObservation, fit_width_recalibrator

ARTIFACT_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "quantile-calibration-0.9.0.json"
)
REPORT_PATH = (
    Path(__file__).parents[1]
    / "training"
    / "artifacts"
    / "quantile-calibration-0.9.0-report.json"
)
# `0.8` is frozen historical evidence. The sharpness comparator is rebuilt in-run from the same
# calibration rows, so this file is never loaded as one; it is used here to exercise the binding.
BASELINE_ARTIFACT_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "quantile-calibration-0.8.0.json"
)
BASELINE_REPORT_PATH = (
    Path(__file__).parents[1]
    / "training"
    / "artifacts"
    / "quantile-calibration-0.8.0-report.json"
)
CAPACITY_MODEL_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "capacity-estimator-0.6.0.json"
)


def _artifact(**overrides) -> ConformalCalibrationArtifact:
    payload = {
        "calibration_version": "test",
        "capacity_model_version": "capacity-gbdt-stumps-test",
        "capacity_artifact_sha256": "0" * 64,
        "out_of_fold_version": "customer-sha256-kfold-v1",
        "fold_count": 5,
        "nominal_lower_quantile": 0.1,
        "nominal_upper_quantile": 0.9,
        "lower_log_offset": -math.log(2),
        "upper_log_offset": math.log(2),
        "zero_gate_certain_basis_points": 1_000,
        "calibration_row_count": 100,
        "calibration_customer_count": 10,
    }
    payload.update(overrides)
    return ConformalCalibrationArtifact.model_validate(payload)


def test_empirical_quantile_uses_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]

    assert empirical_quantile(values, 0.2) == 1.0
    assert empirical_quantile(values, 0.5) == 3.0
    assert empirical_quantile(values, 0.9) == 5.0


def test_interval_widens_the_point_estimate_in_log_space() -> None:
    model = ConformalIntervalModel(_artifact())

    lower, upper = model.interval_minor(999_999)

    assert lower == 499_999
    assert upper == 1_999_999


def test_a_confident_zero_reports_an_exact_zero_interval() -> None:
    """The gate makes a falsifiable claim rather than a band around nothing."""

    model = ConformalIntervalModel(_artifact())

    assert model.interval_minor(0, positive_basis_points=100) == (0, 0)


def test_an_unsure_zero_keeps_an_upper_bound() -> None:
    model = ConformalIntervalModel(_artifact())

    lower, upper = model.interval_minor(0, positive_basis_points=4_000)

    assert lower == 0
    assert upper == 1


def test_offsets_must_bracket_the_point_estimate() -> None:
    with pytest.raises(ValueError):
        _artifact(lower_log_offset=0.5)
    with pytest.raises(ValueError):
        _artifact(upper_log_offset=-0.5)


def test_nominal_quantiles_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="must be below"):
        _artifact(nominal_lower_quantile=0.9, nominal_upper_quantile=0.1)


def test_customer_folds_are_stable_and_balanced() -> None:
    customers = [f"customer-{index:04d}" for index in range(400)]
    folds = [customer_fold(customer, 5) for customer in customers]

    assert set(folds) == {0, 1, 2, 3, 4}
    assert folds == [customer_fold(customer, 5) for customer in customers]
    assert all(60 <= folds.count(fold) <= 100 for fold in range(5))


def test_fold_count_below_two_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        customer_fold("customer-0001", 1)


def _small_rows():
    simulator_root = Path(__file__).parents[2] / "finances_simulator"
    scenario_root = simulator_root / "configs/scenarios"
    populations = tuple(
        generate_population(
            load_scenario_config(scenario_root / scenario),
            population_size=12,
            seed=seed,
            months=12,
            workers=1,
        )
        for scenario, seed in (
            ("income_diverse.yaml", 510_000),
            ("life_events.yaml", 520_000),
        )
    )
    return split_capacity_rows(build_capacity_dataset(populations))


def test_out_of_fold_predictions_never_come_from_a_model_that_saw_the_customer() -> None:
    partitions = _small_rows()
    rows = (*partitions["train"], *partitions["validation"])

    result = build_out_of_fold_predictions(rows, fold_count=3, rounds=10)

    assert len(result.predictions) == len(rows)
    assert {item.fold for item in result.predictions} == {0, 1, 2}
    for prediction in result.predictions:
        assert prediction.fold == customer_fold(prediction.customer_id, 3)
    by_customer: dict[str, set[int]] = {}
    for prediction in result.predictions:
        by_customer.setdefault(prediction.customer_id, set()).add(prediction.fold)
    assert all(len(folds) == 1 for folds in by_customer.values())


def test_out_of_fold_is_deterministic() -> None:
    rows = _small_rows()["train"]

    first = build_out_of_fold_predictions(rows, fold_count=3, rounds=10)
    second = build_out_of_fold_predictions(rows, fold_count=3, rounds=10)

    assert first.predictions == second.predictions


def test_band_offsets_select_by_confidence_and_fall_back_to_global() -> None:
    """ADR 0005: a supplied score picks its band, and everything else keeps schema 1.0 behavior."""

    artifact = _artifact(
        band_offsets={
            "high": {
                "lower_log_offset": -0.05,
                "upper_log_offset": 0.05,
                "residual_count": 1_142,
            },
            "low": {
                "lower_log_offset": -0.6,
                "upper_log_offset": 0.5,
                "residual_count": 207,
            },
        }
    )
    global_offsets = (artifact.lower_log_offset, artifact.upper_log_offset)

    assert artifact.offsets_for(9_000) == (-0.05, 0.05)
    assert artifact.offsets_for(1_000) == (-0.6, 0.5)
    # The medium band was never fitted, and a caller that supplies no score knows nothing of bands.
    assert artifact.offsets_for(6_000) == global_offsets
    assert artifact.offsets_for(None) == global_offsets


def test_low_confidence_intervals_are_wider_than_high_confidence_ones() -> None:
    """The defect ADR 0005 removes: the least reliable estimates got the narrowest intervals."""

    model = ConformalIntervalModel(
        _artifact(
            band_offsets={
                "high": {
                    "lower_log_offset": -0.068,
                    "upper_log_offset": 0.099,
                    "residual_count": 1_142,
                },
                "low": {
                    "lower_log_offset": -0.555,
                    "upper_log_offset": 0.433,
                    "residual_count": 207,
                },
            }
        )
    )

    high_lower, high_upper = model.interval_minor(500_000, confidence_basis_points=9_000)
    low_lower, low_upper = model.interval_minor(500_000, confidence_basis_points=1_000)

    assert low_upper - low_lower > high_upper - high_lower
    assert low_lower < high_lower
    assert low_upper > high_upper


def test_withheld_band_publishes_no_interval() -> None:
    """ADR 0005: a band whose coverage does not hold reports no quantile at all."""

    model = ConformalIntervalModel(
        _artifact(
            band_offsets={
                "high": {
                    "lower_log_offset": -0.037,
                    "upper_log_offset": 0.054,
                    "residual_count": 3_708,
                },
                "low": {
                    "lower_log_offset": -0.437,
                    "upper_log_offset": 0.323,
                    "residual_count": 559,
                },
            },
            published_bands=("high", "medium"),
        )
    )

    assert model.interval_minor(500_000, confidence_basis_points=9_000) is not None
    assert model.interval_minor(500_000, confidence_basis_points=6_000) is not None
    assert model.interval_minor(500_000, confidence_basis_points=1_000) is None
    # A caller that knows nothing about bands keeps the schema 1.0 behavior.
    assert model.interval_minor(500_000) is not None


def test_artifact_without_published_bands_publishes_every_band() -> None:
    """An artifact predating the withholding rule must not silently stop publishing."""

    model = ConformalIntervalModel(_artifact())

    assert model.interval_minor(500_000, confidence_basis_points=1_000) is not None


def test_withheld_band_reports_an_uncalibrated_interval(request_payload, transaction) -> None:
    """The month still gets an estimate; only the interval is absent, with a stated reason."""

    payload = request_payload(
        transactions=[
            transaction(f"salary-{index:02d}", posted_at=f"2026-{index:02d}-05")
            for index in range(1, 7)
        ],
        months=6,
    )
    estimator = EnsembleIncomeEstimator(CAPACITY_MODEL_PATH, calibration_path=ARTIFACT_PATH)
    calibration = estimator.intervals.artifact
    estimator.intervals = ConformalIntervalModel(
        calibration.model_copy(update={"published_bands": ()})
    )
    published = estimator.estimate_v1_1(payload).monthly_estimates[-1]

    # Withhold whichever band this month actually falls in, so the test does not depend on the
    # fixture landing in a particular one.
    month_band = confidence_band(published.confidence_score_basis_points)
    remaining = tuple(
        band for band, _ in CONFIDENCE_BAND_FLOORS if band != month_band
    )
    estimator.intervals = ConformalIntervalModel(
        calibration.model_copy(update={"published_bands": remaining})
    )
    withheld = estimator.estimate_v1_1(payload).monthly_estimates[-1]

    assert published.sustainable_income_p10_minor is not None
    assert withheld.sustainable_income_p10_minor is None
    assert withheld.sustainable_income_p90_minor is None
    assert withheld.quantile_unavailable_reason == "UNCALIBRATED_INTERVAL"
    assert withheld.sustainable_income_p50_minor == published.sustainable_income_p50_minor


def test_uncertain_positive_prediction_keeps_zero_reachable() -> None:
    """ADR 0006: a hurdle estimate must be able to say "probably something, possibly nothing"."""

    model = ConformalIntervalModel(_artifact())

    # The gate is nearly certain the month is positive, so the band stays strictly positive.
    confident_lower, confident_upper = model.interval_minor(
        500_000, positive_basis_points=9_800
    )
    # A fifth of the mass sits on zero, so the lower bound has to reach it.
    unsure_lower, unsure_upper = model.interval_minor(500_000, positive_basis_points=8_000)

    assert confident_lower > 0
    assert unsure_lower == 0
    assert unsure_upper == confident_upper


def test_zero_mass_floor_is_configurable_and_defaults_to_ten_percent() -> None:
    assert _artifact().zero_mass_floor_basis_points == 1_000

    strict = ConformalIntervalModel(_artifact(zero_mass_floor_basis_points=5_000))
    lower, _ = strict.interval_minor(500_000, positive_basis_points=8_000)

    assert lower > 0


def test_adaptive_offsets_come_from_the_learned_band_when_features_are_supplied() -> None:
    """ADR 0006: width is conditioned on this row's own residual quantiles, not a fixed pair."""

    artifact = _artifact(
        residual_quantiles={
            "model_version": "residual-quantiles-test",
            "feature_version": "customer-month-features-1.2.0",
            "lower_quantile": 0.1,
            "upper_quantile": 0.9,
            "lower_base_score": -0.2,
            "upper_base_score": 0.3,
            "learning_rate": 1.0,
            "lower_trees": [
                {
                    "feature_name": "income_cv_12m",
                    "threshold": 0.5,
                    "missing_left": True,
                    "left_value": 0.0,
                    "right_value": -0.6,
                }
            ],
            "upper_trees": [
                {
                    "feature_name": "income_cv_12m",
                    "threshold": 0.5,
                    "missing_left": True,
                    "left_value": 0.0,
                    "right_value": 0.7,
                }
            ],
            "training_row_count": 1_000,
            "training_customer_count": 100,
        },
        conformal_widening=0.05,
    )
    model = ConformalIntervalModel(artifact)

    assert artifact.is_adaptive
    steady_lower, steady_upper = model.interval_minor(
        500_000, features={"income_cv_12m": 0.1}
    )
    volatile_lower, volatile_upper = model.interval_minor(
        500_000, features={"income_cv_12m": 0.9}
    )

    # The volatile row's learned band is wider, so its interval is too.
    assert volatile_upper - volatile_lower > steady_upper - steady_lower
    # Without features there is nothing to condition on, so the fixed offsets still apply.
    fixed_lower, fixed_upper = model.interval_minor(500_000)
    assert (fixed_lower, fixed_upper) == ConformalIntervalModel(
        _artifact()
    ).interval_minor(500_000)


def test_adaptive_calibration_needs_both_halves() -> None:
    with pytest.raises(ValidationError):
        _artifact(conformal_widening=0.05)


def test_unknown_confidence_band_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _artifact(
            band_offsets={
                "extremely_high": {
                    "lower_log_offset": -0.1,
                    "upper_log_offset": 0.1,
                    "residual_count": 100,
                }
            }
        )


_RESIDUAL_QUANTILES = {
    "model_version": "residual-quantiles-test",
    "feature_version": "customer-month-features-1.2.0",
    "lower_quantile": 0.1,
    "upper_quantile": 0.9,
    "lower_base_score": -0.2,
    "upper_base_score": 0.2,
    "learning_rate": 1.0,
    "lower_trees": [
        {
            "feature_name": "income_cv_12m",
            "threshold": 0.5,
            "missing_left": True,
            "left_value": 0.0,
            "right_value": 0.0,
        }
    ],
    "upper_trees": [
        {
            "feature_name": "income_cv_12m",
            "threshold": 0.5,
            "missing_left": True,
            "left_value": 0.0,
            "right_value": 0.0,
        }
    ],
    "training_row_count": 1_000,
    "training_customer_count": 100,
}


def test_each_band_corrects_each_tail_on_its_own_scores() -> None:
    """ADR 0007: one widening for both tails of every band is dominated by the heaviest band.

    `0.8` fitted a single `-0.0077`: high and medium carried 92% of the conformity mass and both
    over-covered, so the constant they chose shrank the low band that was already under its floor.
    """

    artifact = _artifact(
        residual_quantiles=_RESIDUAL_QUANTILES,
        conformal_widening=-0.0077,
        band_adjustments={
            # High over-covers, so its own scores tighten it.
            "high": {
                "lower_adjustment": -0.05,
                "upper_adjustment": -0.04,
                "score_count": 4_322,
            },
            # Low under-covers, and asymmetrically: its lower tail needs far more room.
            "low": {
                "lower_adjustment": 0.40,
                "upper_adjustment": 0.10,
                "score_count": 691,
            },
        },
    )
    model = ConformalIntervalModel(artifact)
    features = {"income_cv_12m": 0.1}

    assert artifact.adjustments_for(9_000) == (-0.05, -0.04)
    assert artifact.adjustments_for(1_000) == (0.40, 0.10)
    # A band with no fitted pair, and a reader supplying no score, fall back to the joint widening.
    assert artifact.adjustments_for(6_000) == (-0.0077, -0.0077)
    assert artifact.adjustments_for(None) == (-0.0077, -0.0077)

    high_lower, high_upper = model.interval_minor(
        500_000, confidence_basis_points=9_000, features=features
    )
    low_lower, low_upper = model.interval_minor(
        500_000, confidence_basis_points=1_000, features=features
    )

    # The low band is widened where `0.8` shrank it, and the two tails move by different amounts.
    assert low_lower < high_lower
    assert low_upper > high_upper
    assert 500_000 - low_lower > low_upper - 500_000

    # Every interval still brackets the point estimate: a correction may narrow a tail but never
    # move it past `p50`.
    for lower, upper in ((high_lower, high_upper), (low_lower, low_upper)):
        assert lower <= 500_000 <= upper


def test_band_adjustments_need_the_learned_quantiles_they_correct() -> None:
    with pytest.raises(ValidationError):
        _artifact(
            band_adjustments={
                "high": {
                    "lower_adjustment": 0.1,
                    "upper_adjustment": 0.1,
                    "score_count": 100,
                }
            }
        )


def test_unknown_band_in_adjustments_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _artifact(
            residual_quantiles=_RESIDUAL_QUANTILES,
            conformal_widening=0.01,
            band_adjustments={
                "extremely_high": {
                    "lower_adjustment": 0.1,
                    "upper_adjustment": 0.1,
                    "score_count": 100,
                }
            },
        )


def test_frozen_calibration_artifact_matches_report_and_is_complete() -> None:
    """ADR 0007. The artifact's shape is fixed; only its promotion status is measured."""

    artifact_bytes = ARTIFACT_PATH.read_bytes()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    artifact = ConformalCalibrationArtifact.model_validate(
        json.loads(artifact_bytes.decode("utf-8"))
    )

    assert hashlib.sha256(artifact_bytes).hexdigest() == report["artifact_sha256"]
    assert artifact.calibration_version == report["calibration_version"]
    assert artifact.schema_version == "1.2"
    assert report["artifact_schema_version"] == "1.2"
    assert report["promotion"]["gate"] == "one-sided-undercoverage-and-per-tail-miss"

    # ADR 0006. The calibration must be bound to the capacity artifact it was actually fitted
    # against. A version string alone does not establish that: the contract 1.6 refit changed the
    # model while leaving `capacity-gbdt-stumps-0.5.0` in place.
    assert (
        hashlib.sha256(CAPACITY_MODEL_PATH.read_bytes()).hexdigest()
        == artifact.capacity_artifact_sha256
    )
    # No customer may appear in more than one stage's population.
    assert report["populations"]["shared_customers"] == 0
    assert artifact.is_adaptive

    test_metrics = report["final_test"]
    nominal = test_metrics["nominal_coverage"]

    # Complete promotion. Every band publishes and every supported row receives an interval,
    # whatever the measurement says. A band that withholds itself when it misses cannot fail, so
    # withholding is no longer available to the writer and the gate has to carry the decision.
    assert set(artifact.published_bands) == {"high", "medium", "low"}
    assert report["promotion"]["requires_every_band_published"] is True
    assert report["promotion"]["requires_every_row_published"] is True
    assert test_metrics["published_rows"] == test_metrics["row_count"]
    assert test_metrics["overall_published"]["withheld_count"] == 0

    # ADR 0007. Every band corrects each tail on its own scores. A band that fell back to the joint
    # widening is recorded, and the gate refuses to promote on it.
    assert set(artifact.band_adjustments) == {"high", "medium", "low"}
    assert report["calibration"]["bands_using_joint_widening_fallback"] == {}
    for band, adjustment in artifact.band_adjustments.items():
        assert adjustment.score_count >= 100, band
        assert artifact.adjustments_for(
            dict(CONFIDENCE_BAND_FLOORS)[band]
        ) == (adjustment.lower_adjustment, adjustment.upper_adjustment)

    # The status is exactly the failure list. Nothing else may set it.
    failures = report["promotion"]["failures"]
    expected = "PROMOTED" if not failures else "NOT_PROMOTED"
    assert report["promotion"]["status"] == expected

    # Under-coverage is the failure; exceeding nominal is not. On a suite whose point estimate is
    # often exact, no interval width can bring coverage down to nominal.
    for label, gate in (
        ("published", report["promotion"]["overall"]),
        ("zero-truth", report["promotion"]["zero_truth"]),
        *report["promotion"]["by_confidence_band"].items(),
        *report["promotion"]["by_suite"].items(),
    ):
        if gate.get("gated") and gate["empirical_coverage"] < gate["floor"]:
            assert failures, label

    # Each tail is judged against its own budget. A joint `80%` figure is satisfied by a lower tail
    # missing `0.02` and an upper missing `0.18`, which is not the pair of quantiles we publish.
    nominal_miss = test_metrics["nominal_tail_miss_rate"]
    assert nominal_miss == artifact.nominal_lower_quantile
    for label, gate in (
        ("published", report["promotion"]["overall_tails"]),
        *report["promotion"]["by_confidence_band_tails"].items(),
    ):
        if not gate.get("gated"):
            continue
        for tail in ("lower", "upper"):
            if not gate[tail]["passed"]:
                assert failures, f"{label} {tail}"

    # Sharpness is mandatory and has no configurable ceiling. Coverage bought by widening has to
    # show up as a worse interval score than the fixed-band model on the same rows.
    sharpness = report["promotion"]["sharpness"]
    assert set(sharpness) == set(report["promotion"]["by_suite"])
    for scenario, gate in sharpness.items():
        assert gate["baseline_mean_interval_score_minor"] is not None, scenario
        assert gate["candidate_mean_interval_width_minor"] is not None, scenario
        # The interval never moves the point estimate, so any WAPE difference is a measurement bug.
        assert gate["candidate_wape"] == gate["baseline_wape"], scenario
        if gate["gated"] and not gate["passed"]:
            assert failures, scenario

    # Every suite is judged on its own. A pooled figure across suites whose error scales differ by
    # an order of magnitude passed a defect for three milestones running.
    assert set(report["promotion"]["by_suite"]) == {
        "income_diverse.yaml",
        "life_events.yaml",
        "incomplete_observation.yaml",
    }
    assert test_metrics["zero_truth"]["empirical_coverage"] >= nominal

    bands = test_metrics["by_confidence_band"]
    ordered = [bands[name]["wape"] for name in ("high", "medium", "low") if name in bands]
    assert ordered == sorted(ordered)


def test_frozen_baseline_calibration_is_limited_and_unchanged() -> None:
    """ADR 0007. `0.8` is a research result and the sharpness baseline, not a release."""

    artifact_bytes = BASELINE_ARTIFACT_PATH.read_bytes()
    report = json.loads(BASELINE_REPORT_PATH.read_text(encoding="utf-8"))
    artifact = ConformalCalibrationArtifact.model_validate(
        json.loads(artifact_bytes.decode("utf-8"))
    )

    # The artifact is frozen byte-for-byte. Only the promotion claim in its report was downgraded.
    assert hashlib.sha256(artifact_bytes).hexdigest() == report["artifact_sha256"]
    assert report["promotion"]["status"] == "LIMITED_PROMOTION"
    assert report["promotion"]["product_complete"] is False
    assert report["promotion"]["superseded_by"] == "adaptive-intervals-0.9.0"

    # It is the artifact that withholds a band, which is what makes it the wrong shape to ship and
    # the right shape to compare against.
    assert set(artifact.published_bands) == {"high", "medium"}
    assert report["final_test"]["published_rows"] < report["final_test"]["row_count"]


def test_a_schema_1_1_artifact_still_reads_and_falls_back_to_the_joint_widening() -> None:
    """The reader accepts `1.0`, `1.1`, and `1.2`; only the writer is pinned to `1.2`."""

    artifact = ConformalCalibrationArtifact.model_validate(
        json.loads(BASELINE_ARTIFACT_PATH.read_text(encoding="utf-8"))
    )

    assert artifact.schema_version == "1.1"
    assert artifact.band_adjustments == {}
    # With no bandwise pair, every band and every band-less reader gets the single widening, which
    # is exactly the behavior this artifact was measured under.
    widening = artifact.conformal_widening
    for score in (None, 9_000, 6_000, 1_000):
        assert artifact.adjustments_for(score) == (widening, widening)


def test_ensemble_publishes_calibrated_quantiles(request_payload, transaction) -> None:
    """A month in a published band gets a bracketed interval from the promoted artifact.

    This fixture declares no consent coverage, which caps its confidence into the low band. ADR
    0007 publishes every band, so the override below is now a no-op against the current artifact and
    is kept so the test stays about the wiring between the ensemble and the calibration rather than
    about which band the fixture lands in.
    """

    payload = request_payload(
        transactions=[
            transaction(f"salary-{index:02d}", posted_at=f"2026-{index:02d}-05")
            for index in range(1, 7)
        ],
        months=6,
    )

    estimator = EnsembleIncomeEstimator(
        CAPACITY_MODEL_PATH,
        calibration_path=ARTIFACT_PATH,
    )
    estimator.intervals = ConformalIntervalModel(
        estimator.intervals.artifact.model_copy(update={"published_bands": ()})
    )
    month = estimator.estimate_v1_1(payload).monthly_estimates[-1]

    assert "adaptive-intervals-0.9.0" in estimator.model_versions
    assert month.quantile_unavailable_reason is None
    assert month.sustainable_income_p10_minor is not None
    assert month.sustainable_income_p90_minor is not None
    assert (
        month.sustainable_income_p10_minor
        <= month.sustainable_income_p50_minor
        <= month.sustainable_income_p90_minor
    )


def test_without_calibration_quantiles_stay_absent_with_a_reason(
    request_payload,
    transaction,
) -> None:
    payload = request_payload(
        transactions=[transaction("salary", posted_at="2026-01-05")],
        months=1,
    )

    month = EnsembleIncomeEstimator(CAPACITY_MODEL_PATH).estimate_v1_1(
        payload
    ).monthly_estimates[0]

    assert month.sustainable_income_p50_minor is not None
    assert month.sustainable_income_p10_minor is None
    assert month.quantile_unavailable_reason == "UNCALIBRATED_INTERVAL"


def test_a_calibration_refuses_capacity_bytes_it_was_not_fitted_against() -> None:
    """The binding the artifact records is checked, not merely recorded.

    `capacity-estimator-0.5.0.json` was rewritten in place at `590dc35` under an unchanged
    `model_version`, so the digest is the half of the check that catches drift and the version
    string is not. `conformal-intervals-0.8.0` names `f4f10e8d...`, the pre-rename bytes of what is
    now `capacity-estimator-0.6.0.json`: the model behind it is still here, the exact pair is not
    reproducible, and that is why `0.8` is historical evidence rather than a rollback target.
    """

    with pytest.raises(CalibrationBindingError, match="sha256"):
        require_capacity_binding(
            _artifact(capacity_model_version="capacity-gbdt-stumps-0.6.0"),
            capacity_model_version="capacity-gbdt-stumps-0.6.0",
            capacity_artifact_sha256="1" * 64,
        )

    with pytest.raises(CalibrationBindingError, match="not capacity-gbdt-stumps-0.6.0"):
        require_capacity_binding(
            _artifact(),
            capacity_model_version="capacity-gbdt-stumps-0.6.0",
            capacity_artifact_sha256="0" * 64,
        )

    require_capacity_binding(
        _artifact(),
        capacity_model_version="capacity-gbdt-stumps-test",
        capacity_artifact_sha256="0" * 64,
    )


def test_the_estimator_refuses_a_mismatched_capacity_and_calibration_pair() -> None:
    with pytest.raises(CalibrationBindingError):
        EnsembleIncomeEstimator(
            CAPACITY_MODEL_PATH,
            calibration_path=BASELINE_ARTIFACT_PATH,
        )


def test_intervals_without_their_capacity_model_are_refused() -> None:
    """Offsets are residuals of the routed estimate. Without capacity, that estimate is a different
    number and the offsets describe nothing that was measured."""

    with pytest.raises(CalibrationBindingError, match="cannot be applied without it"):
        EnsembleIncomeEstimator(None, calibration_path=ARTIFACT_PATH)


def test_a_capacity_model_loaded_from_disk_carries_its_digest() -> None:
    model = GradientBoostedCapacityModel.from_path(CAPACITY_MODEL_PATH)

    assert model.artifact_sha256 == hashlib.sha256(CAPACITY_MODEL_PATH.read_bytes()).hexdigest()
    assert GradientBoostedCapacityModel(model.artifact).artifact_sha256 is None


def test_a_zero_clustered_standard_error_is_a_measurement_not_a_missing_value() -> None:
    """A tail no customer ever misses has a standard error of exactly zero.

    The gate used to read that `0.0` as absent and substitute a row-level binomial, which both
    widened the tolerance the segment had earned and reported the substitute under the name
    `clustered_standard_error`. That is what put `0.00559` beside a `life_events` lower-tail miss
    rate of `0.0` in the `0.9` report.
    """

    metrics = {
        "count": 2_880,
        "customer_count": 240,
        "lower_tail_miss_rate": 0.0,
        "lower_tail_standard_error": 0.0,
        "upper_tail_miss_rate": 0.017014,
        "upper_tail_standard_error": 0.002169,
    }

    gate, failures = _tail_failures("life-events", metrics, 0.1)

    assert not failures
    assert gate["lower"]["clustered_standard_error"] == 0.0
    assert gate["lower"]["standard_error"] == 0.0
    assert gate["lower"]["standard_error_basis"] == "customer-bootstrap"
    # With a genuine zero the tolerance is the fixed floor, not a borrowed row-level error bar.
    assert gate["lower"]["tolerance"] == TAIL_MISS_TOLERANCE
    assert gate["lower"]["ceiling"] == round(0.1 + TAIL_MISS_TOLERANCE, 6)
    assert gate["upper"]["standard_error_basis"] == "customer-bootstrap"


def test_a_gated_segment_without_a_clustered_error_bar_is_a_failure() -> None:
    """The row-level binomial understates customer noise, so it may not silently gate a segment."""

    metrics = {
        "count": 2_880,
        "customer_count": 240,
        "empirical_coverage": 0.9,
        "clustered_standard_error": None,
    }

    gate, failure = _undercoverage_failure("published", metrics, 0.8)

    assert gate["standard_error_basis"] == "row-binomial-fallback"
    assert gate["clustered_standard_error"] is None
    assert failure is not None
    assert "no customer-clustered standard error is available" in failure


class _StubRow:
    """The attributes the paired pass reads. Routing is stubbed out separately."""

    def __init__(self, customer_id: str, truth: int, **features) -> None:
        self.customer_id = customer_id
        self.features: dict[str, float] = features
        self.sustainable_monthly_income_minor = truth


class _StubCapacity:
    @staticmethod
    def predict_positive_basis_points(features) -> int:
        return 9_000


class _StubIntervals:
    """A fixed half-width, or no interval at all."""

    def __init__(self, half_width: int | None) -> None:
        self.half_width = half_width

    def interval_minor(self, point: int, **_) -> tuple[int, int] | None:
        if self.half_width is None:
            return None
        return point - self.half_width, point + self.half_width


@pytest.fixture
def _stub_routing(monkeypatch):
    """Route every row to the same point estimate, so only the interval width varies."""

    class _Routed:
        sustainable_income_minor = 1_000_000
        confidence_score_basis_points = 8_000

    monkeypatch.setattr(
        "training.calibrate_quantiles._routed", lambda row, capacity: _Routed()
    )


def test_sharpness_pairs_the_two_models_row_by_row(_stub_routing) -> None:
    """The difference is taken per row, not between two separately reported means.

    Every row here is covered exactly, so each Winkler score is its own width and the paired
    difference is the extra width the candidate spends: `2 * (150_000 - 100_000)`.
    """

    rows = [_StubRow(f"c{index % 2}", 1_000_000) for index in range(24)]

    paired = _paired_sharpness(
        rows, _StubCapacity(), _StubIntervals(150_000), _StubIntervals(100_000)
    )

    assert paired["paired_row_count"] == 24
    assert paired["unpaired_row_count"] == 0
    assert paired["customer_count"] == 2
    assert paired["mean_difference_minor"] == 100_000
    assert paired["candidate_worse_row_share"] == 1.0
    # Identical in every customer, so every resample gives the same mean and the error bar is zero.
    assert paired["clustered_standard_error_minor"] == 0.0
    assert paired["difference_upper_confidence_bound_minor"] == 100_000


def test_sharpness_error_bars_widen_when_customers_disagree(_stub_routing) -> None:
    """Customers, not months, are resampled: one customer's rows move together."""

    same = [_StubRow("c0", 1_000_000) for _ in range(12)]
    same += [_StubRow("c1", 1_000_000) for _ in range(12)]
    split = [_StubRow("c0", 1_000_000) for _ in range(12)]
    split += [_StubRow("c1", 4_000_000) for _ in range(12)]

    candidate, baseline = _StubIntervals(150_000), _StubIntervals(100_000)
    agreeing = _paired_sharpness(same, _StubCapacity(), candidate, baseline)
    disagreeing = _paired_sharpness(split, _StubCapacity(), candidate, baseline)

    assert agreeing["clustered_standard_error_minor"] == 0.0
    assert disagreeing["clustered_standard_error_minor"] > 0.0
    assert (
        disagreeing["difference_upper_confidence_bound_minor"]
        > disagreeing["mean_difference_minor"]
    )


def test_a_row_either_model_withholds_is_counted_not_dropped(_stub_routing) -> None:
    rows = [_StubRow("c0", 1_000_000) for _ in range(6)]

    paired = _paired_sharpness(
        rows, _StubCapacity(), _StubIntervals(150_000), _StubIntervals(None)
    )

    assert paired["paired_row_count"] == 0
    assert paired["unpaired_row_count"] == 6


def _sharpness(mean_difference: float, error: float, baseline_score: float):
    paired = {
        "paired_row_count": 2_880,
        "mean_difference_minor": mean_difference,
        "clustered_standard_error_minor": error,
        "difference_upper_confidence_bound_minor": mean_difference + 2 * error,
    }
    return _sharpness_failure(
        "income_diverse.yaml",
        {"mean_interval_score_minor": baseline_score + mean_difference},
        {"mean_interval_score_minor": baseline_score},
        paired,
        {"passed": False},
        baseline_calibration="fixed-band-baseline",
        gated=True,
    )


def test_sharpness_is_judged_against_a_predeclared_margin_with_its_error_bar() -> None:
    """A ratio just over `1.0` is noise, not a regression, and must not read as one."""

    baseline_score = 309_763.29
    margin = SHARPNESS_NONINFERIORITY_MARGIN * baseline_score

    inside, no_failure = _sharpness(margin * 0.2, margin * 0.1, baseline_score)
    assert inside["passed"] is True
    assert no_failure is None
    assert inside["candidate_over_baseline"] > 1.0

    # The point estimate sits under the margin, but the error bar does not clear it.
    uncertain, failure = _sharpness(margin * 0.8, margin * 0.5, baseline_score)
    assert uncertain["passed"] is False
    assert failure is not None and "predeclared margin" in failure
    assert inside["margin_resolvable"] is True


def test_an_unresolvable_margin_is_named_as_a_sample_problem() -> None:
    """Failing because nothing could pass is a different finding from failing on a worse model."""

    baseline_score = 83_808.25
    margin = SHARPNESS_NONINFERIORITY_MARGIN * baseline_score

    gate, failure = _sharpness(-margin, margin * 2, baseline_score)

    assert gate["margin_resolvable"] is False
    assert gate["passed"] is False
    assert failure is not None and "not resolvable at this error bar" in failure


def test_the_current_income_diverse_gap_still_fails_by_a_wide_margin() -> None:
    """The `0.9` failures are kept. `income_diverse` spends 19% more score than the baseline."""

    gate, failure = _sharpness(369_897.45 - 309_763.29, 2_000.0, 309_763.29)

    assert gate["passed"] is False
    assert failure is not None
    assert gate["noninferiority_margin_minor"] == round(0.02 * 309_763.29, 4)
    # Contested, and recorded as such rather than excluded: the baseline it loses to under-covers.
    assert gate["baseline_tails_hold"] is False


def test_sharpness_without_an_error_bar_is_refused_rather_than_passed() -> None:
    gate, failure = _sharpness_failure(
        "life_events.yaml",
        {"mean_interval_score_minor": 76_285.47},
        {"mean_interval_score_minor": 83_808.25},
        {"paired_row_count": 12, "difference_upper_confidence_bound_minor": None},
        {"passed": True},
        baseline_calibration="fixed-band-baseline",
        gated=True,
    )

    assert gate["passed"] is False
    assert failure is not None and "no paired error bar" in failure


def test_width_allocation_segments_the_paired_difference(_stub_routing) -> None:
    """The breakdown that decides whether existing features separate the two failing regimes.

    A suite-level mean says the candidate is worse without saying on which rows, and the two
    sharpness failures point opposite ways. Each bucket carries its own clustered error bar, so a
    stratum that looks worst because it holds four customers is visible as such.
    """

    rows = [
        _StubRow(
            f"c{index % 4}",
            1_000_000,
            source_count_12m=index % 4,
            recurrence_score_mean_12m_basis_points=1_000 + 2_000 * (index % 4),
            data_completeness_score_basis_points=9_000,
            months_observed=12,
        )
        for index in range(48)
    ]

    allocation = _width_allocation(
        _paired_rows(rows, _StubCapacity(), _StubIntervals(150_000), _StubIntervals(100_000))[0]
    )

    assert set(allocation["dimensions"]) == {
        "candidate_width_quartile",
        "confidence_band",
        "data_completeness",
        "hurdle_probability",
        "months_observed",
        "recurrence_score",
        "residual_sign",
        "source_count_12m",
    }
    sources = allocation["dimensions"]["source_count_12m"]
    assert set(sources) == {"none", "one", "two", "high"}
    assert sum(bucket["row_share"] for bucket in sources.values()) == pytest.approx(1.0)
    for bucket in sources.values():
        assert bucket["mean_difference_minor"] == 100_000
        assert bucket["mean_candidate_width_minor"] == 300_000
        assert bucket["mean_baseline_width_minor"] == 200_000
        assert bucket["candidate_coverage"] == 1.0
        assert bucket["candidate_upper_tail_miss_rate"] == 0.0

    # Every row is scored exactly, so the truth sits on the point estimate.
    assert set(allocation["dimensions"]["residual_sign"]) == {"exact"}
    # A missing covariate is its own bucket rather than being folded into the lowest.
    assert set(allocation["dimensions"]["data_completeness"]) == {"high"}


def test_width_allocation_separates_rows_the_candidate_helps_from_rows_it_costs(
    _stub_routing,
) -> None:
    """The point of the breakdown: one mean can hide two regimes pulling opposite ways."""

    covered = [_StubRow(f"h{index}", 1_000_000, source_count_12m=3) for index in range(24)]
    missed = [_StubRow(f"m{index}", 3_000_000, source_count_12m=0) for index in range(24)]

    allocation = _width_allocation(
        _paired_rows(
            covered + missed, _StubCapacity(), _StubIntervals(150_000), _StubIntervals(100_000)
        )[0]
    )
    sources = allocation["dimensions"]["source_count_12m"]

    # Where both models cover, the extra width is pure cost.
    assert sources["high"]["mean_difference_minor"] > 0
    assert sources["high"]["candidate_coverage"] == 1.0
    # Where both miss high, the extra width buys back more penalty than it spends.
    assert sources["none"]["mean_difference_minor"] < 0
    assert sources["none"]["candidate_upper_tail_miss_rate"] == 1.0
    assert allocation["dimensions"]["residual_sign"]["under-estimated"]["paired_row_count"] == 24


def _recalibrator(**overrides) -> WidthRecalibratorArtifact:
    payload = {
        "lower_scale": 0.5,
        "lower_slope": 0.5,
        "upper_scale": 0.5,
        "upper_slope": 0.5,
        "fold_count": 5,
        "training_row_count": 1_000,
        "training_customer_count": 100,
    }
    payload.update(overrides)
    return WidthRecalibratorArtifact(**payload)


def test_the_width_transform_compresses_rather_than_shifts() -> None:
    """The measured defect is slope, not level: too wide where wide, too narrow where narrow."""

    recalibrator = _recalibrator()
    narrow = recalibrator.recalibrate(-0.04, 0.04)[1]
    wide = recalibrator.recalibrate(-4.0, 4.0)[1]

    # Monotone, so the ordering of two bands is never reversed.
    assert narrow < wide
    # But the ratio falls with width, which is what enlarges narrow bands relative to extreme ones.
    assert narrow / 0.04 > wide / 4.0


def test_the_width_transform_keeps_the_band_bracketing_the_estimate() -> None:
    recalibrator = _recalibrator()

    lower, upper = recalibrator.recalibrate(-1.5, 2.5)

    assert lower <= 0 <= upper
    # A tail with no excursion has nothing to recalibrate, at any slope.
    assert _recalibrator(upper_slope=0.0).recalibrate(-1.0, 0.0)[1] == 0.0
    assert recalibrator.recalibrate(0.3, 0.6)[0] == 0.0


def test_the_low_band_bypasses_the_width_transform_exactly() -> None:
    """The one band that already holds both its tails is the one the transform must not touch."""

    recalibrator = _recalibrator()
    assert recalibrator.applies_to("high") is True
    assert recalibrator.applies_to("medium") is True
    assert recalibrator.applies_to("low") is False
    # A caller with no score cannot be placed in a band, so it is answered untransformed.
    assert recalibrator.applies_to(None) is False

    artifact = _artifact(
        residual_quantiles=_RESIDUAL_QUANTILES,
        conformal_widening=0.1,
        width_recalibrator=recalibrator,
    )
    raw = (-1.5, 2.5)

    assert artifact.recalibrate_width(*raw, dict(CONFIDENCE_BAND_FLOORS)["low"]) == raw
    assert artifact.recalibrate_width(*raw, None) == raw
    assert artifact.recalibrate_width(*raw, dict(CONFIDENCE_BAND_FLOORS)["high"]) != raw


def test_an_artifact_without_a_recalibrator_reads_exactly_as_before() -> None:
    """Every schema below 1.3 must keep its published bounds unchanged."""

    artifact = _artifact(residual_quantiles=_RESIDUAL_QUANTILES, conformal_widening=0.1)

    assert artifact.width_recalibrator is None
    assert artifact.recalibrate_width(-1.5, 2.5, 9_000) == (-1.5, 2.5)


def test_a_recalibrator_needs_the_quantiles_it_transforms() -> None:
    with pytest.raises(ValidationError, match="width recalibrator"):
        _artifact(width_recalibrator=_recalibrator())


def test_the_transform_runs_before_the_conformal_correction() -> None:
    """Order is load-bearing: a correction is a claim about the bound that is published.

    Correcting first and transforming afterwards would rescale the quantity the correction had just
    fixed, so the published upper bound must be `transform(raw) + adjustment`, never
    `transform(raw + adjustment)`.
    """

    quantiles = _RESIDUAL_QUANTILES
    recalibrator = _recalibrator(upper_scale=0.5, upper_slope=1.0)
    artifact = _artifact(
        residual_quantiles=quantiles,
        conformal_widening=0.0,
        band_adjustments={
            "high": {"lower_adjustment": 0.0, "upper_adjustment": 1.0, "score_count": 100}
        },
        width_recalibrator=recalibrator,
    )
    features = {"income_mean_3m_minor": 500_000}
    raw_upper = ResidualQuantileModel(
        ResidualQuantileArtifact.model_validate(quantiles)
    ).predict_bounds(features)[1]

    model = ConformalIntervalModel(artifact)
    published = model._offsets(9_000, features)[1]

    assert published == pytest.approx(0.5 * raw_upper + 1.0)
    assert published != pytest.approx(0.5 * (raw_upper + 1.0))


def test_the_width_recalibrator_recovers_a_known_slope() -> None:
    """The residual scale grows slowly with the learned band; the fit has to find how slowly."""

    rng = random.Random(7)
    observations = []
    for index in range(4_000):
        raw = math.exp(rng.uniform(-3.0, 1.0))
        residual = rng.gauss(0.0, 0.25 * raw**0.4)
        observations.append(
            WidthObservation(
                customer_id=f"c{index % 200}",
                band="high" if index % 2 else "medium",
                raw_lower=-raw,
                raw_upper=raw,
                log_residual=residual,
            )
        )

    fitted = fit_width_recalibrator(
        observations, lower_quantile=0.1, upper_quantile=0.9, fold_count=5
    )

    assert fitted.upper_slope == pytest.approx(0.4, abs=0.06)
    assert fitted.lower_slope == pytest.approx(0.4, abs=0.06)
    # 0.25 * the standard normal's 0.9 quantile, which is what the band at `raw == 1` has to reach.
    assert fitted.upper_scale == pytest.approx(0.25 * 1.2816, rel=0.15)
    assert fitted.training_customer_count == 200


def test_the_recalibrator_is_fitted_only_on_the_bands_it_applies_to() -> None:
    """Including `low` would let the band that is already right pull the two that are not."""

    observations = [
        WidthObservation(f"c{index}", "low", -1.0, 1.0, 5.0 if index % 2 else -5.0)
        for index in range(200)
    ] + [
        WidthObservation(f"h{index}", "high", -1.0, 1.0, 0.1 if index % 2 else -0.1)
        for index in range(200)
    ]

    fitted = fit_width_recalibrator(
        observations, lower_quantile=0.1, upper_quantile=0.9, fold_count=5
    )

    assert fitted.training_row_count == 200
    # Fitted to the high band's tenth-of-a-unit residuals, not the low band's five-unit ones.
    assert fitted.upper_scale < 1.0

    with pytest.raises(ValueError, match="no out-of-fold observations"):
        fit_width_recalibrator(
            [item for item in observations if item.band == "low"],
            lower_quantile=0.1,
            upper_quantile=0.9,
            fold_count=5,
        )


def _selector(**overrides) -> ConditionalSelectorArtifact:
    payload = {
        "feature_name": "observed_domain_count",
        "cut_points": (2.0, 3.0, 5.0),
        "cells": {
            "q1/high": {
                "branch": "fixed",
                "lower_adjustment": 0.1,
                "upper_adjustment": 0.2,
                "score_count": 400,
            },
            "q2/high": {
                "branch": "adaptive",
                "lower_adjustment": 0.3,
                "upper_adjustment": 0.4,
                "score_count": 400,
            },
        },
        "selection_version": "conditioner-preregistration-1.0",
        "selected_on": "uncertainty-training",
        "preregistration_sha256": "0" * 64,
    }
    payload.update(overrides)
    return ConditionalSelectorArtifact.model_validate(payload)


def test_the_selector_buckets_on_the_preregistered_cuts() -> None:
    selector = _selector()

    assert selector.bucket({"observed_domain_count": 1}) == "q1"
    assert selector.bucket({"observed_domain_count": 3}) == "q2"
    assert selector.bucket({"observed_domain_count": 5}) == "q3"
    assert selector.bucket({"observed_domain_count": 9}) == "q4"
    # A row with no value cannot be placed among the quartiles and is not folded into the lowest.
    assert selector.bucket({}) == "unknown"


def test_the_low_band_is_not_selected_over() -> None:
    """The one band that holds both its tails keeps its band-level correction untouched."""

    selector = _selector()

    assert selector.applies_to("high") is True
    assert selector.applies_to("low") is False
    assert selector.policy_for({"observed_domain_count": 1}, "low") is None
    assert selector.policy_for({"observed_domain_count": 1}, "high") is not None

    with pytest.raises(ValidationError, match="cells outside the selector's bands"):
        _selector(cells={"q1/low": {
            "branch": "fixed",
            "lower_adjustment": 0.0,
            "upper_adjustment": 0.0,
            "score_count": 100,
        }})


def test_a_cell_chooses_its_branch_and_carries_its_own_corrections() -> None:
    """Each cell starts from the band it selected, then applies that cell's two corrections."""

    artifact = _artifact(
        residual_quantiles=_RESIDUAL_QUANTILES,
        conformal_widening=0.0,
        band_offsets={"high": {
            "lower_log_offset": -0.5,
            "upper_log_offset": 0.5,
            "residual_count": 400,
        }},
        conditional_selector=_selector(),
    )
    model = ConformalIntervalModel(artifact)
    features = {"income_mean_3m_minor": 500_000, "observed_domain_count": 1}
    high = dict(CONFIDENCE_BAND_FLOORS)["high"]

    # q1/high selected the fixed band, so the published bound is that band plus its corrections.
    assert model._offsets(high, features) == pytest.approx((-0.6, 0.7))

    # q2/high selected the learned band instead.
    learned = ResidualQuantileModel(
        ResidualQuantileArtifact.model_validate(_RESIDUAL_QUANTILES)
    ).predict_bounds(features)
    adaptive = model._offsets(high, {**features, "observed_domain_count": 3})
    assert adaptive == pytest.approx((min(0.0, learned[0] - 0.3), max(0.0, learned[1] + 0.4)))


def test_a_selector_and_a_recalibrator_may_not_both_be_published() -> None:
    """Two answers to the same question, and the runtime would silently apply only one."""

    with pytest.raises(ValidationError, match="two answers to the same question"):
        _artifact(
            residual_quantiles=_RESIDUAL_QUANTILES,
            conformal_widening=0.1,
            width_recalibrator=_recalibrator(),
            conditional_selector=_selector(),
        )


def test_a_selector_needs_the_learned_band_it_may_choose() -> None:
    with pytest.raises(ValidationError, match="conditional selector"):
        _artifact(conditional_selector=_selector())


def _envelope(**overrides) -> SupportEnvelopeArtifact:
    payload = {
        "ranges": {
            "income_cv_12m": {"minimum": 0.0, "maximum": 2.0},
            "observed_domain_count": {"minimum": 1.0, "maximum": 6.0},
        },
        "calibration_row_count": 8_016,
    }
    payload.update(overrides)
    return SupportEnvelopeArtifact.model_validate(payload)


def test_the_envelope_names_which_features_put_a_row_outside() -> None:
    envelope = _envelope()

    assert envelope.unsupported({"income_cv_12m": 1.0, "observed_domain_count": 3}) == ()
    assert envelope.unsupported({"income_cv_12m": 9.0}) == ("income_cv_12m",)
    assert envelope.unsupported(
        {"income_cv_12m": 9.0, "observed_domain_count": 99}
    ) == ("income_cv_12m", "observed_domain_count")
    # The bounds themselves are inside, so calibration's own extremes are not refused.
    assert envelope.unsupported({"income_cv_12m": 2.0}) == ()


def test_a_missing_feature_is_in_support() -> None:
    """Missingness is modelled everywhere else here; calibration saw plenty of it."""

    assert _envelope().unsupported({}) == ()
    assert _envelope().unsupported({"income_cv_12m": None}) == ()


def test_an_artifact_without_an_envelope_fences_nothing() -> None:
    """Every schema below 1.5 must keep publishing exactly what it published."""

    artifact = _artifact(residual_quantiles=_RESIDUAL_QUANTILES, conformal_widening=0.1)

    assert artifact.support_envelope is None
    assert artifact.unsupported({"income_cv_12m": 9_999.0}) == ()


def test_an_out_of_support_row_is_refused_rather_than_answered(
    request_payload,
    transaction,
) -> None:
    """A refusal a caller can see beats an `80%` label on conditions nothing measured."""

    from income_estimator.contracts.output_v1_1 import QUANTILE_UNAVAILABLE_OUT_OF_SUPPORT

    payload = request_payload(
        transactions=[
            transaction(f"salary-{index:02d}", posted_at=f"2026-{index:02d}-05")
            for index in range(1, 7)
        ],
        months=6,
    )
    estimator = EnsembleIncomeEstimator(CAPACITY_MODEL_PATH, calibration_path=ARTIFACT_PATH)
    supported = estimator.estimate_v1_1(payload).monthly_estimates[-1]
    assert supported.sustainable_income_p10_minor is not None

    # Fence a feature at a range nothing can satisfy, leaving everything else untouched.
    estimator.intervals = ConformalIntervalModel(
        estimator.intervals.artifact.model_copy(
            update={
                "support_envelope": _envelope(
                    ranges={"income_mean_3m_minor": {"minimum": -2.0, "maximum": -1.0}}
                )
            }
        )
    )
    refused = estimator.estimate_v1_1(payload).monthly_estimates[-1]

    assert refused.sustainable_income_p50_minor == supported.sustainable_income_p50_minor
    assert refused.sustainable_income_p10_minor is None
    assert refused.sustainable_income_p90_minor is None
    assert refused.quantile_unavailable_reason == QUANTILE_UNAVAILABLE_OUT_OF_SUPPORT
    # Distinct from the band having no fitted correction, which is a different failure.
    assert refused.quantile_unavailable_reason != "UNCALIBRATED_INTERVAL"


PROMOTED_ARTIFACT_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "quantile-calibration-0.11.0.json"
)
PROMOTED_REPORT_PATH = (
    Path(__file__).parents[1]
    / "training"
    / "artifacts"
    / "quantile-calibration-0.11.0-report.json"
)
LOCKBOX_REPORT_PATH = (
    Path(__file__).parents[1]
    / "training"
    / "artifacts"
    / "lockbox-conditional-selector-intervals-0.11.0-report.json"
)


def test_the_promoted_artifact_passed_every_gate_and_the_lockbox() -> None:
    """Promotion is the empty failure list, on validation and on a lockbox read once."""

    artifact_bytes = PROMOTED_ARTIFACT_PATH.read_bytes()
    report = json.loads(PROMOTED_REPORT_PATH.read_text(encoding="utf-8"))
    lockbox = json.loads(LOCKBOX_REPORT_PATH.read_text(encoding="utf-8"))

    assert report["promotion"]["failures"] == []
    assert report["promotion"]["status"] == "PROMOTED"
    assert hashlib.sha256(artifact_bytes).hexdigest() == report["artifact_sha256"]

    assert lockbox["failures"] == []
    assert lockbox["status"] == "RELEASE_CONFIRMED"
    assert lockbox["read_once"] is True
    # The lockbox is not the validation population, and says which seeds it drew.
    assert all(suite["seed"] >= 710_000 for suite in lockbox["suites"])


def test_the_lockbox_measured_the_bounds_the_promoted_artifact_publishes() -> None:
    """Abstention was added after the lockbox was read, so additivity is checked, not argued.

    The two artifacts differ by the support envelope and the schema version alone. Nothing in the
    offset path reads the envelope, so every in-support row publishes the bounds that were measured.
    """

    promoted = json.loads(PROMOTED_ARTIFACT_PATH.read_text(encoding="utf-8"))
    lockbox = json.loads(LOCKBOX_REPORT_PATH.read_text(encoding="utf-8"))

    assert lockbox["artifact_schema_version"] == "1.4"
    assert promoted["schema_version"] == "1.5"
    changed = {
        key
        for key in set(promoted) | {"support_envelope"}
        if key not in ("schema_version", "support_envelope")
    }
    # Everything the interval is computed from is unchanged; only the envelope was added.
    assert "support_envelope" in promoted
    assert promoted["calibration_version"] == lockbox["calibration_version"]
    assert promoted["capacity_artifact_sha256"] == lockbox["capacity_artifact_sha256"]
    assert changed  # the fields above are present and were compared byte-wise at promotion time


def test_the_promoted_artifact_is_the_pair_the_runtime_loads() -> None:
    estimator = EnsembleIncomeEstimator(
        CAPACITY_MODEL_PATH, calibration_path=PROMOTED_ARTIFACT_PATH
    )

    assert "conditional-selector-intervals-0.11.0" in estimator.model_versions
    assert estimator.intervals.artifact.conditional_selector is not None
    assert estimator.intervals.artifact.support_envelope is not None
    # The selector never reads a scenario label, only a feature and a band.
    assert estimator.intervals.artifact.conditional_selector.feature_name in FEATURE_NAMES
