"""Explainable, observation-only income estimator."""

from income_estimator.contracts import (
    EstimatorInputV1,
    IncomeEstimateV1,
    MonthlyIncomeEstimateV1,
)
from income_estimator.pipeline import RuleBasedIncomeEstimator

__version__ = "0.1.0"

__all__ = [
    "EstimatorInputV1",
    "IncomeEstimateV1",
    "MonthlyIncomeEstimateV1",
    "RuleBasedIncomeEstimator",
    "__version__",
]
