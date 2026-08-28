"""Everything the demo does that is not rendering.

The module is deliberately free of Streamlit imports. It owns the profile-to-scenario mapping, the
promoted bundle binding, one inference call, and the join of that inference against the
simulator's private truth.

The order of the four stages in :func:`run_demo` is the point of the file, not an implementation
detail. The simulator builds a complete hidden financial world; only its observable projection is
adapted into an estimator request; the estimator answers from that request alone; and the private
truth is projected and joined afterwards, purely so the demo can show how far off the answer was.
:func:`run_inference` takes the request and nothing else, so no later edit can quietly hand the
estimator a label.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from finances_simulator.config import load_scenario_config
from finances_simulator.generation import GeneratedScenario, generate_scenario
from finances_simulator.ground_truth.income_targets import project_income_targets
from finances_simulator.integration.adapter import build_estimator_input_v1_2
from income_estimator.contracts.explanation_v1 import EstimationExplanationV1
from income_estimator.contracts.output_v1_1 import IncomeEstimateV11
from income_estimator.production import BundleError, ProductionIncomeEstimator

from demo_app.profiles import Profile, get_profile, supported_months

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DIRECTORY = REPOSITORY_ROOT / "estimator" / "bundles"
PROMOTED_BUNDLE_PATH = BUNDLE_DIRECTORY / "production-0.11.0"
EXPECTED_BUNDLE_ID = "production-0.11.0"
EXPECTED_MODEL_VERSIONS: tuple[str, ...] = (
    "capacity-gbdt-stumps-0.6.0",
    "conditional-selector-intervals-0.11.0",
)

# Field names that exist only inside the simulator's private layers. The demo asserts none of them
# reach the estimator; see demo_app/tests/test_service.py.
PRIVATE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "realized_income_month_minor",
        "expected_income_month_minor",
        "sustainable_monthly_income_minor",
        "realized_income_trailing_12m_minor",
        "expected_income_next_12m_minor",
        "active_source_count",
        "recurring_source_count",
        "bonus_income_month_minor",
        "income_source_id",
        "life_event_id",
        "anomaly_id",
        "net_worth_minor",
        "annualized_base_income_before_minor",
        "annualized_base_income_after_minor",
        "is_true_income",
    }
)


class DemoConfigurationError(RuntimeError):
    """The demo cannot be run as configured, for a reason worth showing a person."""


@dataclass(frozen=True, slots=True)
class MonthRow:
    """One reference month: what the estimator said, and what was actually true."""

    month: str
    realized_estimate_minor: int
    sustainable_p10_minor: int | None
    sustainable_p50_minor: int | None
    sustainable_p90_minor: int | None
    quantile_unavailable_reason: str | None
    confidence_score_basis_points: int | None
    routing_reason_codes: tuple[str, ...]
    component_disagreement_basis_points: int | None
    included_transaction_count: int
    excluded_transaction_count: int
    # Evaluation only. Never provided to the estimator.
    truth_realized_minor: int | None
    truth_sustainable_minor: int | None
    truth_active_source_count: int | None

    @property
    def realized_error_minor(self) -> int | None:
        if self.truth_realized_minor is None:
            return None
        return self.realized_estimate_minor - self.truth_realized_minor

    @property
    def realized_error_percent(self) -> float | None:
        if not self.truth_realized_minor:
            return None
        return (self.realized_error_minor or 0) / self.truth_realized_minor * 100.0

    @property
    def sustainable_error_minor(self) -> int | None:
        if self.truth_sustainable_minor is None or self.sustainable_p50_minor is None:
            return None
        return self.sustainable_p50_minor - self.truth_sustainable_minor

    @property
    def sustainable_error_percent(self) -> float | None:
        if not self.truth_sustainable_minor or self.sustainable_error_minor is None:
            return None
        return self.sustainable_error_minor / self.truth_sustainable_minor * 100.0

    @property
    def has_interval(self) -> bool:
        return self.sustainable_p10_minor is not None and self.sustainable_p90_minor is not None

    @property
    def interval_contains_truth(self) -> bool | None:
        """Whether the band held the truth, or None when no band was published."""

        if not self.has_interval or self.truth_sustainable_minor is None:
            return None
        return (
            self.sustainable_p10_minor
            <= self.truth_sustainable_minor
            <= self.sustainable_p90_minor
        )


@dataclass(frozen=True, slots=True)
class BalanceRow:
    """Month-end position assembled from observed balances only.

    Nothing here is read from the private balance sheet. Under partial consent the numbers are
    therefore incomplete on purpose: they are what the client's consent actually exposed.
    """

    month: str
    account_balance_minor: int
    investment_balance_minor: int
    debt_minor: int

    @property
    def net_position_minor(self) -> int:
        return self.account_balance_minor + self.investment_balance_minor - self.debt_minor


@dataclass(frozen=True, slots=True)
class CoverageRow:
    """How much of one account's eligible history the consent actually exposed."""

    account_id: str
    configured_coverage_percent: int
    eligible_record_count: int
    observed_record_count: int
    effective_coverage_basis_points: int


