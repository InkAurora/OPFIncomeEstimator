"""Income stability features over the reconstructed monthly income series."""

from __future__ import annotations

from statistics import fmean, median, pstdev, pvariance, quantiles

from income_estimator.features.monthly import PointInTimeView
from income_estimator.features.outcomes import (
    FeatureOutcome,
    missing,
    present,
    round_minor,
    round_ratio,
    round_variance,
)
from income_estimator.features.schema import (
    MISSING_INSUFFICIENT_HISTORY,
    MISSING_UNDEFINED_DENOMINATOR,
)

AGGREGATE_WINDOWS = (3, 6, 12)
DISPERSION_WINDOWS = (6, 12)


def _series(view: PointInTimeView, window: int) -> list[int]:
    return [item.income_minor for item in view.trailing(window)]


def stability_features(view: PointInTimeView) -> dict[str, FeatureOutcome]:
    """Report central tendency, dispersion, and zero-income months by trailing window."""

    result: dict[str, FeatureOutcome] = {}
    for window in AGGREGATE_WINDOWS:
        series = _series(view, window)
        result[f"income_mean_{window}m_minor"] = present(round_minor(fmean(series)))
        result[f"income_median_{window}m_minor"] = present(round_minor(median(series)))
        if len(series) >= 2:
            result[f"income_std_{window}m_minor"] = present(round_minor(pstdev(series)))
            result[f"income_variance_{window}m"] = present(round_variance(pvariance(series)))
        else:
            result[f"income_std_{window}m_minor"] = missing(MISSING_INSUFFICIENT_HISTORY)
            result[f"income_variance_{window}m"] = missing(MISSING_INSUFFICIENT_HISTORY)

    for window in DISPERSION_WINDOWS:
        series = _series(view, window)
        mean = fmean(series)
        if len(series) < 2:
            result[f"income_cv_{window}m"] = missing(MISSING_INSUFFICIENT_HISTORY)
        elif mean == 0:
            result[f"income_cv_{window}m"] = missing(MISSING_UNDEFINED_DENOMINATOR)
        else:
            result[f"income_cv_{window}m"] = present(round_ratio(pstdev(series) / mean))
        result[f"zero_income_months_{window}m"] = present(
            sum(1 for value in series if value == 0)
        )

    year = _series(view, 12)
    if len(year) >= 2:
        first, second, third = quantiles(year, n=4, method="inclusive")
    else:
        first = second = third = float(year[0])
    result["income_p25_12m_minor"] = present(round_minor(first))
    result["income_p50_12m_minor"] = present(round_minor(second))
    result["income_p75_12m_minor"] = present(round_minor(third))
    result["income_min_12m_minor"] = present(min(year))
    result["income_max_12m_minor"] = present(max(year))
    return result


__all__ = ["stability_features"]
