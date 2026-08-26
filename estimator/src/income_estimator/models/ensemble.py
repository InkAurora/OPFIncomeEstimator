"""Deterministic ensemble routing for estimator 0.6.

The plan draws one ensemble box, but the components do not predict the same thing. Cash-flow and
recurring-stream reconstruction produce `realized_income_month`; the capacity model produces
`sustainable_monthly_income`. Those are distinct targets under ADR 0001, so blending them would
produce a number with no definition. This module therefore routes two ensembles, one per target,
and publishes both.

Routing is deterministic and documented rather than learned. A learned meta-model may only be
fitted on out-of-fold base predictions, which do not exist yet; fitting one on in-sample component
output would leak training performance into the weights.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from income_estimator.contracts.output_v1_1 import (
    QUANTILE_UNAVAILABLE_OUT_OF_SUPPORT,
    QUANTILE_UNAVAILABLE_UNCALIBRATED,
    ComponentEstimateV11,
    ConfidenceComponentV11,
)
from income_estimator.models.capacity import GradientBoostedCapacityModel
from income_estimator.models.quantiles import ConformalIntervalModel

ENSEMBLE_VERSION = "deterministic-routing-0.6.0"

STABLE_VOLATILITY_MAXIMUM_RATIO = 0.1
COMPLETE_COVERAGE_BASIS_POINTS = 10_000

CONFIDENCE_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("data_coverage", 3_000),
    ("history_length", 2_000),
    ("income_stability", 2_000),
    ("classification_certainty", 1_500),
    ("component_agreement", 1_500),
)


@dataclass(frozen=True, slots=True)
class MonthlyEnsembleResult:
    """One month of routed estimates plus the evidence behind them."""

    realized_income_minor: int
    sustainable_income_minor: int | None
    sustainable_lower_minor: int | None
    sustainable_upper_minor: int | None
    components: tuple[ComponentEstimateV11, ...]
    disagreement_basis_points: int | None
    confidence_score_basis_points: int
    confidence_components: tuple[ConfidenceComponentV11, ...]
    routing_reason_codes: tuple[str, ...]
    quantile_unavailable_reason: str | None


def _value(features: Mapping[str, float | int | None], name: str) -> int | None:
    value = features.get(name)
    return int(value) if value is not None else None


def _clamp_basis_points(value: float) -> int:
    return max(0, min(10_000, round(value)))


def _sustainable_candidates(
    features: Mapping[str, float | int | None],
    capacity: GradientBoostedCapacityModel | None,
) -> dict[str, tuple[int, str | None]]:
    """Every sustainable-income candidate, whether or not routing selects it."""

    candidates: dict[str, tuple[int, str | None]] = {}
    historical_median = _value(features, "income_median_12m_minor")
    if historical_median is not None:
        candidates["historical_median_12m"] = (historical_median, None)
    recurring = _value(features, "income_mean_3m_minor")
    if recurring is not None:
        candidates["recurring_stream_mean_3m"] = (recurring, None)
    cash_flow = _value(features, "income_1m_minor")
    if cash_flow is not None:
        candidates["cash_flow_last_month"] = (cash_flow, None)
    if capacity is not None:
        candidates["capacity_model"] = (
            capacity.predict_minor(features),
            capacity.artifact.model_version,
        )
    return candidates


def _route_sustainable(
    features: Mapping[str, float | int | None],
    candidates: Mapping[str, tuple[int, str | None]],
) -> tuple[str | None, tuple[str, ...]]:
    """Select one component and say why.

    The capacity model wins on held-out data in every measured segment except stable income, where
    last month's reconstruction is already the answer and the model only adds noise. That single
    exception is routed explicitly rather than averaged away. Conditioning it on full coverage as
    well was measured and rejected: on the intersection the model wins again, so the narrower rule
    made the ensemble worse than its own best component.
    """

    reasons: list[str] = []
    if "capacity_model" not in candidates:
        if "recurring_stream_mean_3m" in candidates:
            return "recurring_stream_mean_3m", ("CAPACITY_MODEL_UNAVAILABLE",)
        return None, ("NO_SUSTAINABLE_COMPONENT",)

    volatility = features.get("income_cv_12m")
    coverage = features.get("effective_consent_coverage_basis_points")
    stable = volatility is not None and volatility < STABLE_VOLATILITY_MAXIMUM_RATIO
    complete = coverage is not None and coverage >= COMPLETE_COVERAGE_BASIS_POINTS
    if stable and "cash_flow_last_month" in candidates:
        reasons.append("STABLE_INCOME_PREFERS_CASH_FLOW")
        reasons.append("COMPLETE_COVERAGE" if complete else "PARTIAL_OR_UNDECLARED_COVERAGE")
        return "cash_flow_last_month", tuple(reasons)

    reasons.append("CAPACITY_MODEL_SELECTED")
    if coverage is None:
        reasons.append("COVERAGE_UNDECLARED")
    elif not complete:
        reasons.append("PARTIAL_COVERAGE")
    if volatility is None:
        reasons.append("VOLATILITY_UNKNOWN")
    elif volatility >= STABLE_VOLATILITY_MAXIMUM_RATIO:
        reasons.append("VOLATILE_INCOME")
    return "capacity_model", tuple(reasons)


def _disagreement(candidates: Mapping[str, tuple[int, str | None]]) -> int | None:
    """Relative spread across candidates, in basis points of the largest estimate."""

    values = [value for value, _ in candidates.values()]
    if len(values) < 2:
        return None
    highest = max(values)
    if highest == 0:
        return 0
    return round((highest - min(values)) * 10_000 / highest)


def _confidence(
    features: Mapping[str, float | int | None],
    disagreement_basis_points: int | None,
) -> tuple[int, tuple[ConfidenceComponentV11, ...]]:
    """Score confidence from coverage, history, stability, certainty, and agreement.

    Coverage caps the result. High confidence cannot coexist with known low coverage, so a
    customer whose consent hides half their accounts cannot be reported as well understood however
    tidy the visible half looks.
    """

    coverage = features.get("data_completeness_score_basis_points")
    months_observed = features.get("months_observed") or 0
    volatility = features.get("income_cv_12m")
    credits = features.get("credits_12m_minor") or 0
    probable = features.get("probable_income_12m_minor") or 0

    values = {
        "data_coverage": _clamp_basis_points(coverage if coverage is not None else 0),
        "history_length": _clamp_basis_points(min(12, months_observed) * 10_000 / 12),
        "income_stability": (
            _clamp_basis_points(10_000 * (1 - min(1.0, float(volatility))))
            if volatility is not None
            else 5_000
        ),
        "classification_certainty": (
            _clamp_basis_points(probable * 10_000 / credits) if credits else 5_000
        ),
        "component_agreement": (
            _clamp_basis_points(10_000 - disagreement_basis_points)
            if disagreement_basis_points is not None
            else 5_000
        ),
    }
    components = tuple(
        ConfidenceComponentV11(
            name=name,
            value_basis_points=values[name],
            weight_basis_points=weight,
        )
        for name, weight in CONFIDENCE_WEIGHTS
    )
    total_weight = sum(weight for _, weight in CONFIDENCE_WEIGHTS)
    score = sum(values[name] * weight for name, weight in CONFIDENCE_WEIGHTS) // total_weight
    coverage_cap = values["data_coverage"]
    return min(_clamp_basis_points(score), coverage_cap), components


def combine_month(
    realized_income_minor: int,
    features: Mapping[str, float | int | None],
    capacity: GradientBoostedCapacityModel | None,
    *,
    realized_components: Mapping[str, int],
    realized_selected: str,
    intervals: ConformalIntervalModel | None = None,
) -> MonthlyEnsembleResult:
    """Route both targets for one reference month and score confidence once."""

    candidates = _sustainable_candidates(features, capacity)
    selected, reasons = _route_sustainable(features, candidates)
    disagreement = _disagreement(candidates)
    score, confidence_components = _confidence(features, disagreement)

    components = tuple(
        ComponentEstimateV11(
            component=name,
            target="REALIZED_INCOME_MONTH",
            estimate_minor=value,
            weight_basis_points=10_000 if name == realized_selected else 0,
        )
        for name, value in sorted(realized_components.items())
    ) + tuple(
        ComponentEstimateV11(
            component=name,
            target="SUSTAINABLE_MONTHLY_INCOME",
            estimate_minor=value,
            weight_basis_points=10_000 if name == selected else 0,
            model_version=model_version,
        )
        for name, (value, model_version) in sorted(candidates.items())
    )
    sustainable = candidates[selected][0] if selected is not None else None
    lower: int | None = None
    upper: int | None = None
    quantile_reason: str | None = None
    if sustainable is None:
        quantile_reason = None
    elif intervals is None:
        quantile_reason = QUANTILE_UNAVAILABLE_UNCALIBRATED
    elif intervals.artifact.unsupported(features):
        # The calibration exists and does not cover this row. Publishing the interval anyway would
        # put an `80%` label on conditions nothing measured, which is the failure mode the held-out
        # stress suites showed and nothing at inference time could see.
        quantile_reason = QUANTILE_UNAVAILABLE_OUT_OF_SUPPORT
    else:
        bounds = intervals.interval_minor(
            sustainable,
            positive_basis_points=(
                capacity.predict_positive_basis_points(features)
                if capacity is not None
                else None
            ),
            confidence_basis_points=score,
            features=features,
        )
        # A band the calibration does not publish is uncalibrated for this month specifically. An
        # absent quantile is never a point estimate widened by a guess.
        if bounds is None:
            quantile_reason = QUANTILE_UNAVAILABLE_UNCALIBRATED
        else:
            lower, upper = bounds
    return MonthlyEnsembleResult(
        realized_income_minor=realized_income_minor,
        sustainable_income_minor=sustainable,
        sustainable_lower_minor=lower,
        sustainable_upper_minor=upper,
        components=components,
        disagreement_basis_points=disagreement,
        confidence_score_basis_points=score,
        confidence_components=confidence_components,
        routing_reason_codes=reasons,
        quantile_unavailable_reason=quantile_reason,
    )


__all__ = [
    "COMPLETE_COVERAGE_BASIS_POINTS",
    "CONFIDENCE_WEIGHTS",
    "ENSEMBLE_VERSION",
    "STABLE_VOLATILITY_MAXIMUM_RATIO",
    "MonthlyEnsembleResult",
    "combine_month",
]
