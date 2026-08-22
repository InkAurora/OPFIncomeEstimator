"""Explainable, observation-only income estimator."""

from income_estimator.contracts import (
    EstimatorInputV1,
    IncomeEstimateV1,
    MonthlyIncomeEstimateV1,
)
from income_estimator.pipeline import RecurringIncomeEstimator, RuleBasedIncomeEstimator

__version__ = "0.2.0"

__all__ = [
    "EstimatorInputV1",
    "IncomeEstimateV1",
    "MonthlyIncomeEstimateV1",
    "RecurringIncomeEstimator",
    "RuleBasedIncomeEstimator",
    "__version__",
]
