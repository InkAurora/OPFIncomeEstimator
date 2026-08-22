"""Portable JSON gradient-boosted regressor for sustainable monthly income.

The artifact is dependency-free: plain decision stumps evaluated in Python, so runtime inference
needs no training framework. Training happens outside this package and joins private labels; only
the resulting artifact crosses back into runtime.

Missingness is modeled rather than imputed. Every stump records the direction an absent feature
takes, chosen during training by gain, so a customer with no observed cards is routed explicitly
instead of being handed a fabricated zero.

Prediction starts from a deterministic anchor rather than from a constant. Sustainable income is
close to a linear function of observed reconstructed income, which piecewise-constant stumps
approximate badly; boosting the log-ratio around an anchor feature means an empty model reproduces
that anchor exactly, and every tree can only move the estimate away from it for a measured reason.

The model is a hurdle: a logistic gate decides whether sustainable income is zero, and an anchored
regressor sizes it when it is not. A customer with no active source is a real and common outcome,
and on a log target its residual dwarfs every other row. Separating the two parts keeps zero
customers supported instead of dragging every other estimate toward zero.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CAPACITY_FEATURE_VERSION = "customer-month-features-1.1.0"


class CapacityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapacityStump(CapacityModel):
    feature_name: str = Field(min_length=1)
    threshold: float
    missing_left: bool
    left_value: float
    right_value: float


class CapacityEstimatorArtifact(CapacityModel):
    schema_version: Literal["1.1"] = "1.1"
    model_version: str = Field(min_length=1)
    feature_version: str = Field(min_length=1)
    feature_schema_fingerprint: str = Field(min_length=16, max_length=64)
    target: Literal["log1p_sustainable_monthly_income_minor"]
    anchor_feature_name: str = Field(min_length=1)
    feature_names: tuple[str, ...] = Field(min_length=1)
    base_score: float
    learning_rate: float = Field(gt=0, le=1)
    trees: tuple[CapacityStump, ...]
    gate_base_score: float
    gate_learning_rate: float = Field(gt=0, le=1)
    gate_threshold_basis_points: int = Field(ge=0, le=10_000)
    gate_trees: tuple[CapacityStump, ...]
    dataset_version: str = Field(min_length=1)
    split_version: str = Field(min_length=1)
    simulator_version: str = Field(min_length=1)
    income_target_version: str = Field(min_length=1)
    source_contract_versions: tuple[str, ...] = Field(min_length=1)
    input_contract_version: str = Field(min_length=1)
    training_rounds_requested: int = Field(gt=0)
    l2_regularization: float = Field(gt=0)
    minimum_leaf_size: int = Field(gt=0)
    maximum_bins: int = Field(gt=1)
    training_customer_count: int = Field(gt=0)
    validation_customer_count: int = Field(gt=0)
    training_row_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_trees(self) -> CapacityEstimatorArtifact:
        known = set(self.feature_names)
        unknown = sorted(
            {tree.feature_name for tree in (*self.trees, *self.gate_trees)} - known
        )
        if unknown:
            raise ValueError(f"trees reference unknown features: {unknown}")
        if self.anchor_feature_name not in known:
            raise ValueError("anchor_feature_name must be one of feature_names")
        return self


class GradientBoostedCapacityModel:
    """Evaluate a frozen capacity artifact without any training dependency."""

    def __init__(self, artifact: CapacityEstimatorArtifact) -> None:
        self.artifact = artifact

    @classmethod
    def from_path(cls, path: Path) -> GradientBoostedCapacityModel:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(CapacityEstimatorArtifact.model_validate(payload))

    def anchor_log(self, features: Mapping[str, float | int | None]) -> float:
        """Absent or negative anchors fall back to zero, which log1p maps to zero."""

        value = features.get(self.artifact.anchor_feature_name)
        return math.log1p(max(0.0, float(value))) if value is not None else 0.0

    @staticmethod
    def _score(
        features: Mapping[str, float | int | None],
        trees: tuple[CapacityStump, ...],
        base_score: float,
        learning_rate: float,
    ) -> float:
        score = base_score
        for tree in trees:
            value = features.get(tree.feature_name)
            if value is None:
                leaf = tree.left_value if tree.missing_left else tree.right_value
            else:
                leaf = tree.left_value if value <= tree.threshold else tree.right_value
            score += learning_rate * leaf
        return score

    def contributions(
        self,
        features: Mapping[str, float | int | None],
    ) -> dict[str, float]:
        """Exact additive contribution of each feature to the log estimate.

        Every tree is a stump, so it contributes ``learning_rate * leaf`` to exactly one feature.
        The decomposition is therefore exact by construction rather than sampled or approximated,
        as a general attribution method would have to be. Contributions plus the anchor and the
        base score reconstruct the prediction up to floating-point summation order.
        """

        totals: dict[str, float] = {}
        for tree in self.artifact.trees:
            value = features.get(tree.feature_name)
            if value is None:
                leaf = tree.left_value if tree.missing_left else tree.right_value
            else:
                leaf = tree.left_value if value <= tree.threshold else tree.right_value
            totals[tree.feature_name] = (
                totals.get(tree.feature_name, 0.0) + self.artifact.learning_rate * leaf
            )
        return totals

    def predict_positive_basis_points(
        self,
        features: Mapping[str, float | int | None],
    ) -> int:
        """Probability that sustainable income is above zero."""

        score = self._score(
            features,
            self.artifact.gate_trees,
            self.artifact.gate_base_score,
            self.artifact.gate_learning_rate,
        )
        probability = 1 / (1 + math.exp(-max(-40.0, min(40.0, score))))
        return round(probability * 10_000)

    def predict_log_target(self, features: Mapping[str, float | int | None]) -> float:
        return self.anchor_log(features) + self._score(
            features,
            self.artifact.trees,
            self.artifact.base_score,
            self.artifact.learning_rate,
        )

    def predict_minor(self, features: Mapping[str, float | int | None]) -> int:
        """Back-transform to minor units; income is never negative."""

        if (
            self.predict_positive_basis_points(features)
            < self.artifact.gate_threshold_basis_points
        ):
            return 0
        value = math.expm1(min(40.0, self.predict_log_target(features)))
        return max(0, math.floor(value + 0.5))


__all__ = [
    "CAPACITY_FEATURE_VERSION",
    "CapacityEstimatorArtifact",
    "CapacityStump",
    "GradientBoostedCapacityModel",
]
