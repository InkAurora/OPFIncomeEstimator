"""Versioned estimator integration boundary and reference implementation."""

from finances_simulator.integration.adapter import build_estimator_input
from finances_simulator.integration.baseline import BaselineIncomeEstimator
from finances_simulator.integration.contracts import (
    ESTIMATOR_CONTRACT_VERSION,
    EstimatorInputV1,
    IncomeEstimateV1,
    IncomeEstimator,
    MonthlyIncomeEstimateV1,
)
from finances_simulator.integration.evaluation import (
    EvaluationReportV1,
    PopulationEvaluation,
    evaluate_population,
)

__all__ = [
    "ESTIMATOR_CONTRACT_VERSION",
    "BaselineIncomeEstimator",
    "EstimatorInputV1",
    "EvaluationReportV1",
    "IncomeEstimateV1",
    "IncomeEstimator",
    "MonthlyIncomeEstimateV1",
    "PopulationEvaluation",
    "build_estimator_input",
    "evaluate_population",
]
