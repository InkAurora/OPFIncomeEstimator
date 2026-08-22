from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config

from income_estimator.models.quantiles import (
    ConformalCalibrationArtifact,
    ConformalIntervalModel,
    empirical_quantile,
)
from income_estimator.pipeline import EnsembleIncomeEstimator
from training.capacity_datasets import build_capacity_dataset, split_capacity_rows
from training.out_of_fold import (
    build_out_of_fold_predictions,
    customer_fold,
)

ARTIFACT_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "quantile-calibration-0.7.0.json"
)
REPORT_PATH = (
    Path(__file__).parents[1]
    / "training"
    / "artifacts"
    / "quantile-calibration-0.7.0-report.json"
)
CAPACITY_MODEL_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "capacity-estimator-0.5.0.json"
)


def _artifact(**overrides) -> ConformalCalibrationArtifact:
    payload = {
        "calibration_version": "test",
        "capacity_model_version": "capacity-gbdt-stumps-test",
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


def test_frozen_calibration_artifact_matches_report_and_is_promoted() -> None:
    artifact_bytes = ARTIFACT_PATH.read_bytes()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    artifact = ConformalCalibrationArtifact.model_validate(
        json.loads(artifact_bytes.decode("utf-8"))
    )

    assert hashlib.sha256(artifact_bytes).hexdigest() == report["artifact_sha256"]
    assert artifact.calibration_version == report["calibration_version"]
    assert artifact.out_of_fold_version == report["out_of_fold_version"]
    assert report["promotion"]["status"] == "PROMOTED"
    assert report["promotion"]["failures"] == []

    test_metrics = report["test"]
    nominal = test_metrics["nominal_coverage"]
    empirical = test_metrics["overall"]["empirical_coverage"]
    assert abs(empirical - nominal) <= report["promotion"]["coverage_tolerance"]

    bands = test_metrics["by_confidence_band"]
    ordered = [bands[name]["wape"] for name in ("high", "medium", "low") if name in bands]
    assert ordered == sorted(ordered)
    assert test_metrics["by_income_range"]["zero"]["empirical_coverage"] == 1.0


def test_ensemble_publishes_calibrated_quantiles(request_payload, transaction) -> None:
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
    month = estimator.estimate_v1_1(payload).monthly_estimates[-1]

    assert "conformal-intervals-0.7.0" in estimator.model_versions
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
