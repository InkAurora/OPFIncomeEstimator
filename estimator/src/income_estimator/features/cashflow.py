"""Rolling cash-flow features over 1, 3, 6, and 12 trailing months."""

from __future__ import annotations

from statistics import fmean, median

from income_estimator.features.monthly import PointInTimeView
from income_estimator.features.outcomes import FeatureOutcome, present, round_minor

WINDOWS = (1, 3, 6, 12)
AGGREGATE_WINDOWS = (3, 6, 12)


def cash_flow_features(view: PointInTimeView) -> dict[str, FeatureOutcome]:
    """Aggregate gross flow, probability-weighted income, and reconstructed income."""

    result: dict[str, FeatureOutcome] = {}
    for window in WINDOWS:
        trailing = view.trailing(window)
        result[f"credits_{window}m_minor"] = present(
            sum(item.gross_credits_minor for item in trailing)
        )
        result[f"debits_{window}m_minor"] = present(sum(item.debits_minor for item in trailing))
        result[f"probable_income_{window}m_minor"] = present(
            sum(item.probable_income_minor for item in trailing)
        )
        result[f"income_{window}m_minor"] = present(sum(item.income_minor for item in trailing))

    for window in AGGREGATE_WINDOWS:
        probable = [item.probable_income_minor for item in view.trailing(window)]
        result[f"probable_income_mean_{window}m_minor"] = present(round_minor(fmean(probable)))
        result[f"probable_income_median_{window}m_minor"] = present(round_minor(median(probable)))

    trailing_year = view.trailing(12)
    result["imputed_income_12m_minor"] = present(
        sum(item.imputed_income_minor for item in trailing_year)
    )
    result["excluded_own_transfer_12m_minor"] = present(
        sum(item.excluded_own_transfer_minor for item in trailing_year)
    )
    result["excluded_loan_disbursement_12m_minor"] = present(
        sum(item.excluded_loan_disbursement_minor for item in trailing_year)
    )
    result["excluded_investment_redemption_12m_minor"] = present(
        sum(item.excluded_investment_redemption_minor for item in trailing_year)
    )
    result["excluded_refund_12m_minor"] = present(
        sum(item.excluded_refund_minor for item in trailing_year)
    )
    return result


__all__ = ["cash_flow_features"]
