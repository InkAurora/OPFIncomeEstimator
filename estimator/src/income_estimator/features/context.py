"""Balance, loan, and investment context observed through account activity.

These features describe what the deposit-account view can see: the latest balances, loan
disbursements linked to visible credits, and investment flows linked to visible transactions. The
product-side capacity view added by estimator input `1.2` lives in ``capacity.py``.
"""

from __future__ import annotations

from datetime import date

from income_estimator.features.monthly import PointInTimeView
from income_estimator.features.outcomes import FeatureOutcome, missing, present
from income_estimator.features.schema import MISSING_NO_OBSERVED_RECORDS


def _balance_features(view: PointInTimeView) -> dict[str, FeatureOutcome]:
    balances = getattr(view.request, "balances", ())
    if "BALANCES" not in view.domains:
        return {
            "available_balance_minor": missing(MISSING_NO_OBSERVED_RECORDS),
            "balance_accounts_observed": missing(MISSING_NO_OBSERVED_RECORDS),
            "balance_staleness_days": missing(MISSING_NO_OBSERVED_RECORDS),
        }

    latest: dict[str, tuple[str, str, int]] = {}
    for item in balances:
        key = (item.reference_date, item.balance_id)
        current = latest.get(item.account_id)
        if current is None or key > (current[0], current[1]):
            latest[item.account_id] = (item.reference_date, item.balance_id, item.balance_minor)
    newest = max(entry[0] for entry in latest.values())
    return {
        "available_balance_minor": present(sum(entry[2] for entry in latest.values())),
        "balance_accounts_observed": present(len(latest)),
        "balance_staleness_days": present((view.as_of_date - date.fromisoformat(newest)).days),
    }


def _loan_features(view: PointInTimeView) -> dict[str, FeatureOutcome]:
    if "LOANS" not in view.domains:
        return {
            "observed_loan_count": missing(MISSING_NO_OBSERVED_RECORDS),
            "loan_disbursement_12m_minor": missing(MISSING_NO_OBSERVED_RECORDS),
            "months_since_last_loan_disbursement": missing(MISSING_NO_OBSERVED_RECORDS),
        }

    observed = 0
    disbursed = 0
    last_month: str | None = None
    for loan in view.request.loans:
        transaction_id = loan.disbursement_transaction_id
        if transaction_id not in view.available_transaction_ids:
            continue
        decision = view.decision_by_id[transaction_id]
        observed += 1
        if view.in_trailing_window(decision.posted_month, 12):
            disbursed += decision.amount_minor
        if last_month is None or decision.posted_month > last_month:
            last_month = decision.posted_month
    return {
        "observed_loan_count": present(observed),
        "loan_disbursement_12m_minor": present(disbursed),
        "months_since_last_loan_disbursement": (
            present(view.months_since(last_month))
            if last_month is not None
            else missing(MISSING_NO_OBSERVED_RECORDS)
        ),
    }


def _investment_features(view: PointInTimeView) -> dict[str, FeatureOutcome]:
    if "INVESTMENTS" not in view.domains:
        return {
            "investment_contribution_12m_minor": missing(MISSING_NO_OBSERVED_RECORDS),
            "investment_redemption_12m_minor": missing(MISSING_NO_OBSERVED_RECORDS),
            "net_investment_contributions_12m_minor": missing(MISSING_NO_OBSERVED_RECORDS),
            "investment_transaction_count_12m": missing(MISSING_NO_OBSERVED_RECORDS),
        }

    contributions = 0
    redemptions = 0
    count = 0
    for item in view.request.investment_transactions:
        transaction_id = item.related_account_transaction_id
        if transaction_id is None or transaction_id not in view.available_transaction_ids:
            continue
        decision = view.decision_by_id[transaction_id]
        if not view.in_trailing_window(decision.posted_month, 12):
            continue
        count += 1
        if item.transaction_type == "CONTRIBUTION":
            contributions += decision.amount_minor
        elif item.transaction_type == "REDEMPTION":
            redemptions += decision.amount_minor
    return {
        "investment_contribution_12m_minor": present(contributions),
        "investment_redemption_12m_minor": present(redemptions),
        "net_investment_contributions_12m_minor": present(contributions - redemptions),
        "investment_transaction_count_12m": present(count),
    }


def context_features(view: PointInTimeView) -> dict[str, FeatureOutcome]:
    """Return balance, loan, and investment context observed from account activity."""

    return {
        **_balance_features(view),
        **_loan_features(view),
        **_investment_features(view),
    }


__all__ = ["context_features"]
