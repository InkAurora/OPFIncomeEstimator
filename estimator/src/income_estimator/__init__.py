"""Explainable, observation-only income estimator."""

from income_estimator.contracts import (
    EstimatorInputV1,
    EstimatorInputV11,
    IncomeEstimateV1,
    MonthlyIncomeEstimateV1,
)
from income_estimator.pipeline import (
    RecurringIncomeEstimator,
    RuleBasedIncomeEstimator,
    SupervisedIncomeEstimator,
)

__version__ = "0.2.0"

__all__ = [
    "EstimatorInputV1",
    "EstimatorInputV11",
    "IncomeEstimateV1",
    "MonthlyIncomeEstimateV1",
    "RecurringIncomeEstimator",
    "RuleBasedIncomeEstimator",
    "SupervisedIncomeEstimator",
    "__version__",
]
