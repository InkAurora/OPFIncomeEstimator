"""Versioned customer-month feature schema.

Every feature name, group, unit, rolling window, and formula is frozen by ``FEATURE_SET_VERSION``
and summarized by ``FEATURE_SCHEMA_FINGERPRINT``. Changing any of them must bump both constants so
previously written feature tables, and any model trained on them, stay reproducible.

Monetary features use integer minor currency units. Ratios are rounded to eight decimals. Features
that cannot be computed from the available contract or history are reported as explicitly missing
with a reason code; they are never defaulted to zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

FEATURE_SET_VERSION = "customer-month-features-1.2.0"

FeatureGroup = Literal[
    "CASH_FLOW",
    "STABILITY",
    "SOURCES",
    "COVERAGE",
    "ACTIVITY",
    "CONTEXT",
    "CAPACITY",
]
FeatureUnit = Literal[
    "MINOR",
    "MINOR_SQUARED",
    "RATIO",
    "BASIS_POINTS",
    "COUNT",
    "MONTHS",
    "DAYS",
]

MISSING_CONTRACT_DOMAIN_UNAVAILABLE = "CONTRACT_DOMAIN_UNAVAILABLE"
MISSING_NO_OBSERVED_RECORDS = "NO_OBSERVED_RECORDS"
MISSING_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
MISSING_UNDEFINED_DENOMINATOR = "UNDEFINED_ZERO_DENOMINATOR"

MISSING_REASONS = (
    MISSING_CONTRACT_DOMAIN_UNAVAILABLE,
    MISSING_INSUFFICIENT_HISTORY,
    MISSING_NO_OBSERVED_RECORDS,
    MISSING_UNDEFINED_DENOMINATOR,
)

PRODUCT_DOMAINS = ("TRANSACTIONS", "BALANCES", "LOANS", "INVESTMENTS", "CREDIT_CARDS")

CAPACITY_FEATURE_CONTRACT_VERSION = "1.2"


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """One versioned feature definition."""

    name: str
    group: FeatureGroup
    unit: FeatureUnit
    window_months: int | None
    formula: str


def _rolling(
    name_template: str,
    group: FeatureGroup,
    unit: FeatureUnit,
    windows: tuple[int, ...],
    formula_template: str,
) -> tuple[FeatureSpec, ...]:
    return tuple(
        FeatureSpec(
            name=name_template.format(window=window),
            group=group,
            unit=unit,
            window_months=window,
            formula=formula_template.format(window=window),
        )
        for window in windows
    )


_ALL_WINDOWS = (1, 3, 6, 12)
_AGGREGATE_WINDOWS = (3, 6, 12)
_TRAILING = "trailing {window} months ending at reference_month, clipped to window_start"

_CASH_FLOW_SCHEMA: tuple[FeatureSpec, ...] = (
    *_rolling(
        "credits_{window}m_minor",
        "CASH_FLOW",
        "MINOR",
        _ALL_WINDOWS,
        "sum of observed non-duplicate credit amounts posted in the " + _TRAILING,
    ),
    *_rolling(
        "debits_{window}m_minor",
        "CASH_FLOW",
        "MINOR",
        _ALL_WINDOWS,
        "sum of observed non-duplicate debit amounts posted in the " + _TRAILING,
    ),
    *_rolling(
        "probable_income_{window}m_minor",
        "CASH_FLOW",
        "MINOR",
        _ALL_WINDOWS,
        "sum of credit_amount times p_income over the " + _TRAILING,
    ),
    *_rolling(
        "probable_income_mean_{window}m_minor",
        "CASH_FLOW",
        "MINOR",
        _AGGREGATE_WINDOWS,
        "arithmetic mean of monthly probable income over the " + _TRAILING,
    ),
    *_rolling(
        "probable_income_median_{window}m_minor",
        "CASH_FLOW",
        "MINOR",
        _AGGREGATE_WINDOWS,
        "median of monthly probable income over the " + _TRAILING,
    ),
    *_rolling(
        "income_{window}m_minor",
        "CASH_FLOW",
        "MINOR",
        _ALL_WINDOWS,
        "sum of reconstructed monthly income over the " + _TRAILING,
    ),
    FeatureSpec(
        name="imputed_income_12m_minor",
        group="CASH_FLOW",
        unit="MINOR",
        window_months=12,
        formula="sum of stream-imputed monthly income over the trailing 12 months",
    ),
    FeatureSpec(
        name="excluded_own_transfer_12m_minor",
        group="CASH_FLOW",
        unit="MINOR",
        window_months=12,
        formula="sum of credits excluded as visible own transfers in the trailing 12 months",
    ),
    FeatureSpec(
        name="excluded_loan_disbursement_12m_minor",
        group="CASH_FLOW",
        unit="MINOR",
        window_months=12,
        formula="sum of credits excluded as loan disbursements in the trailing 12 months",
    ),
    FeatureSpec(
        name="excluded_investment_redemption_12m_minor",
        group="CASH_FLOW",
        unit="MINOR",
        window_months=12,
        formula="sum of credits excluded as investment redemptions in the trailing 12 months",
    ),
    FeatureSpec(
        name="excluded_refund_12m_minor",
        group="CASH_FLOW",
        unit="MINOR",
        window_months=12,
        formula="sum of credits excluded as refunds or reversals in the trailing 12 months",
    ),
)

_STABILITY_SCHEMA: tuple[FeatureSpec, ...] = (
    *_rolling(
        "income_mean_{window}m_minor",
        "STABILITY",
        "MINOR",
        _AGGREGATE_WINDOWS,
        "arithmetic mean of reconstructed monthly income over the " + _TRAILING,
    ),
    *_rolling(
        "income_median_{window}m_minor",
        "STABILITY",
        "MINOR",
        _AGGREGATE_WINDOWS,
        "median of reconstructed monthly income over the " + _TRAILING,
    ),
    *_rolling(
        "income_std_{window}m_minor",
        "STABILITY",
        "MINOR",
        _AGGREGATE_WINDOWS,
        "population standard deviation of monthly income over the " + _TRAILING,
    ),
    *_rolling(
        "income_variance_{window}m",
        "STABILITY",
        "MINOR_SQUARED",
        _AGGREGATE_WINDOWS,
        "population variance of monthly income over the " + _TRAILING,
    ),
    *_rolling(
        "income_cv_{window}m",
        "STABILITY",
        "RATIO",
        (6, 12),
        "population standard deviation divided by mean monthly income over the " + _TRAILING,
    ),
    *_rolling(
        "zero_income_months_{window}m",
        "STABILITY",
        "COUNT",
        (6, 12),
        "count of months with zero reconstructed income in the " + _TRAILING,
    ),
    FeatureSpec(
        name="income_p25_12m_minor",
        group="STABILITY",
        unit="MINOR",
        window_months=12,
        formula="inclusive first quartile of monthly income over the trailing 12 months",
    ),
    FeatureSpec(
        name="income_p50_12m_minor",
        group="STABILITY",
        unit="MINOR",
        window_months=12,
        formula="inclusive second quartile of monthly income over the trailing 12 months",
    ),
    FeatureSpec(
        name="income_p75_12m_minor",
        group="STABILITY",
        unit="MINOR",
        window_months=12,
        formula="inclusive third quartile of monthly income over the trailing 12 months",
    ),
    FeatureSpec(
        name="income_min_12m_minor",
        group="STABILITY",
        unit="MINOR",
        window_months=12,
        formula="minimum reconstructed monthly income over the trailing 12 months",
    ),
    FeatureSpec(
        name="income_max_12m_minor",
        group="STABILITY",
        unit="MINOR",
        window_months=12,
        formula="maximum reconstructed monthly income over the trailing 12 months",
    ),
)

_SOURCES_SCHEMA: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        name="source_count_12m",
        group="SOURCES",
        unit="COUNT",
        window_months=12,
        formula="detected income streams with at least one credit in the trailing 12 months",
    ),
    FeatureSpec(
        name="recurring_source_count_12m",
        group="SOURCES",
        unit="COUNT",
        window_months=12,
        formula="active streams classified RECURRING_SOURCE in the trailing 12 months",
    ),
    FeatureSpec(
        name="ecosystem_source_count_12m",
        group="SOURCES",
        unit="COUNT",
        window_months=12,
        formula="active streams classified INCOME_ECOSYSTEM in the trailing 12 months",
    ),
    FeatureSpec(
        name="active_source_count_3m",
        group="SOURCES",
        unit="COUNT",
        window_months=3,
        formula="detected income streams with at least one credit in the trailing 3 months",
    ),
    FeatureSpec(
        name="source_income_12m_minor",
        group="SOURCES",
        unit="MINOR",
        window_months=12,
        formula="sum of stream credit amounts posted in the trailing 12 months",
    ),
    FeatureSpec(
        name="largest_source_share_12m",
        group="SOURCES",
        unit="RATIO",
        window_months=12,
        formula="largest trailing 12-month stream amount divided by source_income_12m_minor",
    ),
    FeatureSpec(
        name="source_concentration_hhi_12m",
        group="SOURCES",
        unit="RATIO",
        window_months=12,
        formula="Herfindahl-Hirschman index of trailing 12-month stream amount shares",
    ),
    FeatureSpec(
        name="recurrence_score_mean_12m_basis_points",
        group="SOURCES",
        unit="BASIS_POINTS",
        window_months=12,
        formula="mean recurrence score of streams active in the trailing 12 months",
    ),
    FeatureSpec(
        name="recurrence_score_max_12m_basis_points",
        group="SOURCES",
        unit="BASIS_POINTS",
        window_months=12,
        formula="maximum recurrence score of streams active in the trailing 12 months",
    ),
    FeatureSpec(
        name="source_amount_cv_mean_12m",
        group="SOURCES",
        unit="RATIO",
        window_months=12,
        formula="mean amount coefficient of variation of streams active in trailing 12 months",
    ),
    FeatureSpec(
        name="source_monthly_capacity_minor",
        group="SOURCES",
        unit="MINOR",
        window_months=12,
        formula=(
            "sum of frequency-normalized monthly rates of streams active in the trailing 12 "
            "months; a quarterly source contributes a third of its payment"
        ),
    ),
    FeatureSpec(
        name="largest_source_monthly_capacity_minor",
        group="SOURCES",
        unit="MINOR",
        window_months=12,
        formula="largest frequency-normalized monthly rate among active streams",
    ),
    FeatureSpec(
        name="source_frequency_confidence_mean_basis_points",
        group="SOURCES",
        unit="BASIS_POINTS",
        window_months=12,
        formula=(
            "mean support for each active stream's inferred cadence, from gap regularity and "
            "observation count"
        ),
    ),
    FeatureSpec(
        name="source_observation_count_12m",
        group="SOURCES",
        unit="COUNT",
        window_months=12,
        formula="credits belonging to active streams posted in the trailing 12 months",
    ),
    FeatureSpec(
        name="source_age_months_max",
        group="SOURCES",
        unit="COUNT",
        window_months=12,
        formula="months between the earliest first_seen among active streams and the cutoff",
    ),
    FeatureSpec(
        name="has_no_detected_source",
        group="SOURCES",
        unit="COUNT",
        window_months=12,
        formula="1 when no stream has a credit in the trailing 12 months, else 0",
    ),
    FeatureSpec(
        name="months_since_last_source_activity",
        group="SOURCES",
        unit="MONTHS",
        window_months=None,
        formula="reference_month minus the latest month holding any detected stream credit",
    ),
)

_COVERAGE_SCHEMA: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        name="window_months",
        group="COVERAGE",
        unit="COUNT",
        window_months=None,
        formula="months from window_start through reference_month inclusive",
    ),
    FeatureSpec(
        name="months_observed",
        group="COVERAGE",
        unit="COUNT",
        window_months=None,
        formula="distinct in-window months holding at least one transaction observed by cutoff",
    ),
    FeatureSpec(
        name="months_since_first_observation",
        group="COVERAGE",
        unit="MONTHS",
        window_months=None,
        formula="reference_month minus the earliest observed in-window transaction month",
    ),
    FeatureSpec(
        name="accounts_declared",
        group="COVERAGE",
        unit="COUNT",
        window_months=None,
        formula="accounts present in the consented estimator input",
    ),
    FeatureSpec(
        name="accounts_observed",
        group="COVERAGE",
        unit="COUNT",
        window_months=None,
        formula="declared accounts holding at least one transaction observed by cutoff",
    ),
    FeatureSpec(
        name="institutions_observed",
        group="COVERAGE",
        unit="COUNT",
        window_months=None,
        formula="distinct institutions of accounts observed by cutoff",
    ),
    FeatureSpec(
        name="active_accounts_3m",
        group="COVERAGE",
        unit="COUNT",
        window_months=3,
        formula="accounts with at least one transaction posted in the trailing 3 months",
    ),
    FeatureSpec(
        name="effective_consent_coverage_basis_points",
        group="COVERAGE",
        unit="BASIS_POINTS",
        window_months=None,
        formula=(
            "eligible-record weighted mean account coverage; provider consent metadata declared "
            "for the whole window and not recomputed per cutoff"
        ),
    ),
    FeatureSpec(
        name="minimum_account_coverage_basis_points",
        group="COVERAGE",
        unit="BASIS_POINTS",
        window_months=None,
        formula="minimum declared account coverage in basis points",
    ),
    FeatureSpec(
        name="observed_domain_count",
        group="COVERAGE",
        unit="COUNT",
        window_months=None,
        formula="product domains observable at cutoff among PRODUCT_DOMAINS",
    ),
    FeatureSpec(
        name="data_completeness_score_basis_points",
        group="COVERAGE",
        unit="BASIS_POINTS",
        window_months=None,
        formula=(
            "floor of the mean of available components: consent coverage, months_observed capped "
            "at 12, observed-account ratio, and observed-domain ratio"
        ),
    ),
)

_ACTIVITY_SCHEMA: tuple[FeatureSpec, ...] = (
    *_rolling(
        "transaction_count_{window}m",
        "ACTIVITY",
        "COUNT",
        (1, 3, 12),
        "observed non-duplicate transactions posted in the " + _TRAILING,
    ),
    *_rolling(
        "credit_count_{window}m",
        "ACTIVITY",
        "COUNT",
        (1, 3, 12),
        "observed non-duplicate credits posted in the " + _TRAILING,
    ),
    *_rolling(
        "debit_count_{window}m",
        "ACTIVITY",
        "COUNT",
        (3, 12),
        "observed non-duplicate debits posted in the " + _TRAILING,
    ),
    *_rolling(
        "distinct_credit_counterparties_{window}m",
        "ACTIVITY",
        "COUNT",
        (3, 12),
        "distinct credit counterparty clusters in the " + _TRAILING,
    ),
    FeatureSpec(
        name="days_since_last_credit",
        group="ACTIVITY",
        unit="DAYS",
        window_months=None,
        formula="as_of_date minus the latest observed in-window credit posting date",
    ),
    FeatureSpec(
        name="days_since_last_transaction",
        group="ACTIVITY",
        unit="DAYS",
        window_months=None,
        formula="as_of_date minus the latest observed in-window transaction posting date",
    ),
)

_CONTEXT_SCHEMA: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        name="available_balance_minor",
        group="CONTEXT",
        unit="MINOR",
        window_months=None,
        formula="sum over accounts of the latest balance with reference_date at or before cutoff",
    ),
    FeatureSpec(
        name="balance_accounts_observed",
        group="CONTEXT",
        unit="COUNT",
        window_months=None,
        formula="accounts holding at least one balance observed by cutoff",
    ),
    FeatureSpec(
        name="balance_staleness_days",
        group="CONTEXT",
        unit="DAYS",
        window_months=None,
        formula="as_of_date minus the latest observed balance reference_date",
    ),
    FeatureSpec(
        name="observed_loan_count",
        group="CONTEXT",
        unit="COUNT",
        window_months=None,
        formula="loans whose disbursement transaction is observed in window by cutoff",
    ),
    FeatureSpec(
        name="loan_disbursement_12m_minor",
        group="CONTEXT",
        unit="MINOR",
        window_months=12,
        formula="sum of observed loan disbursement credits in the trailing 12 months",
    ),
    FeatureSpec(
        name="months_since_last_loan_disbursement",
        group="CONTEXT",
        unit="MONTHS",
        window_months=None,
        formula="reference_month minus the latest observed loan disbursement month",
    ),
    FeatureSpec(
        name="investment_contribution_12m_minor",
        group="CONTEXT",
        unit="MINOR",
        window_months=12,
        formula="sum of observed investment contribution debits in the trailing 12 months",
    ),
    FeatureSpec(
        name="investment_redemption_12m_minor",
        group="CONTEXT",
        unit="MINOR",
        window_months=12,
        formula="sum of observed investment redemption credits in the trailing 12 months",
    ),
    FeatureSpec(
        name="net_investment_contributions_12m_minor",
        group="CONTEXT",
        unit="MINOR",
        window_months=12,
        formula="investment_contribution_12m_minor minus investment_redemption_12m_minor",
    ),
    FeatureSpec(
        name="investment_transaction_count_12m",
        group="CONTEXT",
        unit="COUNT",
        window_months=12,
        formula="observed investment transactions linked to in-window trailing 12-month postings",
    ),
)

_CAPACITY_SCHEMA: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        name="card_spend_3m_minor",
        group="CAPACITY",
        unit="MINOR",
        window_months=3,
        formula="sum of observed card purchase amounts in the trailing 3 months",
    ),
    FeatureSpec(
        name="credit_utilization_ratio",
        group="CAPACITY",
        unit="RATIO",
        window_months=None,
        formula="summed used limit divided by summed total limit of the latest card snapshots",
    ),
    FeatureSpec(
        name="installment_commitment_minor",
        group="CAPACITY",
        unit="MINOR",
        window_months=None,
        formula=(
            "unbilled remainder of observed installment purchases, billing one installment per "
            "calendar month from the purchase month; contract 1.2 exposes no statement close day"
        ),
    ),
    FeatureSpec(
        name="monthly_debt_payment_minor",
        group="CAPACITY",
        unit="MINOR",
        window_months=1,
        formula="sum of observed loan installment totals due in the reference month",
    ),
    FeatureSpec(
        name="outstanding_debt_minor",
        group="CAPACITY",
        unit="MINOR",
        window_months=None,
        formula=(
            "latest remaining principal summed across loans plus latest used limit summed across "
            "cards"
        ),
    ),
    FeatureSpec(
        name="investment_balance_minor",
        group="CAPACITY",
        unit="MINOR",
        window_months=None,
        formula="latest observed balance summed across investments",
    ),
)

FEATURE_SCHEMA: tuple[FeatureSpec, ...] = (
    *_CASH_FLOW_SCHEMA,
    *_STABILITY_SCHEMA,
    *_SOURCES_SCHEMA,
    *_COVERAGE_SCHEMA,
    *_ACTIVITY_SCHEMA,
    *_CONTEXT_SCHEMA,
    *_CAPACITY_SCHEMA,
)

FEATURE_NAMES: tuple[str, ...] = tuple(spec.name for spec in FEATURE_SCHEMA)
FEATURE_SPEC_BY_NAME: dict[str, FeatureSpec] = {spec.name: spec for spec in FEATURE_SCHEMA}

if len(FEATURE_NAMES) != len(FEATURE_SPEC_BY_NAME):  # pragma: no cover - import-time guard
    raise RuntimeError("customer-month feature names must be unique")


def feature_schema_fingerprint(schema: tuple[FeatureSpec, ...] = FEATURE_SCHEMA) -> str:
    """Hash names, groups, units, windows, and formulas so silent drift cannot pass review."""

    payload = json.dumps(
        [[spec.name, spec.group, spec.unit, spec.window_months, spec.formula] for spec in schema],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:32]


FEATURE_SCHEMA_FINGERPRINT = feature_schema_fingerprint()

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_SCHEMA",
    "FEATURE_SCHEMA_FINGERPRINT",
    "FEATURE_SET_VERSION",
    "FEATURE_SPEC_BY_NAME",
    "MISSING_CONTRACT_DOMAIN_UNAVAILABLE",
    "MISSING_INSUFFICIENT_HISTORY",
    "MISSING_NO_OBSERVED_RECORDS",
    "MISSING_REASONS",
    "MISSING_UNDEFINED_DENOMINATOR",
    "PRODUCT_DOMAINS",
    "FeatureGroup",
    "FeatureSpec",
    "FeatureUnit",
    "feature_schema_fingerprint",
]
