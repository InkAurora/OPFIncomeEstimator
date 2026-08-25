"""Out-of-fold capacity predictions for honest calibration.

Conformal calibration needs residuals from a model that never saw the row. The `0.5` artifact
cannot supply them: its residuals on `train` are in-sample, and `validation` was consumed twice
already, once to choose the tree count and once to choose the gate threshold. Calibrating on either
would produce intervals that look tighter than they are, and interval coverage is exactly what
`0.7` has to report honestly.

This module refits the hurdle once per fold and predicts only the fold it held out, so every row
gets a prediction from a model that never trained on its customer. Folds are assigned by customer,
never by row, because two months of one customer share almost everything.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from income_estimator.models.capacity import (
    CapacityEstimatorArtifact,
    GradientBoostedCapacityModel,
)
from income_estimator.models.ensemble import combine_month
from income_estimator.models.quantiles import confidence_band
from training.capacity_boosting import fit_capacity_model
from training.capacity_datasets import CapacityRow

OUT_OF_FOLD_VERSION = "customer-sha256-kfold-v1"
DEFAULT_FOLD_COUNT = 5


def customer_fold(customer_id: str, fold_count: int = DEFAULT_FOLD_COUNT) -> int:
    """Assign one customer to one fold, deterministically and independently of ordering."""

    if fold_count < 2:
        raise ValueError("fold_count must be at least 2")
    digest = sha256(f"{OUT_OF_FOLD_VERSION}:{customer_id}".encode()).digest()[:8]
    return int.from_bytes(digest, "big") % fold_count


@dataclass(frozen=True, slots=True)
class OutOfFoldPrediction:
    """One held-out prediction and the residual it implies on the log target."""

    customer_id: str
    reference_month: str
    fold: int
    predicted_minor: int
    truth_minor: int
    log_residual: float
    predicted_positive_basis_points: int
    is_zero_truth: bool
    confidence_basis_points: int


def _confidence_basis_points(
    row: CapacityRow,
    model: GradientBoostedCapacityModel,
) -> int:
    """Score the row with the model that held it out, never with the promoted one.

    ADR 0005 bands calibration residuals by confidence. Scoring these rows with the promoted model
    would reintroduce exactly the in-sample optimism out-of-fold prediction exists to remove, since
    that model trained on these customers.
    """

    realized = int(row.features.get("income_1m_minor") or 0)
    return combine_month(
        realized,
        row.features,
        model,
        realized_components={"recurring_streams_0_2": realized},
        realized_selected="recurring_streams_0_2",
    ).confidence_score_basis_points


@dataclass(frozen=True, slots=True)
class OutOfFoldResult:
    predictions: tuple[OutOfFoldPrediction, ...]
    fold_count: int
    fold_artifacts: tuple[CapacityEstimatorArtifact, ...]

    @property
    def positive_log_residuals(self) -> tuple[float, ...]:
        """Residuals for rows the gate kept positive, which are the ones an interval covers."""

        return tuple(
            item.log_residual for item in self.predictions if not item.is_zero_truth
        )

    def positive_log_residuals_by_band(self) -> dict[str, tuple[float, ...]]:
        """The same residuals, grouped by the confidence band each row was scored into."""

        grouped: dict[str, list[float]] = {}
        for item in self.predictions:
            if item.is_zero_truth:
                continue
            band = confidence_band(item.confidence_basis_points)
            grouped.setdefault(band, []).append(item.log_residual)
        return {band: tuple(values) for band, values in grouped.items()}


def _split_folds(
    rows: Sequence[CapacityRow],
    fold_count: int,
) -> list[tuple[tuple[CapacityRow, ...], tuple[CapacityRow, ...]]]:
    folds: list[list[CapacityRow]] = [[] for _ in range(fold_count)]
    for row in rows:
        folds[customer_fold(row.customer_id, fold_count)].append(row)
    result = []
    for index in range(fold_count):
        held_out = tuple(folds[index])
        remaining = tuple(
            row for other in range(fold_count) if other != index for row in folds[other]
        )
        result.append((remaining, held_out))
    return result


def build_out_of_fold_predictions(
    rows: Sequence[CapacityRow],
    *,
    fold_count: int = DEFAULT_FOLD_COUNT,
    rounds: int = 400,
) -> OutOfFoldResult:
    """Refit per fold and predict only the held-out customers of that fold.

    Each fold's own model still needs a validation set for its stopping decisions. Taking it from
    the next fold rather than from the held-out rows keeps the held-out fold untouched by every
    choice the model makes about itself.
    """

    folds = _split_folds(rows, fold_count)
    predictions: list[OutOfFoldPrediction] = []
    artifacts: list[CapacityEstimatorArtifact] = []
    for index, (remaining, held_out) in enumerate(folds):
        if not held_out:
            artifacts.append(None)  # type: ignore[arg-type]
            continue
        validation_fold = (index + 1) % fold_count
        validation = tuple(
            row
            for row in remaining
            if customer_fold(row.customer_id, fold_count) == validation_fold
        )
        train = tuple(
            row
            for row in remaining
            if customer_fold(row.customer_id, fold_count) != validation_fold
        )
        if not train or not validation:
            raise ValueError(
                f"fold {index} cannot be trained: {len(train)} train and "
                f"{len(validation)} validation rows"
            )
        artifact = fit_capacity_model(train, validation, rounds=rounds)
        artifacts.append(artifact)
        model = GradientBoostedCapacityModel(artifact)
        for row in held_out:
            predicted = model.predict_minor(row.features)
            truth = row.sustainable_monthly_income_minor
            predictions.append(
                OutOfFoldPrediction(
                    customer_id=row.customer_id,
                    reference_month=row.reference_month,
                    fold=index,
                    predicted_minor=predicted,
                    truth_minor=truth,
                    log_residual=math.log1p(truth) - math.log1p(predicted),
                    predicted_positive_basis_points=model.predict_positive_basis_points(
                        row.features
                    ),
                    is_zero_truth=truth == 0,
                    confidence_basis_points=_confidence_basis_points(row, model),
                )
            )
    return OutOfFoldResult(
        predictions=tuple(
            sorted(predictions, key=lambda item: (item.customer_id, item.reference_month))
        ),
        fold_count=fold_count,
        fold_artifacts=tuple(item for item in artifacts if item is not None),
    )


__all__ = [
    "DEFAULT_FOLD_COUNT",
    "OUT_OF_FOLD_VERSION",
    "OutOfFoldPrediction",
    "OutOfFoldResult",
    "build_out_of_fold_predictions",
    "customer_fold",
]