@dataclass(frozen=True, slots=True)
class DataQuality:
    """Feed health, as the estimator saw it."""

    coverage_rows: tuple[CoverageRow, ...]
    coverage_is_declared: bool
    observed_transaction_count: int
    duplicate_count: int
    reversal_count: int
    repost_count: int
    late_arrival_count: int
    max_late_arrival_days: int
    months_with_interval: int
    months_abstained: int
    abstention_reasons: tuple[str, ...]

    @property
    def overall_coverage_basis_points(self) -> int | None:
        if not self.coverage_rows:
            return None
        eligible = sum(row.eligible_record_count for row in self.coverage_rows)
        if not eligible:
            return min(row.effective_coverage_basis_points for row in self.coverage_rows)
        observed = sum(row.observed_record_count for row in self.coverage_rows)
        return round(observed / eligible * 10_000)


@dataclass(frozen=True, slots=True)
class LifeEventRow:
    """A private life event, shown only as a marker on the evaluation timeline."""

    event_type: str
    effective_date: str
    annualized_income_before_minor: int
    annualized_income_after_minor: int


@dataclass(frozen=True, slots=True)
class Inference:
    """Everything the estimator produced, before any truth was in scope."""

    estimate: IncomeEstimateV11
    explanation: EstimationExplanationV1
    elapsed_seconds: float
    production_result_contract_version: str
    bundle_contract_version: str
    bundle_id: str
    bundle_version: str
    bundle_digest: str
    estimator_package_version: str


@dataclass(frozen=True, slots=True)
class DemoResult:
    """One complete run, ready to render."""

    profile: Profile
    seed: int
    months: int
    run_id: str
    customer_id: str
    currency: str
    production_result_contract_version: str
    bundle_contract_version: str
    bundle_id: str
    bundle_version: str
    bundle_digest: str
    estimator_package_version: str
    estimator_version: str
    feature_version: str
    input_contract_version: str
    output_contract_version: str
    model_versions: tuple[str, ...]
    component_versions: tuple[str, ...]
    scenario_schema_version: str
    month_rows: tuple[MonthRow, ...]
    balance_rows: tuple[BalanceRow, ...]
    life_events: tuple[LifeEventRow, ...]
    data_quality: DataQuality
    request_record_counts: dict[str, int]
    estimate: IncomeEstimateV11
    explanation: EstimationExplanationV1
    generation_seconds: float
    inference_seconds: float
    total_seconds: float = field(default=0.0)

    @property
    def latest(self) -> MonthRow:
        return self.month_rows[-1]

    @property
    def realized_mean_absolute_percent_error(self) -> float | None:
        errors = [
            abs(row.realized_error_percent)
            for row in self.month_rows
            if row.realized_error_percent is not None
        ]
        return sum(errors) / len(errors) if errors else None

    @property
    def sustainable_mean_absolute_percent_error(self) -> float | None:
        errors = [
            abs(row.sustainable_error_percent)
            for row in self.month_rows
            if row.sustainable_error_percent is not None
        ]
        return sum(errors) / len(errors) if errors else None

    @property
    def interval_coverage(self) -> tuple[int, int]:
        """Months whose published band held the truth, over months that published a band."""

        published = [row for row in self.month_rows if row.interval_contains_truth is not None]
        return sum(1 for row in published if row.interval_contains_truth), len(published)


