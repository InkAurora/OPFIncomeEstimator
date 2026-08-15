"""Deterministic constant-principal loan simulation."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from finances_simulator.config_v2 import ScenarioConfigV2
from finances_simulator.domain.accounts import Direction
from finances_simulator.domain.events import EconomicType, FinancialEvent
from finances_simulator.domain.loans import (
    Loan,
    LoanBalanceSnapshot,
    LoanPayment,
    LoanPaymentStatus,
)
from finances_simulator.ledger.effects import LedgerEffect, PostingPriority
from finances_simulator.simulation.primitives import (
    deterministic_id,
    month_end,
    month_start,
    scheduled_date,
)


class LoanSimulationError(ValueError):
    """Raised when configured loan state cannot be simulated consistently."""


@dataclass(frozen=True, slots=True)
class LoanSimulation:
    """Complete loan schedules plus in-window cash effects and snapshots."""

    loans: tuple[Loan, ...]
    payments: tuple[LoanPayment, ...]
    balance_snapshots: tuple[LoanBalanceSnapshot, ...]
    events: tuple[FinancialEvent, ...]
    effects: tuple[LedgerEffect, ...]


def _round_half_up_ratio(numerator: int, denominator: int) -> int:
    """Round one non-negative rational number to an integer, ties upward."""

    if numerator < 0:
        raise ValueError("numerator must be non-negative")
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    quotient, remainder = divmod(numerator, denominator)
    return quotient + (1 if remainder * 2 >= denominator else 0)


def split_constant_principal(principal_minor: int, term_months: int) -> tuple[int, ...]:
    """Split principal exactly, assigning indivisible cents to earliest payments."""

    if principal_minor <= 0:
        raise ValueError("principal_minor must be positive")
    if term_months <= 0:
        raise ValueError("term_months must be positive")
    if principal_minor < term_months:
        raise ValueError("principal_minor must be at least term_months")
    base_principal, remainder = divmod(principal_minor, term_months)
    return tuple(base_principal + (1 if index < remainder else 0) for index in range(term_months))


def monthly_interest_minor(
    opening_principal_minor: int,
    annual_interest_basis_points: int,
) -> int:
    """Return one month's interest using exact integer half-up rounding."""

    if opening_principal_minor < 0:
        raise ValueError("opening_principal_minor must be non-negative")
    if annual_interest_basis_points < 0:
        raise ValueError("annual_interest_basis_points must be non-negative")
    return _round_half_up_ratio(
        opening_principal_minor * annual_interest_basis_points,
        120_000,
    )


