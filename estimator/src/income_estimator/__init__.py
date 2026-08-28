"""Explainable, observation-only income estimator."""

from income_estimator.contracts import (
    BUNDLE_CONTRACT_VERSION,
    PRODUCTION_RESULT_CONTRACT_VERSION,
    BundleManifestV1,
    CustomerMonthFeatureRowV1,
    CustomerMonthFeatureTableV1,
    EstimatorInputV1,
    EstimatorInputV11,
    IncomeEstimateV1,
    IncomeEstimateV11,
    MonthlyIncomeEstimateV1,
    ProductionResultV1,
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
from income_estimator.production import (
    BundleCompatibilityError,
    BundleError,
    BundleIntegrityError,
    BundleManifestError,
    ProductionIncomeEstimator,
    load_manifest,
    verify_bundle,
)

__version__ = "0.11.0"

__all__ = [
    "verify_bundle",
    "load_manifest",
    "ProductionResultV1",
    "ProductionIncomeEstimator",
    "BundleManifestV1",
    "BundleManifestError",
    "BundleIntegrityError",
    "BundleError",
    "BundleCompatibilityError",
    "PRODUCTION_RESULT_CONTRACT_VERSION",
    "BUNDLE_CONTRACT_VERSION",
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