# ---------------------------------------------------------------------------------------------
# Stage 1: the simulator builds a complete hidden world.
# ---------------------------------------------------------------------------------------------


def generate_world(profile: Profile, *, seed: int, months: int) -> GeneratedScenario:
    """Run the simulator for one client.

    Raises:
        DemoConfigurationError: If the requested history is longer than the scenario's own
            configured horizon, which would end every income source mid-window.
    """

    allowed = supported_months(profile.key)
    if months not in allowed:
        raise DemoConfigurationError(
            f"profile '{profile.label}' supports {allowed} months of history, not {months}"
        )
    config = load_scenario_config(profile.scenario_path)
    return generate_scenario(config, seed=seed, months=months)


# ---------------------------------------------------------------------------------------------
# Stage 2: only the observable projection crosses into the estimator's world.
# ---------------------------------------------------------------------------------------------


def build_request(world: GeneratedScenario) -> Any:
    """Adapt the observed layer into estimator input 1.2. No private layer is read."""

    return build_estimator_input_v1_2(world)


@lru_cache(maxsize=1)
def load_estimator() -> ProductionIncomeEstimator:
    """Load and verify the promoted bundle once per process.

    Raises:
        DemoConfigurationError: If the bundle is missing, altered, internally inconsistent, or
            incompatible with the installed estimator package.
    """

    try:
        estimator = ProductionIncomeEstimator.from_bundle(PROMOTED_BUNDLE_PATH)
    except (BundleError, OSError) as error:
        raise DemoConfigurationError(
            f"could not load promoted bundle {EXPECTED_BUNDLE_ID!r}: {error}"
        ) from error

    if estimator.manifest is None or estimator.manifest.bundle_id != EXPECTED_BUNDLE_ID:
        loaded = estimator.manifest.bundle_id if estimator.manifest is not None else None
        raise DemoConfigurationError(
            f"expected promoted bundle {EXPECTED_BUNDLE_ID!r}, loaded {loaded!r}"
        )
    if estimator.model_versions != EXPECTED_MODEL_VERSIONS:
        raise DemoConfigurationError(
            f"expected the promoted pair {EXPECTED_MODEL_VERSIONS}, "
            f"loaded {estimator.model_versions}"
        )
    return estimator


# ---------------------------------------------------------------------------------------------
# Stage 3: inference. The request is the only argument, by design.
# ---------------------------------------------------------------------------------------------


def run_inference(estimator: ProductionIncomeEstimator, request: Any) -> Inference:
    """Estimate and explain from the request alone."""

    started = time.perf_counter()
    estimate_result = estimator.estimate_production(request)
    explanation_result = estimator.explain_production(request)
    identity_fields = (
        "schema_version",
        "bundle_contract_version",
        "bundle_id",
        "bundle_version",
        "bundle_digest",
        "estimator_package_version",
        "estimator_version",
        "feature_set_version",
        "model_versions",
    )
    estimate_identity = tuple(getattr(estimate_result, name) for name in identity_fields)
    explanation_identity = tuple(getattr(explanation_result, name) for name in identity_fields)
    if estimate_identity != explanation_identity:
        raise DemoConfigurationError(
            "estimate and explanation came from different bundle identities"
        )
    if estimate_result.estimate is None or explanation_result.explanation is None:
        raise DemoConfigurationError("production estimator returned an incomplete result envelope")
    return Inference(
        estimate=estimate_result.estimate,
        explanation=explanation_result.explanation,
        elapsed_seconds=time.perf_counter() - started,
        production_result_contract_version=estimate_result.schema_version,
        bundle_contract_version=estimate_result.bundle_contract_version,
        bundle_id=estimate_result.bundle_id,
        bundle_version=estimate_result.bundle_version,
        bundle_digest=estimate_result.bundle_digest,
        estimator_package_version=estimate_result.estimator_package_version,
    )


# ---------------------------------------------------------------------------------------------
# Stage 4: the private truth is projected and joined, after the estimate exists.
# ---------------------------------------------------------------------------------------------


def project_private_truth(world: GeneratedScenario) -> dict[str, Any]:
    """Project the private income targets, keyed by month. Evaluation only."""

    return {target.month: target for target in project_income_targets(world.simulation)}


