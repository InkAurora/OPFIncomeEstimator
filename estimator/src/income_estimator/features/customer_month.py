"""Build point-in-time customer-month feature rows.

Each reference month replays the promoted deterministic estimator on a request narrowed to the
records observable at that month's cutoff. Feature groups then read only that replayed view, so no
formula can reach a later arrival even when the caller passes a full-window request.
"""

from __future__ import annotations

from typing import Any, Protocol

from income_estimator.contracts.audit import EstimationAudit
from income_estimator.contracts.features_v1 import (
    CustomerMonthFeatureRowV1,
    CustomerMonthFeatureTableV1,
    CustomerMonthFeatureValueV1,
)
from income_estimator.contracts.v1 import EstimatorInputV1, validate_estimator_input
from income_estimator.features.cashflow import cash_flow_features
from income_estimator.features.context import context_features
from income_estimator.features.coverage import activity_features, coverage_features
from income_estimator.features.monthly import (
    UNAVAILABLE_REASONS,
    PointInTimeView,
    build_monthly_observations,
    observed_domains,
)
from income_estimator.features.outcomes import FeatureOutcome
from income_estimator.features.point_in_time import (
    cutoff_date,
    month_index,
    month_of,
    reference_months,
    slice_request,
)
from income_estimator.features.schema import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_FINGERPRINT,
    FEATURE_SET_VERSION,
)
from income_estimator.features.sources import source_features
from income_estimator.features.stability import stability_features

FEATURE_GROUP_BUILDERS = (
    cash_flow_features,
    stability_features,
    source_features,
    coverage_features,
    activity_features,
    context_features,
)


class ExplainingEstimator(Protocol):
    """Any estimator exposing the internal audit view, including the optional 0.3 candidate."""

    estimator_version: str
    feature_version: str
    model_versions: tuple[str, ...]

    def explain(self, request: Any) -> EstimationAudit: ...


def build_point_in_time_view(
    request: EstimatorInputV1,
    reference_month: str,
    estimator: ExplainingEstimator,
) -> PointInTimeView:
    """Narrow the request to one reference month and replay the estimator on it."""

    cutoff = cutoff_date(request, reference_month)
    sliced = slice_request(request, cutoff)
    audit = estimator.explain(sliced)
    months = tuple(item.month for item in audit.estimate.monthly_estimates)
    observations = build_monthly_observations(sliced, audit, months)
    decision_by_id = {item.transaction_id: item for item in audit.transaction_decisions}
    available_transaction_ids = frozenset(
        transaction_id
        for transaction_id, decision in decision_by_id.items()
        if not UNAVAILABLE_REASONS.intersection(decision.reason_codes)
    )
    return PointInTimeView(
        reference_month=reference_month,
        as_of_date=cutoff,
        months=months,
        request=sliced,
        audit=audit,
        observations=observations,
        decision_by_id=decision_by_id,
        available_transaction_ids=available_transaction_ids,
        domains=observed_domains(sliced, available_transaction_ids),
    )


def _row_values(view: PointInTimeView) -> tuple[CustomerMonthFeatureValueV1, ...]:
    computed: dict[str, FeatureOutcome] = {}
    for builder in FEATURE_GROUP_BUILDERS:
        produced = builder(view)
        overlap = computed.keys() & produced.keys()
        if overlap:
            raise RuntimeError(f"duplicate feature names produced: {sorted(overlap)}")
        computed.update(produced)

    unexpected = computed.keys() - set(FEATURE_NAMES)
    undelivered = set(FEATURE_NAMES) - computed.keys()
    if unexpected or undelivered:
        raise RuntimeError(
            "feature builders disagree with the versioned schema: "
            f"unexpected={sorted(unexpected)} missing={sorted(undelivered)}"
        )
    return tuple(
        CustomerMonthFeatureValueV1(
            name=name,
            value=computed[name].value,
            missing_reason=computed[name].missing_reason,
        )
        for name in FEATURE_NAMES
    )


def build_customer_month_features(
    request: Any,
    estimator: ExplainingEstimator | None = None,
) -> CustomerMonthFeatureTableV1:
    """Return one point-in-time feature row per requested calendar month.

    The default estimator is promoted `0.2`. The rejected `0.3` supervised candidate can be passed
    explicitly; it changes the income probabilities and therefore the recorded model versions.
    """

    if estimator is None:
        from income_estimator.pipeline import RecurringIncomeEstimator

        estimator = RecurringIncomeEstimator()

    validated = validate_estimator_input(request)
    window_start_month = month_of(validated.window_start)
    window_end_month = month_of(validated.window_end)
    rows: list[CustomerMonthFeatureRowV1] = []
    for reference_month in reference_months(validated):
        if month_index(reference_month) < month_index(window_start_month):
            continue
        if month_index(reference_month) > month_index(window_end_month):
            break
        view = build_point_in_time_view(validated, reference_month, estimator)
        rows.append(
            CustomerMonthFeatureRowV1(
                customer_id=validated.customer_id,
                reference_month=reference_month,
                as_of_date=view.as_of_date.isoformat(),
                currency=validated.currency,
                values=_row_values(view),
            )
        )

    return CustomerMonthFeatureTableV1(
        feature_set_version=FEATURE_SET_VERSION,
        feature_schema_fingerprint=FEATURE_SCHEMA_FINGERPRINT,
        transaction_feature_version=estimator.feature_version,
        estimator_version=estimator.estimator_version,
        input_contract_version=validated.schema_version,
        model_versions=tuple(estimator.model_versions),
        run_id=validated.run_id,
        customer_id=validated.customer_id,
        currency=validated.currency,
        rows=tuple(rows),
    )


__all__ = [
    "FEATURE_GROUP_BUILDERS",
    "ExplainingEstimator",
    "build_customer_month_features",
    "build_point_in_time_view",
]
