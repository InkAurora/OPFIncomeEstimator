"""Consent-coverage, observation-history, and account-activity features."""

from __future__ import annotations

from datetime import date

from income_estimator.features.monthly import PointInTimeView
from income_estimator.features.outcomes import (
    FeatureOutcome,
    missing,
    present,
    round_basis_points,
)
from income_estimator.features.schema import MISSING_NO_OBSERVED_RECORDS, PRODUCT_DOMAINS
from income_estimator.models.cashflow import _coverage_by_account


def _consent_coverage(view: PointInTimeView) -> tuple[FeatureOutcome, FeatureOutcome]:
    """Weight declared account coverage by eligible records, not by account count."""

    records = view.request.coverage
    if not records:
        return (
            missing(MISSING_NO_OBSERVED_RECORDS),
            missing(MISSING_NO_OBSERVED_RECORDS),
        )
    normalized = _coverage_by_account(view.request)
    weights = {record.account_id: record.eligible_record_count for record in records}
    total_weight = sum(weights.values())
    if total_weight:
        weighted = (
            sum(normalized[account_id] * weight for account_id, weight in weights.items())
            / total_weight
        )
    else:
        weighted = sum(normalized.values()) / len(normalized)
    return (
        present(round_basis_points(weighted)),
        present(min(normalized.values())),
    )


def coverage_features(view: PointInTimeView) -> dict[str, FeatureOutcome]:
    """Describe how much of the customer's financial life is observable at the cutoff."""

    observed_months = tuple(item.month for item in view.observations if item.active_account_ids)
    observed_accounts: set[str] = set()
    for item in view.observations:
        observed_accounts.update(item.active_account_ids)
    institutions = {
        account.institution_id
        for account in view.request.accounts
        if account.account_id in observed_accounts
    }
    recent_accounts: set[str] = set()
    for item in view.trailing(3):
        recent_accounts.update(item.active_account_ids)

    consent_coverage, minimum_coverage = _consent_coverage(view)
    accounts_declared = len(view.request.accounts)

    result: dict[str, FeatureOutcome] = {
        "window_months": present(view.window_months),
        "months_observed": present(len(observed_months)),
        "months_since_first_observation": (
            present(view.months_since(observed_months[0]))
            if observed_months
            else missing(MISSING_NO_OBSERVED_RECORDS)
        ),
        "accounts_declared": present(accounts_declared),
        "accounts_observed": present(len(observed_accounts)),
        "institutions_observed": present(len(institutions)),
        "active_accounts_3m": present(len(recent_accounts)),
        "effective_consent_coverage_basis_points": consent_coverage,
        "minimum_account_coverage_basis_points": minimum_coverage,
        "observed_domain_count": present(len(view.domains)),
    }

    components = [
        min(10_000, 10_000 * len(observed_months) // 12),
        10_000 * len(observed_accounts) // accounts_declared if accounts_declared else 0,
        10_000 * len(view.domains) // len(PRODUCT_DOMAINS),
    ]
    if not consent_coverage.is_missing:
        components.append(int(consent_coverage.value))
    result["data_completeness_score_basis_points"] = present(sum(components) // len(components))
    return result


def activity_features(view: PointInTimeView) -> dict[str, FeatureOutcome]:
    """Count observed movement and measure how recently the accounts were used."""

    result: dict[str, FeatureOutcome] = {}
    for window in (1, 3, 12):
        trailing = view.trailing(window)
        credits = sum(item.credit_count for item in trailing)
        debits = sum(item.debit_count for item in trailing)
        result[f"transaction_count_{window}m"] = present(credits + debits)
        result[f"credit_count_{window}m"] = present(credits)
        if window in (3, 12):
            result[f"debit_count_{window}m"] = present(debits)
            clusters: set[str] = set()
            for item in trailing:
                clusters.update(item.credit_counterparty_clusters)
            result[f"distinct_credit_counterparties_{window}m"] = present(len(clusters))

    last_credit: str | None = None
    last_transaction: str | None = None
    for transaction in view.request.transactions:
        if (
            transaction.transaction_id not in view.available_transaction_ids
            or transaction.duplicate_of_transaction_id is not None
        ):
            continue
        posted_at = transaction.posted_at
        if last_transaction is None or posted_at > last_transaction:
            last_transaction = posted_at
        if transaction.direction == "CREDIT" and (
            last_credit is None or posted_at > last_credit
        ):
            last_credit = posted_at

    result["days_since_last_credit"] = (
        present((view.as_of_date - date.fromisoformat(last_credit)).days)
        if last_credit
        else missing(MISSING_NO_OBSERVED_RECORDS)
    )
    result["days_since_last_transaction"] = (
        present((view.as_of_date - date.fromisoformat(last_transaction)).days)
        if last_transaction
        else missing(MISSING_NO_OBSERVED_RECORDS)
    )
    return result



__all__ = ["activity_features", "coverage_features"]