def _collect_balance_rows(world: GeneratedScenario) -> tuple[BalanceRow, ...]:
    observations = world.observations
    months: dict[str, dict[str, int]] = {}

    def bucket(reference_date: str) -> dict[str, int]:
        return months.setdefault(
            reference_date[:7],
            {"account": 0, "investment": 0, "debt": 0},
        )

    for balance in getattr(observations, "balances", ()):
        if getattr(balance, "balance_type", "CLOSING") == "CLOSING":
            bucket(balance.reference_date)["account"] += balance.balance_minor
    for balance in getattr(observations, "investment_balances", ()):
        if getattr(balance, "balance_type", "CLOSING") == "CLOSING":
            bucket(balance.reference_date)["investment"] += balance.balance_minor
    for balance in getattr(observations, "loan_balances", ()):
        bucket(balance.reference_date)["debt"] += getattr(
            balance, "outstanding_principal_minor", 0
        )
    for invoice in getattr(observations, "credit_card_invoices", ()):
        outstanding = invoice.amount_due_minor - (invoice.paid_amount_minor or 0)
        if outstanding > 0:
            bucket(invoice.statement_close_date)["debt"] += outstanding

    return tuple(
        BalanceRow(
            month=month,
            account_balance_minor=values["account"],
            investment_balance_minor=values["investment"],
            debt_minor=values["debt"],
        )
        for month, values in sorted(months.items())
    )


def _collect_life_events(world: GeneratedScenario) -> tuple[LifeEventRow, ...]:
    return tuple(
        LifeEventRow(
            event_type=str(getattr(event.event_type, "value", event.event_type)),
            effective_date=event.effective_date,
            annualized_income_before_minor=event.annualized_base_income_before_minor,
            annualized_income_after_minor=event.annualized_base_income_after_minor,
        )
        for event in sorted(
            getattr(world.ground_truth, "life_events", ()),
            key=lambda item: item.effective_date,
        )
    )


