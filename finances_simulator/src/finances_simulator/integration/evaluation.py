"""Automatic population evaluation against physically isolated private truth."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from statistics import fmean, median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finances_simulator.batch.generation import GeneratedPopulation
from finances_simulator.domain.events import EconomicType
from finances_simulator.generation import GeneratedScenario
from finances_simulator.integration.adapter import build_estimator_input
from finances_simulator.integration.contracts import (
    EstimatorInputV1,
    IncomeEstimateV1,
    IncomeEstimator,
)


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"


class ErrorMetricV1(EvaluationModel):
    count: int = Field(ge=0)
    mean_absolute_error_minor: float | None = Field(default=None, ge=0)
    median_absolute_error_minor: float | None = Field(default=None, ge=0)


class ErrorBreakdownV1(ErrorMetricV1):
    value: str


class ClassificationCountV1(EvaluationModel):
    economic_type: str
    count: int = Field(ge=0)


class FalseClassificationV1(EvaluationModel):
    selected_transaction_count: int = Field(ge=0)
    false_classification_count: int = Field(ge=0)
    false_classification_rate: float = Field(ge=0, le=1)
    by_economic_type: tuple[ClassificationCountV1, ...]


class ConfidenceCoverageV1(EvaluationModel):
    interval_count: int = Field(ge=0)
    covered_count: int = Field(ge=0)
    coverage_rate: float = Field(ge=0, le=1)


class LifeEventErrorV1(EvaluationModel):
    around_events: ErrorMetricV1
    outside_events: ErrorMetricV1


class EvaluationReportV1(EvaluationModel):
    """Deterministic evaluation report. All monetary error units are minor units."""

    batch_id: str
    estimator_version: str
    population_size: int = Field(gt=0)
    evaluated_customer_months: int = Field(gt=0)
    overall: ErrorMetricV1
    by_income_type: tuple[ErrorBreakdownV1, ...]
    by_income_range: tuple[ErrorBreakdownV1, ...]
    by_consent_coverage: tuple[ErrorBreakdownV1, ...]
    life_event_error: LifeEventErrorV1
    false_income_classification: FalseClassificationV1
    confidence_interval_coverage: ConfidenceCoverageV1


@dataclass(frozen=True, slots=True)
class PopulationEvaluation:
    estimates: tuple[IncomeEstimateV1, ...]
    report: EvaluationReportV1


@dataclass(frozen=True, slots=True)
class _EvaluationPoint:
    absolute_error: int
    income_type: str
    income_range: str
    consent_coverage: str
    around_life_event: bool
    interval_covered: bool


def _metric(errors: list[int]) -> ErrorMetricV1:
    if not errors:
        return ErrorMetricV1(count=0)
    return ErrorMetricV1(
        count=len(errors),
        mean_absolute_error_minor=round(fmean(errors), 6),
        median_absolute_error_minor=round(float(median(errors)), 6),
    )


def _breakdown(
    points: list[_EvaluationPoint],
    attribute: Literal["income_type", "income_range", "consent_coverage"],
) -> tuple[ErrorBreakdownV1, ...]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for point in points:
        grouped[getattr(point, attribute)].append(point.absolute_error)
    return tuple(
        ErrorBreakdownV1(value=value, **_metric(errors).model_dump(exclude={"schema_version"}))
        for value, errors in sorted(grouped.items())
    )


def _income_range(value: int) -> str:
    if value == 0:
        return "0"
    if value < 250_000:
        return "1-249999"
    if value < 500_000:
        return "250000-499999"
    if value < 1_000_000:
        return "500000-999999"
    return "1000000+"


def _income_type(generated: GeneratedScenario) -> str:
    customer = generated.ground_truth.customers[0]
    value = getattr(customer, "income_profile", None)
    if value is None:
        value = getattr(customer, "employment_status", "UNKNOWN")
    return str(getattr(value, "value", value))


def _coverage_label(request: EstimatorInputV1) -> str:
    eligible = sum(item.eligible_record_count for item in request.coverage)
    observed = sum(item.observed_original_record_count for item in request.coverage)
    if eligible:
        basis_points = (observed * 10_000 + eligible // 2) // eligible
    elif request.coverage:
        basis_points = sum(
            item.effective_coverage_basis_points for item in request.coverage
        ) // len(request.coverage)
    else:
        basis_points = 10_000
    return f"{basis_points / 100:.2f}%"


def _life_event_window(generated: GeneratedScenario) -> set[str]:
    result: set[str] = set()
    truth = generated.ground_truth
    months = tuple(item.month for item in truth.customer_months)
    month_index = {month: index for index, month in enumerate(months)}
    for event in getattr(truth, "life_events", ()):
        event_month = event.effective_date[:7]
        index = month_index.get(event_month)
        if index is None:
            continue
        result.update(months[max(0, index - 1) : min(len(months), index + 2)])
    return result


def _call_estimator(
    estimator: IncomeEstimator | Callable[[EstimatorInputV1], IncomeEstimateV1],
    request: EstimatorInputV1,
) -> IncomeEstimateV1:
    estimate_method = getattr(estimator, "estimate", None)
    if callable(estimate_method):
        raw_result = estimate_method(request)
    elif callable(estimator):
        raw_result = estimator(request)
    else:
        raise TypeError("estimator must be callable or expose estimate(request)")
    dump = getattr(raw_result, "model_dump", None)
    result_payload = dump(mode="python") if callable(dump) else raw_result
    result = IncomeEstimateV1.model_validate(result_payload)
    expected_months = tuple(
        f"{item.year:04d}-{item.month:02d}"
        for item in _month_sequence(request.window_start, request.months)
    )
    actual_months = tuple(item.month for item in result.monthly_estimates)
    if result.run_id != request.run_id or result.customer_id != request.customer_id:
        raise ValueError("estimator result run/customer identity does not match input")
    if result.currency != request.currency:
        raise ValueError("estimator result currency does not match input")
    if actual_months != expected_months:
        raise ValueError("estimator result must contain every simulation month in order")
    known_ids = {item.transaction_id for item in request.transactions}
    contributed_ids = {
        transaction_id
        for item in result.monthly_estimates
        for transaction_id in item.contributing_transaction_ids
    }
    unknown_ids = contributed_ids - known_ids
    if unknown_ids:
        raise ValueError(
            f"estimator result references unknown transactions: {sorted(unknown_ids)}"
        )
    return result


def _month_sequence(window_start: str, count: int):
    from datetime import date

    from finances_simulator.simulation.primitives import month_start

    start = date.fromisoformat(window_start)
    return tuple(month_start(start, index) for index in range(count))


def _false_classifications(
    generated: GeneratedScenario,
    request: EstimatorInputV1,
    estimate: IncomeEstimateV1,
) -> tuple[set[str], Counter[str]]:
    selected_ids = {
        transaction_id
        for monthly in estimate.monthly_estimates
        for transaction_id in monthly.contributing_transaction_ids
    }
    observed_by_id = {item.transaction_id: item for item in request.transactions}
    truth_by_id = {
        item.entry_id: item for item in generated.ground_truth.transactions
    }
    false_types = {
        EconomicType.OWN_TRANSFER,
        EconomicType.LOAN_DISBURSEMENT,
        EconomicType.INVESTMENT_REDEMPTION,
    }
    counts: Counter[str] = Counter()
    for selected_id in selected_ids:
        observed = observed_by_id[selected_id]
        source_id = (
            observed.duplicate_of_transaction_id
            or observed.reversal_of_transaction_id
            or selected_id
        )
        truth = truth_by_id.get(source_id)
        if truth is not None and truth.economic_type in false_types:
            counts[truth.economic_type.value] += 1
    return selected_ids, counts


def evaluate_population(
    population: GeneratedPopulation,
    estimator: IncomeEstimator | Callable[[EstimatorInputV1], IncomeEstimateV1],
) -> PopulationEvaluation:
    """Run estimator for each member and aggregate required Phase-7 metrics."""

    estimates: list[IncomeEstimateV1] = []
    points: list[_EvaluationPoint] = []
    selected_ids: set[str] = set()
    false_type_counts: Counter[str] = Counter()
    estimator_versions: set[str] = set()

    for generated in population.members:
        request = build_estimator_input(generated)
        estimate = _call_estimator(estimator, request)
        estimates.append(estimate)
        estimator_versions.add(estimate.estimator_version)
        estimated_by_month = {
            item.month: item for item in estimate.monthly_estimates
        }
        event_window = _life_event_window(generated)
        income_type = _income_type(generated)
        coverage = _coverage_label(request)
        for truth in generated.ground_truth.customer_months:
            predicted = estimated_by_month[truth.month]
            points.append(
                _EvaluationPoint(
                    absolute_error=abs(
                        predicted.estimated_income_minor - truth.true_income_minor
                    ),
                    income_type=income_type,
                    income_range=_income_range(truth.true_income_minor),
                    consent_coverage=coverage,
                    around_life_event=truth.month in event_window,
                    interval_covered=(
                        predicted.confidence_lower_minor
                        <= truth.true_income_minor
                        <= predicted.confidence_upper_minor
                    ),
                )
            )
        member_selected, member_false = _false_classifications(
            generated, request, estimate
        )
        selected_ids.update(member_selected)
        false_type_counts.update(member_false)

    if len(estimator_versions) != 1:
        raise ValueError("all estimator results must use one estimator_version")
    errors = [point.absolute_error for point in points]
    around_errors = [
        point.absolute_error for point in points if point.around_life_event
    ]
    outside_errors = [
        point.absolute_error for point in points if not point.around_life_event
    ]
    covered_count = sum(point.interval_covered for point in points)
    false_count = sum(false_type_counts.values())
    selected_count = len(selected_ids)
    false_classification_types = (
        EconomicType.INVESTMENT_REDEMPTION.value,
        EconomicType.LOAN_DISBURSEMENT.value,
        EconomicType.OWN_TRANSFER.value,
    )
    report = EvaluationReportV1(
        batch_id=population.batch_id,
        estimator_version=next(iter(estimator_versions)),
        population_size=population.population_size,
        evaluated_customer_months=len(points),
        overall=_metric(errors),
        by_income_type=_breakdown(points, "income_type"),
        by_income_range=_breakdown(points, "income_range"),
        by_consent_coverage=_breakdown(points, "consent_coverage"),
        life_event_error=LifeEventErrorV1(
            around_events=_metric(around_errors),
            outside_events=_metric(outside_errors),
        ),
        false_income_classification=FalseClassificationV1(
            selected_transaction_count=selected_count,
            false_classification_count=false_count,
            false_classification_rate=(
                round(false_count / selected_count, 8) if selected_count else 0
            ),
            by_economic_type=tuple(
                ClassificationCountV1(
                    economic_type=economic_type,
                    count=false_type_counts[economic_type],
                )
                for economic_type in sorted(false_classification_types)
            ),
        ),
        confidence_interval_coverage=ConfidenceCoverageV1(
            interval_count=len(points),
            covered_count=covered_count,
            coverage_rate=round(covered_count / len(points), 8),
        ),
    )
    return PopulationEvaluation(estimates=tuple(estimates), report=report)


__all__ = [
    "ConfidenceCoverageV1",
    "ErrorBreakdownV1",
    "ErrorMetricV1",
    "EvaluationReportV1",
    "FalseClassificationV1",
    "LifeEventErrorV1",
    "PopulationEvaluation",
    "evaluate_population",
]
