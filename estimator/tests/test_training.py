from __future__ import annotations

import hashlib
import json
from pathlib import Path

from income_estimator.models.transaction_classifier import (
    MODEL_FEATURE_NAMES,
    GradientBoostedTransactionClassifier,
)
from training.datasets import LabeledTransaction, split_by_customer
from training.gradient_boosting import fit_gradient_boosted_stumps
from training.metrics import classification_metrics


def _record(index: int, label: int) -> LabeledTransaction:
    values = dict.fromkeys(MODEL_FEATURE_NAMES, 0.0)
    values["description_has_payroll"] = float(label)
    values["description_has_transfer"] = float(not label)
    values["log_amount_minor"] = 13.0 if label else 10.0
    return LabeledTransaction(
        customer_id=f"customer-{index // 2}",
        transaction_id=f"transaction-{index}",
        label=label,
        economic_type="INCOME" if label else "OWN_TRANSFER",
        model_features=values,
        baseline_is_income=bool(label),
        hard_excluded=not bool(label),
        baseline_reason_codes=("SYNTHETIC_RULE",),
    )


def test_training_is_deterministic_and_artifact_is_portable() -> None:
    train = tuple(_record(index, index % 2) for index in range(64))
    validation = tuple(_record(100 + index, index % 2) for index in range(24))

    first = fit_gradient_boosted_stumps(train, validation, rounds=12)
    second = fit_gradient_boosted_stumps(train, validation, rounds=12)
    model = GradientBoostedTransactionClassifier(first)

    assert first == second
    assert first.trees
    assert model.predict_values_basis_points(train[1].model_features) > (
        model.predict_values_basis_points(train[0].model_features)
    )
    assert classification_metrics(validation, artifact=first)["f1"] == 1.0


def test_customer_split_has_no_overlap() -> None:
    records = tuple(_record(index, index % 2) for index in range(200))

    partitions = split_by_customer(records)
    customer_sets = {
        name: {record.customer_id for record in items}
        for name, items in partitions.items()
    }

    assert customer_sets["train"].isdisjoint(customer_sets["validation"])
    assert customer_sets["train"].isdisjoint(customer_sets["test"])
    assert customer_sets["validation"].isdisjoint(customer_sets["test"])


def test_frozen_candidate_artifact_matches_report_and_is_not_promoted() -> None:
    artifact_path = (
        Path(__file__).parents[1]
        / "training"
        / "artifacts"
        / "transaction-classifier-0.3.0.json"
    )
    report_path = artifact_path.with_name("transaction-classifier-0.3.0-report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == report[
        "artifact_sha256"
    ]
    assert report["promotion"]["status"] == "NOT_PROMOTED"
    assert report["metrics"]["test"]["candidate"]["f1"] == report["metrics"][
        "test"
    ]["baseline"]["f1"]
    assert report["metrics"]["test"]["candidate"][
        "hard_exclusion_false_negative_reasons"
    ] == {"REVERSED_ORIGINAL": 5}
