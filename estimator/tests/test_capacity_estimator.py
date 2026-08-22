from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from finances_simulator.batch import generate_population
from finances_simulator.config import load_scenario_config

from income_estimator.models.capacity import (
    CapacityEstimatorArtifact,
    CapacityStump,
    GradientBoostedCapacityModel,
)
from training.capacity_boosting import ANCHOR_FEATURE_NAME, fit_capacity_model
from training.capacity_datasets import (
    build_capacity_dataset,
    split_capacity_rows,
)
from training.capacity_metrics import (
    BASELINES,
    best_baseline,
    evaluate_partition,
    promotion_decision,
    regression_metrics,
)
from training.datasets import customer_partition

ARTIFACT_PATH = (
    Path(__file__).parents[1] / "training" / "artifacts" / "capacity-estimator-0.5.0.json"
)
REPORT_PATH = (
    Path(__file__).parents[1]
    / "training"
    / "artifacts"
    / "capacity-estimator-0.5.0-report.json"
)


def _artifact(
    *,
    trees: tuple[CapacityStump, ...] = (),
    gate_trees: tuple[CapacityStump, ...] = (),
    base_score: float = 0.0,
    gate_base_score: float = 10.0,
    gate_threshold_basis_points: int = 5_000,
) -> CapacityEstimatorArtifact:
    return CapacityEstimatorArtifact(
        model_version="capacity-gbdt-stumps-test",
        feature_version="customer-month-features-1.1.0",
        feature_schema_fingerprint="0" * 32,
        target="log1p_sustainable_monthly_income_minor",
        anchor_feature_name=ANCHOR_FEATURE_NAME,
        feature_names=(ANCHOR_FEATURE_NAME, "credit_utilization_ratio"),
        base_score=base_score,
        learning_rate=1.0,
        trees=trees,
        gate_base_score=gate_base_score,
        gate_learning_rate=1.0,
        gate_threshold_basis_points=gate_threshold_basis_points,
        gate_trees=gate_trees,
        dataset_version="test",
        split_version="test",
        simulator_version="0.7.0",
        income_target_version="income-targets-1.0.0",
        source_contract_versions=("1.5",),
        input_contract_version="1.2",
        training_rounds_requested=1,
        l2_regularization=1.0,
        minimum_leaf_size=1,
        maximum_bins=32,
        training_customer_count=1,
        validation_customer_count=1,
        training_row_count=1,
    )


def test_empty_model_reproduces_its_anchor() -> None:
    """Boosting starts at the anchor, so a tree can only move it for a measured reason."""

    model = GradientBoostedCapacityModel(_artifact())

    assert model.predict_minor({ANCHOR_FEATURE_NAME: 600_000}) == 600_000
    assert model.predict_minor({ANCHOR_FEATURE_NAME: 0}) == 0


def test_absent_anchor_falls_back_to_zero() -> None:
    model = GradientBoostedCapacityModel(_artifact())

    assert model.predict_minor({ANCHOR_FEATURE_NAME: None}) == 0
    assert model.predict_minor({}) == 0


def test_gate_below_threshold_predicts_zero() -> None:
    """A closed gate returns zero regardless of what the regressor would have sized."""

    model = GradientBoostedCapacityModel(_artifact(gate_base_score=-10.0))

    assert model.predict_positive_basis_points({ANCHOR_FEATURE_NAME: 600_000}) < 5_000
    assert model.predict_minor({ANCHOR_FEATURE_NAME: 600_000}) == 0


def test_missing_feature_routes_by_recorded_direction() -> None:
    tree = CapacityStump(
        feature_name="credit_utilization_ratio",
        threshold=0.5,
        missing_left=True,
        left_value=math.log(2),
        right_value=0.0,
    )
    model = GradientBoostedCapacityModel(_artifact(trees=(tree,)))
    features = {ANCHOR_FEATURE_NAME: 100_000}

    assert model.predict_minor({**features, "credit_utilization_ratio": None}) == 200_001
    assert model.predict_minor({**features, "credit_utilization_ratio": 0.9}) == 100_000


def test_artifact_rejects_trees_on_unknown_features() -> None:
    tree = CapacityStump(
        feature_name="not_a_feature",
        threshold=0.0,
        missing_left=False,
        left_value=0.0,
        right_value=0.0,
    )

    with pytest.raises(ValueError, match="unknown features"):
        _artifact(trees=(tree,))