def simulate_loans(
    *,
    config: ScenarioConfigV2,
    loans_by_ref: dict[str, Loan],
    customer_id: str,
    start_date: date,
    end_date: date,
    months: int,
    namespace: UUID,
) -> LoanSimulation:
    """Build full SAC schedules and post disbursements/payments inside the run window."""

    if months <= 0:
        raise LoanSimulationError("months must be positive")
    configured_refs = {settings.loan_ref for settings in config.loans}
    if set(loans_by_ref) != configured_refs:
        raise LoanSimulationError("loans_by_ref must match every configured loan exactly")

    loans = tuple(sorted(loans_by_ref.values(), key=lambda item: item.loan_id))
    payments: list[LoanPayment] = []
    events: list[FinancialEvent] = []
    effects: list[LedgerEffect] = []

    for settings in sorted(config.loans, key=lambda item: item.loan_ref):
        loan = loans_by_ref[settings.loan_ref]
        if loan.customer_id != customer_id:
            raise LoanSimulationError(
                f"Loan {loan.loan_id} does not belong to customer {customer_id}."
            )
        if (
            loan.principal_minor != settings.principal_minor
            or loan.annual_interest_basis_points != settings.annual_interest_basis_points
            or loan.term_months != settings.term_months
        ):
            raise LoanSimulationError(
                f"Loan {loan.loan_id} does not match its configured financial terms."
            )

        if start_date <= loan.originated_at <= end_date:
            disbursement_event = FinancialEvent(
                event_id=loan.disbursement_event_id,
                customer_id=customer_id,
                occurred_at=loan.originated_at,
                economic_type=EconomicType.LOAN_DISBURSEMENT,
                amount_minor=loan.principal_minor,
                currency=loan.currency,
                source_entity=loan.loan_id,
                destination_entity=loan.disbursement_account_id,
                description=loan.disbursement_description,
                metadata={
                    "loan_id": loan.loan_id,
                    "loan_type": loan.loan_type,
                },
            )
            events.append(disbursement_event)
            effects.append(
                LedgerEffect(
                    event_id=disbursement_event.event_id,
                    account_id=loan.disbursement_account_id,
                    posted_at=loan.originated_at,
                    direction=Direction.CREDIT,
                    amount_minor=loan.principal_minor,
                    posting_priority=PostingPriority.LOAN_DISBURSEMENT,
                    entry_key=f"loan-disbursement:{loan.loan_id}",
                    description=loan.disbursement_description,
                )
            )

        remaining_principal = loan.principal_minor
        principal_parts = split_constant_principal(
            loan.principal_minor,
            loan.term_months,
        )
        origination_month = loan.originated_at.replace(day=1)
        for installment_index, principal_minor in enumerate(principal_parts):
            installment_number = installment_index + 1
            due_date = scheduled_date(
                month_start(origination_month, installment_number),
                settings.payment_day_of_month,
            )
            opening_principal = remaining_principal
            interest_minor = monthly_interest_minor(
                opening_principal,
                loan.annual_interest_basis_points,
            )
            remaining_principal -= principal_minor
            payment_minor = principal_minor + interest_minor
            paid = loan.originated_at <= end_date and due_date <= end_date
            payment_id = deterministic_id(
                namespace,
                "loan_payment",
                f"{loan.loan_id}:{installment_number}",
            )
            payment_event_id = (
                deterministic_id(namespace, "event", f"loan-payment:{payment_id}") if paid else None
            )
            payment = LoanPayment(
                payment_id=payment_id,
                loan_id=loan.loan_id,
                customer_id=customer_id,
                installment_number=installment_number,
                installment_count=loan.term_months,
                due_date=due_date,
                opening_principal_minor=opening_principal,
                principal_minor=principal_minor,
                interest_minor=interest_minor,
                payment_minor=payment_minor,
                remaining_principal_minor=remaining_principal,
                status=(LoanPaymentStatus.PAID if paid else LoanPaymentStatus.SCHEDULED),
                paid_at=due_date if paid else None,
                payment_event_id=payment_event_id,
            )
            payments.append(payment)

            if not paid or payment_event_id is None:
                continue
            events.append(
                FinancialEvent(
                    event_id=payment_event_id,
                    customer_id=customer_id,
                    occurred_at=due_date,
                    economic_type=EconomicType.LOAN_PAYMENT,
                    amount_minor=payment_minor,
                    currency=loan.currency,
                    source_entity=loan.payment_account_id,
                    destination_entity=loan.loan_id,
                    caused_by_event_id=loan.disbursement_event_id,
                    description=loan.payment_description,
                    metadata={
                        "loan_id": loan.loan_id,
                        "installment_number": installment_number,
                        "principal_minor": principal_minor,
                        "interest_minor": interest_minor,
                    },
                )
            )
            effects.append(
                LedgerEffect(
                    event_id=payment_event_id,
                    account_id=loan.payment_account_id,
                    posted_at=due_date,
                    direction=Direction.DEBIT,
                    amount_minor=payment_minor,
                    posting_priority=PostingPriority.LOAN_PAYMENT,
                    entry_key=f"loan-payment:{payment_id}",
                    description=loan.payment_description,
                )
            )

    payments_by_loan: dict[str, tuple[LoanPayment, ...]] = {
        loan.loan_id: tuple(
            sorted(
                (payment for payment in payments if payment.loan_id == loan.loan_id),
                key=lambda item: item.installment_number,
            )
        )
        for loan in loans
    }
    snapshots: list[LoanBalanceSnapshot] = []
    for month_index in range(months):
        reference_date = month_end(month_start(start_date, month_index))
        for loan in loans:
            if reference_date < loan.originated_at:
                continue
            remaining_principal = loan.principal_minor
            for payment in payments_by_loan[loan.loan_id]:
                if payment.due_date > reference_date:
                    break
                remaining_principal = payment.remaining_principal_minor
            snapshots.append(
                LoanBalanceSnapshot(
                    snapshot_id=deterministic_id(
                        namespace,
                        "loan_balance",
                        f"{loan.loan_id}:{reference_date.isoformat()}",
                    ),
                    customer_id=customer_id,
                    loan_id=loan.loan_id,
                    reference_date=reference_date,
                    remaining_principal_minor=remaining_principal,
                    currency=loan.currency,
                )
            )

    return LoanSimulation(
        loans=loans,
        payments=tuple(
            sorted(payments, key=lambda item: (item.due_date, item.loan_id, item.payment_id))
        ),
        balance_snapshots=tuple(
            sorted(snapshots, key=lambda item: (item.reference_date, item.loan_id))
        ),
        events=tuple(sorted(events, key=lambda item: (item.occurred_at, item.event_id))),
        effects=tuple(
            sorted(
                effects,
                key=lambda item: (item.posted_at, item.posting_priority, item.event_id),
            )
        ),
    )


__all__ = [
    "LoanSimulation",
    "LoanSimulationError",
    "monthly_interest_minor",
    "simulate_loans",
    "split_constant_principal",
]
