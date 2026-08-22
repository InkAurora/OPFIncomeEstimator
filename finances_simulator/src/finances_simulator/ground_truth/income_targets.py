"""Project the private income targets defined by ADR 0002.

The estimator needs distinct realized, expected, and sustainable income concepts. Only realized
income exists on the customer-month record, so this module derives the remaining targets from the
hidden simulation run, beside the engine whose parameters define them.

Expectations are exact. The engine pays one attempt with probability
``payment_probability_basis_points / 10000`` and scales the base amount by source seasonality,
scenario seasonality, and a zero-mean volatility shock. The expected amount of one attempt is
therefore ``base * source_seasonality * scenario_seasonality * probability / 10**12``. Every target
sums these integer ratios and rounds half up exactly once.

Forward-looking targets apply the state effective at the reference cutoff and never a later life
event: they describe capacity known at the reference date rather than a forecast privileged with
future knowledge.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from finances_simulator.contracts.income_targets_v1 import CustomerMonthIncomeTargetV1
from finances_simulator.domain.events import EconomicType
from finances_simulator.domain.income import IncomeSource
from finances_simulator.domain.life_events import IncomeSourceState, LifeEventTransition
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import month_end, month_start, scheduled_date

INCOME_TARGET_VERSION = "income-targets-1.0.0"
MINIMUM_CONTRACT_SCHEMA_VERSION = "1.3"

EXPECTATION_DENOMINATOR = 10**12
NEUTRAL_SEASONALITY_BASIS_POINTS = (10_000,) * 12
FORWARD_MONTHS = 12


class IncomeTargetProjectionError(ValueError):
    """Raised when a run predates the private income-source record."""


@dataclass(frozen=True, slots=True)
class _Attempt:
    """One scheduled income attempt, independent of whether the engine realized it."""

    source: IncomeSource
    month_index: int
    calendar_month: int
    occurred_at: date


def _round_half_up(numerator: int, denominator: int) -> int:
    return (numerator * 2 + denominator) // (denominator * 2)


def _scenario_seasonality(run: SimulationRun) -> tuple[int, ...]:
    configured = getattr(run, "income_seasonality_basis_points", ())
    return tuple(configured) if configured else NEUTRAL_SEASONALITY_BASIS_POINTS


def _is_horizon_limited(source: IncomeSource, months: int) -> bool:
    """Distinguish a source that ends from one whose schedule merely fills the window.

    Scenarios size ``occurrences`` to cover the simulated months, so a schedule whose next
    attempt would fall outside the window says nothing about the customer ending that income. A
    source that stops while the window still runs is a genuine end and is honored as one.
    """

    interval = source.frequency.interval_months
    return source.start_month_index + source.occurrences * interval >= months


def _attempts(run: SimulationRun) -> tuple[_Attempt, ...]:
    """Enumerate every scheduled attempt, including those beyond the simulated window.

    The simulation horizon truncates emission, but a source's schedule is a property of the
    customer rather than of the run length. Forward targets at the end of the window would
    otherwise report a spurious collapse to zero. Attempts added past the window can only ever
    fall outside it, so in-window months are unaffected.
    """

    horizon = run.months + FORWARD_MONTHS
    result: list[_Attempt] = []
    for source in run.income_sources:
        interval = source.frequency.interval_months
        occurrences = source.occurrences
        if _is_horizon_limited(source, run.months):
            occurrences = max(
                occurrences,
                -(-(horizon - source.start_month_index) // interval),
            )
        for occurrence_index in range(occurrences):
            month_index = source.start_month_index + occurrence_index * interval
            if month_index >= horizon:
                break
            current_month = month_start(run.start_date, month_index)
            result.append(
                _Attempt(
                    source=source,
                    month_index=month_index,
                    calendar_month=current_month.month,
                    occurred_at=scheduled_date(current_month, source.day_of_month),
                )
            )
    return tuple(result)


def _initial_states(run: SimulationRun) -> dict[str, IncomeSourceState]:
    return {
        source.income_source_id: IncomeSourceState(
            income_source_id=source.income_source_id,
            source_ref=source.source_ref,
            active=True,
            base_amount_minor=source.base_amount_minor,
            payer=source.payer,
            description=source.description,
        )
        for source in run.income_sources
    }


def _states_at(
    reference: date,
    initial: dict[str, IncomeSourceState],
    transitions: tuple[LifeEventTransition, ...],
) -> dict[str, IncomeSourceState]:
    """Resolve effective source state exactly as the engine does at an attempt date."""

    states = dict(initial)
    for transition in transitions:
        if transition.effective_date > reference:
            break
        for state in transition.income_sources_after:
            states[state.income_source_id] = state
    return states


def _expected_numerator(
    attempt: _Attempt,
    state: IncomeSourceState,
    scenario_seasonality: tuple[int, ...],
) -> int:
    if not state.active:
        return 0
    source = attempt.source
    return (
        state.base_amount_minor
        * source.seasonality_basis_points[attempt.calendar_month - 1]
        * scenario_seasonality[attempt.calendar_month - 1]
        * source.payment_probability_basis_points
    )


def _is_partial_month(month: date, run: SimulationRun) -> bool:
    return month < run.start_date or month_end(month) > run.end_date


def project_income_targets(run: SimulationRun) -> tuple[CustomerMonthIncomeTargetV1, ...]:
    """Return one private income-target record per simulated calendar month."""

    if run.profile.contract_schema_version < MINIMUM_CONTRACT_SCHEMA_VERSION:
        raise IncomeTargetProjectionError(
            "income targets require private income sources introduced by contract "
            f"{MINIMUM_CONTRACT_SCHEMA_VERSION}; run uses "
            f"{run.profile.contract_schema_version}"
        )

    twin = run.customer_twin
    transitions = tuple(
        sorted(run.life_event_transitions, key=lambda item: item.effective_date)
    )
    scenario_seasonality = _scenario_seasonality(run)
    attempts = _attempts(run)
    attempts_by_month: dict[int, list[_Attempt]] = defaultdict(list)
    for attempt in attempts:
        attempts_by_month[attempt.month_index].append(attempt)
    initial = _initial_states(run)

    realized_by_month: dict[str, int] = defaultdict(int)
    bonus_by_month: dict[str, int] = defaultdict(int)
    for event in run.events:
        if event.economic_type is not EconomicType.INCOME:
            continue
        month_key = event.occurred_at.strftime("%Y-%m")
        realized_by_month[month_key] += event.amount_minor
        if event.metadata.get("life_event_type") == "BONUS":
            bonus_by_month[month_key] += event.amount_minor

    records: list[CustomerMonthIncomeTargetV1] = []
    realized_series: list[int] = []
    partial_series: list[bool] = []
    for month_index in range(run.months):
        current_month = month_start(run.start_date, month_index)
        month_key = current_month.strftime("%Y-%m")
        cutoff = month_end(current_month)
        realized = realized_by_month.get(month_key, 0)
        realized_series.append(realized)
        partial_series.append(_is_partial_month(current_month, run))

        expected_numerator = 0
        for attempt in attempts_by_month.get(month_index, ()):
            states = _states_at(attempt.occurred_at, initial, transitions)
            expected_numerator += _expected_numerator(
                attempt,
                states[attempt.source.income_source_id],
                scenario_seasonality,
            )
        expected_month = _round_half_up(expected_numerator, EXPECTATION_DENOMINATOR)
        expected_month += bonus_by_month.get(month_key, 0)

        cutoff_states = _states_at(cutoff, initial, transitions)
        forward_numerator_by_source: dict[str, int] = defaultdict(int)
        remaining_by_source: dict[str, int] = defaultdict(int)
        forward_attempts_by_source: dict[str, int] = defaultdict(int)
        for attempt in attempts:
            if attempt.month_index <= month_index:
                continue
            source_id = attempt.source.income_source_id
            remaining_by_source[source_id] += 1
            if attempt.month_index > month_index + FORWARD_MONTHS:
                continue
            forward_attempts_by_source[source_id] += 1
            forward_numerator_by_source[source_id] += _expected_numerator(
                attempt,
                cutoff_states[source_id],
                scenario_seasonality,
            )

        active_source_ids = tuple(
            source.income_source_id
            for source in run.income_sources
            if cutoff_states[source.income_source_id].active
            and remaining_by_source[source.income_source_id]
        )
        recurring_source_ids = tuple(
            source_id
            for source_id in active_source_ids
            if remaining_by_source[source_id] >= 2 and forward_attempts_by_source[source_id] >= 1
        )
        expected_next_12m = _round_half_up(
            sum(forward_numerator_by_source[source_id] for source_id in active_source_ids),
            EXPECTATION_DENOMINATOR,
        )
        sustainable = _round_half_up(
            sum(forward_numerator_by_source[source_id] for source_id in recurring_source_ids),
            EXPECTATION_DENOMINATOR * FORWARD_MONTHS,
        )

        complete_window = month_index >= FORWARD_MONTHS - 1 and not any(
            partial_series[month_index - FORWARD_MONTHS + 1 : month_index + 1]
        )
        records.append(
            CustomerMonthIncomeTargetV1(
                customer_id=twin.customer_id,
                month=month_key,
                currency=twin.currency,
                realized_income_month_minor=realized,
                expected_income_month_minor=expected_month,
                sustainable_monthly_income_minor=sustainable,
                realized_income_trailing_12m_minor=(
                    sum(realized_series[month_index - FORWARD_MONTHS + 1 : month_index + 1])
                    if complete_window
                    else None
                ),
                expected_income_next_12m_minor=expected_next_12m,
                active_source_count=len(active_source_ids),
                recurring_source_count=len(recurring_source_ids),
                bonus_income_month_minor=bonus_by_month.get(month_key, 0),
                is_partial_month=partial_series[month_index],
            )
        )
    return tuple(records)


__all__ = [
    "INCOME_TARGET_VERSION",
    "CustomerMonthIncomeTargetV1",
    "IncomeTargetProjectionError",
    "project_income_targets",
]
