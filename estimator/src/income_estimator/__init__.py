"""Explainable, observation-only income estimator."""

from income_estimator.contracts import (
    CustomerMonthFeatureRowV1,
    CustomerMonthFeatureTableV1,
    EstimatorInputV1,
    EstimatorInputV11,
    IncomeEstimateV1,
    MonthlyIncomeEstimateV1,
)
from income_estimator.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA,
    FEATURE_SCHEMA_FINGERPRINT,
    FEATURE_SET_VERSION,
    build_customer_month_features,
)
from income_estimator.pipeline import (
    RecurringIncomeEstimator,
    RuleBasedIncomeEstimator,
    SupervisedIncomeEstimator,
)

__version__ = "0.4.0"

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_SCHEMA",
    "FEATURE_SCHEMA_FINGERPRINT",
    "FEATURE_SET_VERSION",
    "CustomerMonthFeatureRowV1",
    "CustomerMonthFeatureTableV1",
    "EstimatorInputV1",
    "EstimatorInputV11",
    "IncomeEstimateV1",
    "MonthlyIncomeEstimateV1",
    "RecurringIncomeEstimator",
    "RuleBasedIncomeEstimator",
    "SupervisedIncomeEstimator",
    "build_customer_month_features",
    "__version__",
]
