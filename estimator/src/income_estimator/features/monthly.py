"""Per-month observation series replayed at one reference cutoff."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from income_estimator.contracts.audit import EstimationAudit, TransactionDecision
from income_estimator.contracts.v1 import EstimatorInputV1
from income_estimator.features.outcomes import weighted_amount_minor
from income_estimator.features.point_in_time import month_index, month_of

UNAVAILABLE_REASONS = frozenset({"OBSERVED_AFTER_CUTOFF", "OUTSIDE_ESTIMATION_WINDOW"})

OWN_TRANSFER_REASONS = frozenset(
    {
        "VISIBLE_OWN_TRANSFER_PAIR",
        "EXCLUDED_DESCRIPTION_TRANSFER_FROM",
        "EXCLUDED_DESCRIPTION_OWN_TRANSFER",
    }
)
LOAN_DISBURSEMENT_REASONS = frozenset(
    {
        "LOAN_DISBURSEMENT_LINK",
        "EXCLUDED_DESCRIPTION_LOAN_DISBURSEMENT",
        "EXCLUDED_DESCRIPTION_CASH_ADVANCE",
    }
)
INVESTMENT_REDEMPTION_REASONS = frozenset(
    {
        "INVESTMENT_REDEMPTION_LINK",
        "EXCLUDED_DESCRIPTION_INVESTMENT_REDEMPTION",
    }
)
REFUND_REASONS = frozenset(
    {
        "EXCLUDED_DESCRIPTION_REFUND",
        "EXCLUDED_DESCRIPTION_REVERSAL",
        "REVERSAL_OBSERVATION",
        "REVERSED_ORIGINAL",
    }
)


@dataclass(frozen=True, slots=True)
class MonthlyObservation:
    """One calendar month summarized from observations available at the cutoff."""

    month: str
    gross_credits_minor: int
    debits_minor: int
    probable_income_minor: int
    classified_income_minor: int
    income_minor: int
    imputed_income_minor: int
    excluded_own_transfer_minor: int
    excluded_loan_disbursement_minor: int
    excluded_investment_redemption_minor: int
    excluded_refund_minor: int
    credit_count: int
    debit_count: int
    active_account_ids: frozenset[str]
    credit_counterparty_clusters: frozenset[str]


@dataclass(frozen=True, slots=True)
class PointInTimeView:
    """Everything a feature group may read for one ``customer_id`` and ``reference_month``."""

    reference_month: str
    as_of_date: date
    months: tuple[str, ...]
    request: EstimatorInputV1
    audit: EstimationAudit
    observations: tuple[MonthlyObservation, ...]
    decision_by_id: dict[str, TransactionDecision]
    available_transaction_ids: frozenset[str]
    domains: frozenset[str]

    @property
    def window_months(self) -> int:
        return len(self.months)

    def trailing(self, window: int) -> tuple[MonthlyObservation, ...]:
        """Return at most ``window`` months ending at the reference month."""

        return self.observations[-window:] if window < len(self.observations) else self.observations

    def months_since(self, month: str) -> int:
        return month_index(self.reference_month) - month_index(month)

    def in_trailing_window(self, month: str, window: int) -> bool:
        distance = self.months_since(month)
        return 0 <= distance < window


def _is_available(decision: TransactionDecision) -> bool:
    return not UNAVAILABLE_REASONS.intersection(decision.reason_codes)


def _bucket(decision: TransactionDecision, reasons: frozenset[str]) -> bool:
    return bool(reasons.intersection(decision.reason_codes))


def build_monthly_observations(
    request: EstimatorInputV1,
    audit: EstimationAudit,
    months: tuple[str, ...],
) -> tuple[MonthlyObservation, ...]:
    """Summarize each month using only decisions from the already-narrowed request."""

    decision_by_id = {item.transaction_id: item for item in audit.transaction_decisions}
    estimate_by_month = {
        item.month: item.estimated_income_minor for item in audit.estimate.monthly_estimates
    }
    imputed_by_month = {
        item.month: item.imputed_income_minor for item in audit.monthly_reconstructions
    }

    gross_credits: dict[str, int] = defaultdict(int)
    debits: dict[str, int] = defaultdict(int)
    probable: dict[str, int] = defaultdict(int)
    classified: dict[str, int] = defaultdict(int)
    own_transfer: dict[str, int] = defaultdict(int)
    loan_disbursement: dict[str, int] = defaultdict(int)
    investment_redemption: dict[str, int] = defaultdict(int)
    refund: dict[str, int] = defaultdict(int)
    credit_count: dict[str, int] = defaultdict(int)
    debit_count: dict[str, int] = defaultdict(int)
    accounts: dict[str, set[str]] = defaultdict(set)
    clusters: dict[str, set[str]] = defaultdict(set)

    for transaction in request.transactions:
        decision = decision_by_id.get(transaction.transaction_id)
        if decision is None or not _is_available(decision):
            continue
        if transaction.duplicate_of_transaction_id is not None:
            continue
        month = month_of(transaction.posted_at)
        if month not in estimate_by_month:
            continue
        accounts[month].add(transaction.account_id)
        if transaction.direction == "DEBIT":
            debits[month] += transaction.amount_minor
            debit_count[month] += 1
            continue

        gross_credits[month] += transaction.amount_minor
        credit_count[month] += 1
        clusters[month].add(decision.counterparty_cluster)
        probable[month] += weighted_amount_minor(
            transaction.amount_minor,
            decision.income_probability_basis_points,
        )
        if decision.classification == "INCOME":
            classified[month] += transaction.amount_minor
        if _bucket(decision, OWN_TRANSFER_REASONS):
            own_transfer[month] += transaction.amount_minor
        if _bucket(decision, LOAN_DISBURSEMENT_REASONS):
            loan_disbursement[month] += transaction.amount_minor
        if _bucket(decision, INVESTMENT_REDEMPTION_REASONS):
            investment_redemption[month] += transaction.amount_minor
        if _bucket(decision, REFUND_REASONS):
            refund[month] += transaction.amount_minor

    return tuple(
        MonthlyObservation(
            month=month,
            gross_credits_minor=gross_credits[month],
            debits_minor=debits[month],
            probable_income_minor=probable[month],
            classified_income_minor=classified[month],
            income_minor=estimate_by_month.get(month, 0),
            imputed_income_minor=imputed_by_month.get(month, 0),
            excluded_own_transfer_minor=own_transfer[month],
            excluded_loan_disbursement_minor=loan_disbursement[month],
            excluded_investment_redemption_minor=investment_redemption[month],
            excluded_refund_minor=refund[month],
            credit_count=credit_count[month],
            debit_count=debit_count[month],
            active_account_ids=frozenset(accounts[month]),
            credit_counterparty_clusters=frozenset(clusters[month]),
        )
        for month in months
    )


def observed_domains(
    request: EstimatorInputV1,
    available_transaction_ids: frozenset[str],
) -> frozenset[str]:
    """Report which product domains are observable at the cutoff.

    Domain presence is itself point-in-time: a loan or investment record only counts once its
    linked account transaction is visible, so a future product cannot make an earlier month look
    better covered. Credit cards require estimator input 1.2 and are never observable here.
    """

    domains: set[str] = set()
    if available_transaction_ids:
        domains.add("TRANSACTIONS")
    if getattr(request, "balances", ()):
        domains.add("BALANCES")
    if any(
        item.disbursement_transaction_id in available_transaction_ids for item in request.loans
    ):
        domains.add("LOANS")
    if any(
        item.related_account_transaction_id in available_transaction_ids
        for item in request.investment_transactions
    ):
        domains.add("INVESTMENTS")
    return frozenset(domains)


__all__ = [
    "INVESTMENT_REDEMPTION_REASONS",
    "LOAN_DISBURSEMENT_REASONS",
    "OWN_TRANSFER_REASONS",
    "REFUND_REASONS",
    "MonthlyObservation",
    "PointInTimeView",
    "build_monthly_observations",
    "observed_domains",
]
