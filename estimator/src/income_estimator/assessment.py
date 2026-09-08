"""Decide what a month's evidence supports, separately from what the models produced.

Support checking used to happen after candidate selection and affect only whether an interval was
published. A point estimate survived it untouched, so a request with two accounts and no
transactions returned a sustainable income anyway. This module is the missing step: it reads the
evidence that produced a month and says whether that month can qualify an amount at all.

Nothing here reads a model. It reads what was observed, what was classified, and whether the
calibration covered the row. That is deliberate: an eligibility rule a bank has to sign off on must
be readable without loading an artifact.
"""

from __future__ import annotations

from income_estimator.contracts.output_v1_2 import (
    ASSESSMENT_INSUFFICIENT_EVIDENCE,
    ASSESSMENT_REVIEW_REQUIRED,
    ASSESSMENT_SUPPORTED,
)

# Share of arriving credit that may be unclassified before a month needs a person. Provisional: it
# is a starting point for the conversation with a lending policy owner, not a measured threshold,
# and it is named here so that conversation has something specific to move.
MATERIAL_UNCLASSIFIED_SHARE_BASIS_POINTS = 2_000

# A credit nothing recognizes, arriving from the same counterparty this many times across this many
# months, is a source. Below this it is noise; at or above it, the classifier is missing something a
# reviewer should look at before the month is treated as fully explained.
RECURRING_UNRECOGNIZED_MINIMUM_ITEMS = 3
RECURRING_UNRECOGNIZED_MINIMUM_MONTHS = 3

NO_OBSERVED_TRANSACTIONS = "NO_OBSERVED_TRANSACTIONS"
NO_ESTABLISHED_INCOME = "NO_ESTABLISHED_INCOME"
NO_SUSTAINABLE_ESTIMATE = "NO_SUSTAINABLE_ESTIMATE"
MATERIAL_UNCLASSIFIED_CREDITS = "MATERIAL_UNCLASSIFIED_CREDITS"
RECURRING_UNRECOGNIZED_SOURCE = "RECURRING_UNRECOGNIZED_SOURCE"
EVIDENCE_WITHIN_CALIBRATED_SUPPORT = "EVIDENCE_WITHIN_CALIBRATED_SUPPORT"


def assess_month(
    *,
    sustainable_income_minor: int | None,
    quantile_unavailable_reason: str | None,
    counted_income_minor: int,
    unclassified_credit_minor: int,
    window_has_observed_transactions: bool,
    window_has_established_income: bool,
    has_recurring_unrecognized_source: bool,
) -> tuple[str, tuple[str, ...]]:
    """Return this month's assessment status and every reason that produced it.

    The window-level inputs are window-level on purpose. A quarterly source earns nothing in two
    months out of three, and those months are not short of evidence; they are months in which
    nothing was due. What would be short of evidence is a window in which nothing was ever
    established as income at all.
    """

    blocking: list[str] = []
    if not window_has_observed_transactions:
        blocking.append(NO_OBSERVED_TRANSACTIONS)
    if not window_has_established_income:
        blocking.append(NO_ESTABLISHED_INCOME)
    if sustainable_income_minor is None:
        blocking.append(NO_SUSTAINABLE_ESTIMATE)
    if blocking:
        return ASSESSMENT_INSUFFICIENT_EVIDENCE, tuple(blocking)

    review: list[str] = []
    if quantile_unavailable_reason is not None:
        # An amount whose interval could not be published is not thereby wrong. It is unmeasured
        # here, which is a question for a person rather than an input to an automatic decision.
        review.append(quantile_unavailable_reason)
    arriving = counted_income_minor + unclassified_credit_minor
    if arriving and unclassified_credit_minor * 10_000 >= (
        arriving * MATERIAL_UNCLASSIFIED_SHARE_BASIS_POINTS
    ):
        review.append(MATERIAL_UNCLASSIFIED_CREDITS)
    if has_recurring_unrecognized_source:
        review.append(RECURRING_UNRECOGNIZED_SOURCE)
    if review:
        return ASSESSMENT_REVIEW_REQUIRED, tuple(review)

    return ASSESSMENT_SUPPORTED, (EVIDENCE_WITHIN_CALIBRATED_SUPPORT,)


def recurring_unrecognized_months(
    decisions: tuple,
) -> frozenset[str]:
    """Months carrying a credit from a counterparty that repeats and that nothing recognizes.

    Stream detection only ever sees credits already classified as income, so a payer whose
    description the rules do not know cannot become a stream however regularly it pays. Its credits
    become ordinary zeros, and an ordinary zero is indistinguishable from no payment. Letting
    recurrence count such a credit as income would replace a silent zero with a silent amount, so
    recurrence is used here only to raise the month for review.
    """

    by_cluster: dict[str, list] = {}
    for decision in decisions:
        if decision.classification != "AMBIGUOUS":
            continue
        by_cluster.setdefault(decision.counterparty_cluster, []).append(decision)

    months: set[str] = set()
    for items in by_cluster.values():
        distinct_months = {item.posted_month for item in items}
        if (
            len(items) >= RECURRING_UNRECOGNIZED_MINIMUM_ITEMS
            and len(distinct_months) >= RECURRING_UNRECOGNIZED_MINIMUM_MONTHS
        ):
            months.update(distinct_months)
    return frozenset(months)


__all__ = [
    "EVIDENCE_WITHIN_CALIBRATED_SUPPORT",
    "MATERIAL_UNCLASSIFIED_CREDITS",
    "MATERIAL_UNCLASSIFIED_SHARE_BASIS_POINTS",
    "NO_ESTABLISHED_INCOME",
    "NO_OBSERVED_TRANSACTIONS",
    "NO_SUSTAINABLE_ESTIMATE",
    "RECURRING_UNRECOGNIZED_SOURCE",
    "assess_month",
    "recurring_unrecognized_months",
]
