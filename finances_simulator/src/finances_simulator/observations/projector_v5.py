"""Deterministic schema-1.5 consent and observation degradation projection."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from pydantic import BaseModel

from finances_simulator.config_v5 import ScenarioConfigV5
from finances_simulator.contracts.observed_v5 import (
    AccountV5,
    BalanceV5,
    CardInvoiceItemV5,
    CardInvoiceV5,
    CardTransactionV5,
    CreditCardV5,
    CreditLimitV5,
    InvestmentBalanceV5,
    InvestmentTransactionV5,
    InvestmentV5,
    LoanBalanceV5,
    LoanPaymentV5,
    LoanV5,
    ObservationCoverageV5,
    TransactionV5,
)
from finances_simulator.domain.accounts import Direction
from finances_simulator.observations.projector_v4 import project_observations_v4
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import deterministic_id


@dataclass(frozen=True, slots=True)
class ObservationBundleV5:
    """Complete estimator-safe schema-1.5 datasets and coverage metrics."""

    accounts: tuple[AccountV5, ...]
    balances: tuple[BalanceV5, ...]
    transactions: tuple[TransactionV5, ...]
    credit_cards: tuple[CreditCardV5, ...]
    credit_limits: tuple[CreditLimitV5, ...]
    credit_card_transactions: tuple[CardTransactionV5, ...]
    credit_card_invoices: tuple[CardInvoiceV5, ...]
    credit_card_invoice_items: tuple[CardInvoiceItemV5, ...]
    loans: tuple[LoanV5, ...]
    loan_payments: tuple[LoanPaymentV5, ...]
    loan_balances: tuple[LoanBalanceV5, ...]
    investments: tuple[InvestmentV5, ...]
    investment_transactions: tuple[InvestmentTransactionV5, ...]
    investment_balances: tuple[InvestmentBalanceV5, ...]
    observation_coverage: tuple[ObservationCoverageV5, ...]


def _upgrade[OutputRecord: BaseModel](
    record: BaseModel,
    model: type[OutputRecord],
    **updates: object,
) -> OutputRecord:
    return model.model_validate(
        {
            **record.model_dump(exclude={"schema_version"}),
            **updates,
        }
    )


def _rank(namespace: UUID, label: str, key: str) -> bytes:
    return hashlib.sha256(f"{namespace}:{label}:{key}".encode()).digest()


def _target_count(size: int, basis_points: int) -> int:
    return (size * basis_points + 5_000) // 10_000


def _pick_ids[Record: BaseModel](
    records: Iterable[Record],
    *,
    record_id: Callable[[Record], str],
    basis_points: int,
    namespace: UUID,
    label: str,
) -> set[str]:
    items = tuple(records)
    count = _target_count(len(items), basis_points)
    ranked = sorted(items, key=lambda item: _rank(namespace, label, record_id(item)))
    return {record_id(item) for item in ranked[:count]}


def _filter_by_consent[Record: BaseModel](
    records: Iterable[Record],
    *,
    scope_id: Callable[[Record], str],
    record_id: Callable[[Record], str],
    coverage_basis_points: Callable[[str], int],
    namespace: UUID,
    label: str,
) -> tuple[Record, ...]:
    grouped: dict[str, list[Record]] = {}
    for record in records:
        grouped.setdefault(scope_id(record), []).append(record)
    selected: set[str] = set()
    for scope, items in grouped.items():
        selected.update(
            _pick_ids(
                items,
                record_id=record_id,
                basis_points=coverage_basis_points(scope),
                namespace=namespace,
                label=f"consent:{label}:{scope}",
            )
        )
    return tuple(record for record in records if record_id(record) in selected)


def _delay_days(namespace: UUID, label: str, record_id: str, maximum: int) -> int:
    value = int.from_bytes(_rank(namespace, label, record_id)[:8], "big")
    return value % maximum + 1


def project_observations_v5(
    run: SimulationRun,
    config: ScenarioConfigV5,
    *,
    world_namespace: UUID,
    observation_namespace: UUID,
) -> ObservationBundleV5:
    """Degrade complete V4 observations without mutating simulated economics."""

    base = project_observations_v4(run, namespace=world_namespace)
    settings = config.observation_degradation
    default_percent = settings.consent.default_coverage_percent
    institution_percent_by_ref = {
        item.institution_ref: item.coverage_percent
        for item in settings.consent.institutions
    }
    account_percent_by_ref = {
        item.account_ref: item.coverage_percent for item in settings.consent.accounts
    }
    institution_ref_by_id = {
        item.institution_id: item.institution_ref for item in config.institutions
    }
    institution_percent_by_id = {
        item.institution_id: institution_percent_by_ref.get(
            item.institution_ref,
            default_percent,
        )
        for item in config.institutions
    }
    account_ref_by_id = {
        deterministic_id(world_namespace, "account", item.account_ref): item.account_ref
        for item in config.accounts
    }
    account_percent_by_id = {
        account.account_id: account_percent_by_ref.get(
            account_ref_by_id[account.account_id],
            institution_percent_by_id[account.institution_id],
        )
        for account in base.accounts
    }

    description_by_institution_id = {
        institution_id: rule
        for institution_id, institution_ref in institution_ref_by_id.items()
        for rule in settings.institution_descriptions
        if rule.institution_ref == institution_ref
    }

    def described(institution_id: str, description: str) -> str:
        rule = description_by_institution_id.get(institution_id)
        return description if rule is None else f"{rule.description_prefix} | {description}"

    def reversed_description(institution_id: str, description: str) -> str:
        rule = description_by_institution_id.get(institution_id)
        if rule is None:
            return f"REVERSAL | {description}"
        return f"{rule.description_prefix} | {rule.reversal_prefix} | {description}"

    def institution_coverage(institution_id: str) -> int:
        return institution_percent_by_id[institution_id] * 100

    def account_coverage(account_id: str) -> int:
        return account_percent_by_id[account_id] * 100

    card_institution_id = {item.card_id: item.institution_id for item in base.credit_cards}
    loan_institution_id = {item.loan_id: item.institution_id for item in base.loans}
    investment_institution_id = {
        item.investment_id: item.institution_id for item in base.investments
    }

    accounts = tuple(_upgrade(item, AccountV5) for item in base.accounts)
    balances = tuple(
        _upgrade(item, BalanceV5)
        for item in _filter_by_consent(
            base.balances,
            scope_id=lambda item: item.account_id,
            record_id=lambda item: item.balance_id,
            coverage_basis_points=account_coverage,
            namespace=observation_namespace,
            label="balances",
        )
    )

    transactions: list[TransactionV5] = []
    coverage: list[ObservationCoverageV5] = []
    base_transactions_by_account: dict[str, list] = {}
    for item in base.transactions:
        base_transactions_by_account.setdefault(item.account_id, []).append(item)

    account_by_id = {item.account_id: item for item in base.accounts}
    for account_id in sorted(account_by_id):
        account = account_by_id[account_id]
        eligible = tuple(base_transactions_by_account.get(account_id, ()))
        consent_ids = _pick_ids(
            eligible,
            record_id=lambda item: item.transaction_id,
            basis_points=account_coverage(account_id),
            namespace=observation_namespace,
            label=f"consent:transactions:{account_id}",
        )
        consented = tuple(item for item in eligible if item.transaction_id in consent_ids)
        missing_ids = _pick_ids(
            consented,
            record_id=lambda item: item.transaction_id,
            basis_points=settings.missing_record_basis_points,
            namespace=observation_namespace,
            label=f"missing:transactions:{account_id}",
        )
        retained = tuple(item for item in consented if item.transaction_id not in missing_ids)
        late_ids = _pick_ids(
            retained,
            record_id=lambda item: item.transaction_id,
            basis_points=settings.late_record_basis_points,
            namespace=observation_namespace,
            label=f"late:transactions:{account_id}",
        )
        duplicate_ids = _pick_ids(
            retained,
            record_id=lambda item: item.transaction_id,
            basis_points=settings.duplicate_record_basis_points,
            namespace=observation_namespace,
            label=f"duplicate:transactions:{account_id}",
        )
        reversal_ids = _pick_ids(
            retained,
            record_id=lambda item: item.transaction_id,
            basis_points=settings.reversal_record_basis_points,
            namespace=observation_namespace,
            label=f"reversal:transactions:{account_id}",
        )

        projected_by_id: dict[str, TransactionV5] = {}
        for item in retained:
            observed_date = date.fromisoformat(item.posted_at)
            if item.transaction_id in late_ids:
                observed_date += timedelta(
                    days=_delay_days(
                        observation_namespace,
                        "late-days",
                        item.transaction_id,
                        settings.maximum_late_days,
                    )
                )
            projected = _upgrade(
                item,
                TransactionV5,
                description=described(account.institution_id, item.description),
                observed_at=observed_date.isoformat(),
            )
            projected_by_id[item.transaction_id] = projected
            transactions.append(projected)

        for item in retained:
            original = projected_by_id[item.transaction_id]
            if item.transaction_id in duplicate_ids:
                transactions.append(
                    original.model_copy(
                        update={
                            "transaction_id": deterministic_id(
                                observation_namespace,
                                "observation_duplicate",
                                item.transaction_id,
                            ),
                            "observed_at": (
                                date.fromisoformat(original.observed_at) + timedelta(days=1)
                            ).isoformat(),
                            "duplicate_of_transaction_id": item.transaction_id,
                        }
                    )
                )
            if item.transaction_id in reversal_ids:
                reversal_date = date.fromisoformat(original.observed_at) + timedelta(
                    days=_delay_days(
                        observation_namespace,
                        "reversal-days",
                        item.transaction_id,
                        settings.maximum_reversal_delay_days,
                    )
                )
                reversal_direction = (
                    Direction.DEBIT
                    if item.direction is Direction.CREDIT
                    else Direction.CREDIT
                )
                balance_before_minor = (
                    item.balance_after_minor - item.amount_minor
                    if item.direction is Direction.CREDIT
                    else item.balance_after_minor + item.amount_minor
                )
                transactions.append(
                    _upgrade(
                        item,
                        TransactionV5,
                        transaction_id=deterministic_id(
                            observation_namespace,
                            "observation_reversal",
                            item.transaction_id,
                        ),
                        direction=reversal_direction,
                        balance_after_minor=balance_before_minor,
                        description=reversed_description(
                            account.institution_id,
                            item.description,
                        ),
                        observed_at=reversal_date.isoformat(),
                        reversal_of_transaction_id=item.transaction_id,
                    )
                )

        observed_count = len(retained)
        # Direct half-up ratio avoids floating point and keeps the metric bounded.
        effective = (
            (observed_count * 10_000 + len(eligible) // 2) // len(eligible)
            if eligible
            else 0
        )
        coverage.append(
            ObservationCoverageV5(
                coverage_id=deterministic_id(
                    observation_namespace,
                    "observation_coverage",
                    account_id,
                ),
                customer_id=account.customer_id,
                institution_id=account.institution_id,
                institution_name=account.institution_name,
                account_id=account_id,
                configured_coverage_percent=account_percent_by_id[account_id],
                eligible_record_count=len(eligible),
                consented_record_count=len(consented),
                observed_original_record_count=observed_count,
                missing_record_count=len(missing_ids),
                late_record_count=len(late_ids),
                duplicate_record_count=len(duplicate_ids),
                reversal_record_count=len(reversal_ids),
                effective_coverage_basis_points=effective,
            )
        )

    transactions.sort(key=lambda item: (item.observed_at, item.transaction_id))

    credit_cards = tuple(_upgrade(item, CreditCardV5) for item in base.credit_cards)
    credit_limits = tuple(
        _upgrade(item, CreditLimitV5)
        for item in _filter_by_consent(
            base.credit_limits,
            scope_id=lambda item: item.card_id,
            record_id=lambda item: item.credit_limit_id,
            coverage_basis_points=lambda card_id: institution_coverage(
                card_institution_id[card_id]
            ),
            namespace=observation_namespace,
            label="credit-limits",
        )
    )
    credit_card_transactions = tuple(
        _upgrade(
            item,
            CardTransactionV5,
            description=described(card_institution_id[item.card_id], item.description),
        )
        for item in _filter_by_consent(
            base.credit_card_transactions,
            scope_id=lambda item: item.card_id,
            record_id=lambda item: item.card_transaction_id,
            coverage_basis_points=lambda card_id: institution_coverage(
                card_institution_id[card_id]
            ),
            namespace=observation_namespace,
            label="card-transactions",
        )
    )
    credit_card_invoices = tuple(
        _upgrade(item, CardInvoiceV5)
        for item in _filter_by_consent(
            base.credit_card_invoices,
            scope_id=lambda item: item.card_id,
            record_id=lambda item: item.invoice_id,
            coverage_basis_points=lambda card_id: institution_coverage(
                card_institution_id[card_id]
            ),
            namespace=observation_namespace,
            label="card-invoices",
        )
    )
    credit_card_invoice_items = tuple(
        _upgrade(
            item,
            CardInvoiceItemV5,
            description=described(card_institution_id[item.card_id], item.description),
        )
        for item in _filter_by_consent(
            base.credit_card_invoice_items,
            scope_id=lambda item: item.card_id,
            record_id=lambda item: item.invoice_item_id,
            coverage_basis_points=lambda card_id: institution_coverage(
                card_institution_id[card_id]
            ),
            namespace=observation_namespace,
            label="card-invoice-items",
        )
    )

    loans = tuple(_upgrade(item, LoanV5) for item in base.loans)
    loan_payments = tuple(
        _upgrade(item, LoanPaymentV5)
        for item in _filter_by_consent(
            base.loan_payments,
            scope_id=lambda item: item.loan_id,
            record_id=lambda item: item.loan_payment_id,
            coverage_basis_points=lambda loan_id: institution_coverage(
                loan_institution_id[loan_id]
            ),
            namespace=observation_namespace,
            label="loan-payments",
        )
    )
    loan_balances = tuple(
        _upgrade(item, LoanBalanceV5)
        for item in _filter_by_consent(
            base.loan_balances,
            scope_id=lambda item: item.loan_id,
            record_id=lambda item: item.loan_balance_id,
            coverage_basis_points=lambda loan_id: institution_coverage(
                loan_institution_id[loan_id]
            ),
            namespace=observation_namespace,
            label="loan-balances",
        )
    )

    investments = tuple(_upgrade(item, InvestmentV5) for item in base.investments)
    investment_transactions = tuple(
        _upgrade(
            item,
            InvestmentTransactionV5,
            description=described(
                investment_institution_id[item.investment_id],
                item.description,
            ),
        )
        for item in _filter_by_consent(
            base.investment_transactions,
            scope_id=lambda item: item.investment_id,
            record_id=lambda item: item.investment_transaction_id,
            coverage_basis_points=lambda investment_id: institution_coverage(
                investment_institution_id[investment_id]
            ),
            namespace=observation_namespace,
            label="investment-transactions",
        )
    )
    investment_balances = tuple(
        _upgrade(item, InvestmentBalanceV5)
        for item in _filter_by_consent(
            base.investment_balances,
            scope_id=lambda item: item.investment_id,
            record_id=lambda item: item.investment_balance_id,
            coverage_basis_points=lambda investment_id: institution_coverage(
                investment_institution_id[investment_id]
            ),
            namespace=observation_namespace,
            label="investment-balances",
        )
    )

    projected = ObservationBundleV5(
        accounts=accounts,
        balances=balances,
        transactions=tuple(transactions),
        credit_cards=credit_cards,
        credit_limits=credit_limits,
        credit_card_transactions=credit_card_transactions,
        credit_card_invoices=credit_card_invoices,
        credit_card_invoice_items=credit_card_invoice_items,
        loans=loans,
        loan_payments=loan_payments,
        loan_balances=loan_balances,
        investments=investments,
        investment_transactions=investment_transactions,
        investment_balances=investment_balances,
        observation_coverage=tuple(coverage),
    )
    from finances_simulator.validation.v5 import validate_observation_degradation

    validate_observation_degradation(base, projected)
    return projected


__all__ = ["ObservationBundleV5", "project_observations_v5"]
