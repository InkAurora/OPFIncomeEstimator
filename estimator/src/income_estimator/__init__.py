"""Explainable, observation-only income estimator."""

from income_estimator.contracts import (
    CustomerMonthFeatureRowV1,
    CustomerMonthFeatureTableV1,
    EstimatorInputV1,
    EstimatorInputV11,
    IncomeEstimateV1,
    IncomeEstimateV11,
    MonthlyIncomeEstimateV1,
)
from income_estimator.explainability import build_explanation
from income_estimator.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA,
    FEATURE_SCHEMA_FINGERPRINT,
    FEATURE_SET_VERSION,
    build_customer_month_features,
)
from income_estimator.models import (
    CapacityEstimatorArtifact,
    GradientBoostedCapacityModel,
)
from income_estimator.models.quantiles import (
    ConformalCalibrationArtifact,
    ConformalIntervalModel,
)
from income_estimator.pipeline import (
    EnsembleIncomeEstimator,
    RecurringIncomeEstimator,
    RuleBasedIncomeEstimator,
    SupervisedIncomeEstimator,
)

__version__ = "0.8.0"

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_SCHEMA",
    "FEATURE_SCHEMA_FINGERPRINT",
    "FEATURE_SET_VERSION",
    "CapacityEstimatorArtifact",
    "ConformalCalibrationArtifact",
    "ConformalIntervalModel",
    "CustomerMonthFeatureRowV1",
    "CustomerMonthFeatureTableV1",
    "EstimatorInputV1",
    "EnsembleIncomeEstimator",
    "EstimatorInputV11",
    "GradientBoostedCapacityModel",
    "IncomeEstimateV1",
    "IncomeEstimateV11",
    "MonthlyIncomeEstimateV1",
    "RecurringIncomeEstimator",
    "RuleBasedIncomeEstimator",
    "SupervisedIncomeEstimator",
    "build_customer_month_features",
    "build_explanation",
    "__version__",
]