def _small_populations():
    simulator_root = Path(__file__).parents[2] / "finances_simulator"
    scenario_root = simulator_root / "configs/scenarios"
    return tuple(
        generate_population(
            load_scenario_config(scenario_root / scenario),
            population_size=10,
            seed=seed,
            months=12,
            workers=1,
        )
        for scenario, seed in (
            ("income_diverse.yaml", 210_000),
            ("life_events.yaml", 220_000),
        )
    )


def test_training_is_deterministic_and_artifact_is_portable(tmp_path: Path) -> None:
    rows = build_capacity_dataset(_small_populations())
    partitions = split_capacity_rows(rows)

    first = fit_capacity_model(partitions["train"], partitions["validation"], rounds=20)
    second = fit_capacity_model(partitions["train"], partitions["validation"], rounds=20)

    assert first == second
    path = tmp_path / "artifact.json"
    path.write_text(first.model_dump_json(indent=2), encoding="utf-8")
    loaded = GradientBoostedCapacityModel.from_path(path)

    assert loaded.artifact == first
    reference = GradientBoostedCapacityModel(first)
    assert [
        loaded.predict_minor(row.features) for row in partitions["test"]
    ] == [reference.predict_minor(row.features) for row in partitions["test"]]


def test_customer_split_keeps_partitions_disjoint() -> None:
    rows = build_capacity_dataset(_small_populations())
    partitions = split_capacity_rows(rows)
    customers = {
        name: {row.customer_id for row in items} for name, items in partitions.items()
    }

    assert customers["train"].isdisjoint(customers["validation"])
    assert customers["train"].isdisjoint(customers["test"])
    assert customers["validation"].isdisjoint(customers["test"])
    assert all(
        customer_partition(row.customer_id) == name
        for name, items in partitions.items()
        for row in items
    )


def test_dataset_rows_carry_features_and_private_target() -> None:
    rows = build_capacity_dataset(_small_populations())

    assert rows
    assert all(row.sustainable_monthly_income_minor >= 0 for row in rows)
    assert all("income_median_12m_minor" in row.features for row in rows)
    assert any(row.features["card_spend_3m_minor"] is None for row in rows)
    assert all(
        row.log_target == math.log1p(row.sustainable_monthly_income_minor) for row in rows
    )


def test_baselines_read_only_observed_features() -> None:
    rows = build_capacity_dataset(_small_populations())
    partitions = split_capacity_rows(rows)

    for name, predictor in BASELINES.items():
        metrics = regression_metrics(partitions["test"], predictor)
        assert metrics["count"] == len(partitions["test"]), name
        assert metrics["mean_absolute_error_minor"] >= 0


def test_promotion_gate_rejects_a_baseline_matching_candidate() -> None:
    """The gate demands strict improvement, so a copy of a baseline cannot pass."""

    rows = build_capacity_dataset(_small_populations())
    partitions = split_capacity_rows(rows)
    artifact = fit_capacity_model(partitions["train"], partitions["validation"], rounds=5)
    evaluation = evaluate_partition(partitions["test"], artifact)
    baseline_name, _ = best_baseline(evaluation)
    evaluation["candidate"] = evaluation[baseline_name]

    status, failures = promotion_decision(evaluation)

    assert status == "NOT_PROMOTED"
    assert any("MAE must improve" in failure for failure in failures)


def test_frozen_capacity_artifact_matches_report_and_is_promoted() -> None:
    artifact_bytes = ARTIFACT_PATH.read_bytes()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    artifact = CapacityEstimatorArtifact.model_validate(
        json.loads(artifact_bytes.decode("utf-8"))
    )

    assert hashlib.sha256(artifact_bytes).hexdigest() == report["artifact_sha256"]
    assert artifact.model_version == report["model_version"]
    assert artifact.feature_version == report["feature_version"]
    assert artifact.income_target_version == report["income_target_version"]
    assert artifact.input_contract_version == "1.2"
    assert report["promotion"]["status"] == "PROMOTED"
    assert report["promotion"]["failures"] == []

    test_metrics = report["evaluation"]["test"]
    candidate = test_metrics["candidate"]["overall"]["mean_absolute_error_minor"]
    assert candidate < report["promotion"]["best_baseline_mae_minor"]
    assert all(
        candidate < test_metrics[name]["overall"]["mean_absolute_error_minor"]
        for name in BASELINES
    )
    zero_band = test_metrics["candidate"]["segments"]["sustainable_income_range"]["zero"]
    assert zero_band["mean_absolute_error_minor"] == 0
