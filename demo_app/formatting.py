"""Presentation helpers: money, percentages, and plain-language labels for machine codes.

Amounts everywhere in the project are integer minor units. They are converted to text exactly once,
here, in Brazilian format, so no rounding happens anywhere near the arithmetic.
"""

from __future__ import annotations

MINOR_UNITS_PER_MAJOR = 100

#: Reason and component codes are stable machine identifiers. An executive audience should not have
#: to read them, but the raw code stays visible next to the sentence so an engineer still can.
ROUTING_REASONS: dict[str, str] = {
    "STABLE_INCOME_PREFERS_CASH_FLOW": (
        "Income was stable enough that recent cash flow was the better answer"
    ),
    "COMPLETE_COVERAGE": "The consented feed covered the whole window",
    "PARTIAL_COVERAGE": "Part of the history was outside the consent, and was reconstructed",
    "CAPACITY_MODEL_SELECTED": "The trained capacity model produced the sustainable figure",
    "CAPACITY_MODEL_UNAVAILABLE": (
        "No capacity model was loaded, so a deterministic component answered instead"
    ),
    "VOLATILE_INCOME_PREFERS_MODEL": (
        "Income varied too much for a simple average, so the model was preferred"
    ),
    "ZERO_CAPACITY_GATE": "The model judged sustainable income to be zero",
    "SHORT_HISTORY": "The history was short, which widens the uncertainty",
}

QUANTILE_UNAVAILABLE_REASONS: dict[str, str] = {
    "OUT_OF_CALIBRATED_SUPPORT": (
        "This month sits outside the range the interval model was calibrated on, so no P10-P90 "
        "band is published. The estimator declines rather than publishing a band it cannot stand "
        "behind."
    ),
    "NO_CALIBRATION_ARTIFACT": "No interval calibration was loaded for this run.",
    "ZERO_GATE_UNCERTAIN": (
        "The model could not decide confidently whether sustainable income is zero, so no band is "
        "published."
    ),
}

CONFIDENCE_COMPONENTS: dict[str, str] = {
    "data_coverage": "How much of the consented history was actually visible",
    "history_length": "How many months of history were available",
    "income_stability": "How steady the income has been",
    "classification_certainty": "How clearly each credit was income or not income",
    "component_agreement": "How closely the independent estimates agreed",
}

COMPONENT_LABELS: dict[str, str] = {
    "cashflow_baseline_0_1": "Cash-flow baseline (frozen 0.1 rule)",
    "recurring_streams_0_2": "Recurring-stream reconstruction (0.2)",
    "cash_flow_last_month": "Last month's income",
    "historical_median_12m": "12-month median",
    "recurring_stream_mean_3m": "3-month recurring-stream mean",
    "capacity_model": "Trained capacity model",
}

LIFE_EVENT_LABELS: dict[str, str] = {
    "MARRIAGE": "Marriage",
    "DIVORCE": "Divorce",
    "DEPENDENT_ADDED": "Dependent added",
    "DEPENDENT_REMOVED": "Dependent removed",
    "RAISE": "Raise",
    "PROMOTION": "Promotion",
    "JOB_LOSS": "Job loss",
    "JOB_CHANGE": "Job change",
    "BONUS": "Bonus",
    "INHERITANCE": "Inheritance",
    "VEHICLE_PURCHASE": "Vehicle purchase",
    "PROPERTY_PURCHASE": "Property purchase",
    "MEDICAL_EXPENSE": "Medical expense",
    "VACATION": "Vacation",
}


def money(minor: int | None, currency: str = "BRL") -> str:
    """Render integer minor units in Brazilian format, e.g. ``R$ 6.482,15``."""

    if minor is None:
        return "not published"
    symbol = "R$" if currency == "BRL" else f"{currency} "
    sign = "-" if minor < 0 else ""
    major, cents = divmod(abs(minor), MINOR_UNITS_PER_MAJOR)
    grouped = f"{major:,}".replace(",", ".")
    return f"{sign}{symbol} {grouped},{cents:02d}"


def money_md(minor: int | None, currency: str = "BRL") -> str:
    """Render money for a Markdown surface.

    Streamlit parses ``$ ... $`` as inline LaTeX, so two amounts in one string swallow the text
    between them. Escaping the symbol is only correct where Markdown is actually parsed, which is
    why :func:`money` stays unescaped for tables and for the export.
    """

    return money(minor, currency).replace("$", "\\$")


def money_major(minor: int | None) -> float | None:
    """Convert minor units to a float in major units, for charting only."""

    return None if minor is None else minor / MINOR_UNITS_PER_MAJOR


def percent(value: float | None, digits: int = 1) -> str:
    """Render a percentage that is already expressed in percent."""

    return "n/a" if value is None else f"{value:.{digits}f}%"


def signed_percent(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:+.{digits}f}%"


def basis_points(value: int | None, digits: int = 1) -> str:
    """Render basis points as a percentage."""

    return "n/a" if value is None else f"{value / 100:.{digits}f}%"


def describe(code: str, table: dict[str, str]) -> str:
    """Return the plain sentence for a code, falling back to the code itself."""

    return table.get(code, code.replace("_", " ").capitalize())


__all__ = [
    "COMPONENT_LABELS",
    "CONFIDENCE_COMPONENTS",
    "LIFE_EVENT_LABELS",
    "QUANTILE_UNAVAILABLE_REASONS",
    "ROUTING_REASONS",
    "basis_points",
    "describe",
    "money",
    "money_major",
    "money_md",
    "percent",
    "signed_percent",
]
