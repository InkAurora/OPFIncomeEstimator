"""Point-in-time customer-month features (estimator 0.4)."""

from income_estimator.features.customer_month import (
    ExplainingEstimator,
    build_customer_month_features,
    build_point_in_time_view,
)
from income_estimator.features.monthly import MonthlyObservation, PointInTimeView
from income_estimator.features.point_in_time import (
    cutoff_date,
    reference_months,
    slice_request,
)
from income_estimator.features.schema import (
    FEATURE_NAMES,
    FEATURE_SCHEMA,
    FEATURE_SCHEMA_FINGERPRINT,
    FEATURE_SET_VERSION,
    FEATURE_SPEC_BY_NAME,
    FeatureSpec,
    feature_schema_fingerprint,
)

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_SCHEMA",
    "FEATURE_SCHEMA_FINGERPRINT",
    "FEATURE_SET_VERSION",
    "FEATURE_SPEC_BY_NAME",
    "ExplainingEstimator",
    "FeatureSpec",
    "MonthlyObservation",
    "PointInTimeView",
    "build_customer_month_features",
    "build_point_in_time_view",
    "cutoff_date",
    "feature_schema_fingerprint",
    "reference_months",
    "slice_request",
]