def _day_gap(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _collect_data_quality(
    world: GeneratedScenario,
    request: Any,
    estimate: IncomeEstimateV11,
) -> DataQuality:
    declared = bool(getattr(world.observations, "observation_coverage", ()))
    coverage_rows = tuple(
        CoverageRow(
            account_id=item.account_id,
            configured_coverage_percent=item.configured_coverage_percent,
            eligible_record_count=item.eligible_record_count,
            observed_record_count=item.observed_original_record_count,
            effective_coverage_basis_points=item.effective_coverage_basis_points,
        )
        for item in sorted(request.coverage, key=lambda item: item.account_id)
    )

    duplicates = reversals = reposts = late = 0
    max_late = 0
    for transaction in world.observations.transactions:
        if getattr(transaction, "duplicate_of_transaction_id", None):
            duplicates += 1
        if getattr(transaction, "reversal_of_transaction_id", None):
            reversals += 1
        if getattr(transaction, "repost_of_transaction_id", None):
            reposts += 1
        observed_at = getattr(transaction, "observed_at", None)
        if observed_at and observed_at > transaction.posted_at:
            late += 1
            max_late = max(max_late, _day_gap(transaction.posted_at, observed_at))

    reasons = sorted(
        {
            month.quantile_unavailable_reason
            for month in estimate.monthly_estimates
            if month.quantile_unavailable_reason
        }
    )
    published = sum(
        1
        for month in estimate.monthly_estimates
        if month.sustainable_income_p10_minor is not None
    )
    return DataQuality(
        coverage_rows=coverage_rows,
        coverage_is_declared=declared,
        observed_transaction_count=len(world.observations.transactions),
        duplicate_count=duplicates,
        reversal_count=reversals,
        repost_count=reposts,
        late_arrival_count=late,
        max_late_arrival_days=max_late,
        months_with_interval=published,
        months_abstained=len(estimate.monthly_estimates) - published,
        abstention_reasons=tuple(reasons),
    )


def _request_record_counts(request: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, value in request.model_dump().items():
        if isinstance(value, (list, tuple)):
            counts[name] = len(value)
    return counts


def join_truth(
    profile: Profile,
    *,
    seed: int,
    months: int,
    world: GeneratedScenario,
    request: Any,
    inference: Inference,
    generation_seconds: float,
) -> DemoResult:
    """Assemble the render-ready result, joining private truth onto a finished estimate."""

    truth = project_private_truth(world)
    included_by_month = {
        item.month: len(item.included_transactions)
        for item in inference.explanation.monthly_explanations
    }
    excluded_by_month = {
        item.month: len(item.excluded_transactions)
        for item in inference.explanation.monthly_explanations
    }

    month_rows = tuple(
        MonthRow(
            month=month.month,
            realized_estimate_minor=month.realized_income_estimate_minor,
            sustainable_p10_minor=month.sustainable_income_p10_minor,
            sustainable_p50_minor=month.sustainable_income_p50_minor,
            sustainable_p90_minor=month.sustainable_income_p90_minor,
            quantile_unavailable_reason=month.quantile_unavailable_reason,
            confidence_score_basis_points=month.confidence_score_basis_points,
            routing_reason_codes=tuple(month.routing_reason_codes),
            component_disagreement_basis_points=month.component_disagreement_basis_points,
            included_transaction_count=included_by_month.get(month.month, 0),
            excluded_transaction_count=excluded_by_month.get(month.month, 0),
            truth_realized_minor=(
                truth[month.month].realized_income_month_minor if month.month in truth else None
            ),
            truth_sustainable_minor=(
                truth[month.month].sustainable_monthly_income_minor
                if month.month in truth
                else None
            ),
            truth_active_source_count=(
                truth[month.month].active_source_count if month.month in truth else None
            ),
        )
        for month in inference.estimate.monthly_estimates
    )

    return DemoResult(
        profile=profile,
        seed=seed,
        months=months,
        run_id=inference.estimate.run_id,
        customer_id=inference.estimate.customer_id,
        currency=inference.estimate.currency,
        production_result_contract_version=inference.production_result_contract_version,
        bundle_contract_version=inference.bundle_contract_version,
        bundle_id=inference.bundle_id,
        bundle_version=inference.bundle_version,
        bundle_digest=inference.bundle_digest,
        estimator_package_version=inference.estimator_package_version,
        estimator_version=inference.estimate.estimator_version,
        feature_version=inference.estimate.feature_version,
        input_contract_version=inference.estimate.input_contract_version,
        output_contract_version=inference.estimate.schema_version,
        model_versions=tuple(inference.estimate.model_versions),
        component_versions=tuple(inference.estimate.component_versions),
        scenario_schema_version=world.simulation.profile.contract_schema_version,
        month_rows=month_rows,
        balance_rows=_collect_balance_rows(world),
        life_events=_collect_life_events(world),
        data_quality=_collect_data_quality(world, request, inference.estimate),
        request_record_counts=_request_record_counts(request),
        estimate=inference.estimate,
        explanation=inference.explanation,
        generation_seconds=generation_seconds,
        inference_seconds=inference.elapsed_seconds,
    )


def run_demo(profile_key: str, *, seed: int, months: int) -> DemoResult:
    """Run the whole flow, in the one order that keeps the boundary honest."""

    profile = get_profile(profile_key)
    estimator = load_estimator()

    started = time.perf_counter()
    world = generate_world(profile, seed=seed, months=months)
    generation_seconds = time.perf_counter() - started

    request = build_request(world)
    inference = run_inference(estimator, request)

    result = join_truth(
        profile,
        seed=seed,
        months=months,
        world=world,
        request=request,
        inference=inference,
        generation_seconds=generation_seconds,
    )
    object.__setattr__(result, "total_seconds", time.perf_counter() - started)
    return result


__all__ = [
    "BUNDLE_DIRECTORY",
    "EXPECTED_BUNDLE_ID",
    "EXPECTED_MODEL_VERSIONS",
    "PROMOTED_BUNDLE_PATH",
    "PRIVATE_FIELD_NAMES",
    "BalanceRow",
    "CoverageRow",
    "DataQuality",
    "DemoConfigurationError",
    "DemoResult",
    "Inference",
    "LifeEventRow",
    "MonthRow",
    "build_request",
    "generate_world",
    "join_truth",
    "load_estimator",
    "project_private_truth",
    "run_demo",
    "run_inference",
]
