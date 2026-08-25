from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

pytest.importorskip("pyarrow")

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config

from income_estimator.models.quantiles import (
    CONFIDENCE_BAND_FLOORS,
    ConformalCalibrationArtifact,
    ConformalIntervalModel,
    confidence_band,
    empirical_quantile,
)
from income_estimator.pipeline import EnsembleIncomeEstimator
from training.capacity_datasets import build_capacity_dataset, split_capacity_rows
from training.out_of_fold import (
    build_out_of_fold_predictions,
    customer_fold,
)

ARTIFACT_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "quantile-calibration-0.9.0.json"
)
REPORT_PATH = (
    Path(__file__).parents[1]
    / "training"
    / "artifacts"
    / "quantile-calibration-0.9.0-report.json"
)
# ADR 0007 keeps `0.8` frozen as the fixed-band comparison the sharpness gate measures against.
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
