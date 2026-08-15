"""Validated configuration contract for Phase 2 multi-account/card scenarios."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from finances_simulator.config import (
    ConfigModel,
    CurrencyCode,
    Merchant,
    NonEmptyString,
    ScenarioSettings,
)

ReferenceId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]


class CustomerSettingsV1(ConfigModel):
    """Customer-wide settings shared by all configured financial products."""

    currency: CurrencyCode
    primary_account_ref: ReferenceId


class InstitutionSettings(ConfigModel):
    """One fictional institution available to accounts and credit cards."""

    institution_ref: ReferenceId
    institution_id: NonEmptyString
    institution_name: NonEmptyString


class AccountSettingsV1(ConfigModel):
    """One deposit account owned by the simulated customer."""

    account_ref: ReferenceId
    institution_ref: ReferenceId
    account_label: NonEmptyString
    account_type: Literal["CHECKING", "SAVINGS"]
    opening_balance_minor: int = Field(ge=0)


class SalaryRuleV1(ConfigModel):
    """Monthly salary rule with an explicit destination account."""

    amount_minor: int = Field(gt=0)
    day_of_month: int = Field(ge=1, le=31)
    payer: NonEmptyString
    description: NonEmptyString
    destination_account_ref: ReferenceId


class FixedExpenseRuleV1(ConfigModel):
    """Monthly fixed expense with an explicit funding account."""

    rule_id: ReferenceId
    category: NonEmptyString
    amount_minor: int = Field(gt=0)
    day_of_month: int = Field(ge=1, le=31)
    payee: NonEmptyString
    description: NonEmptyString
    source_account_ref: ReferenceId


class VariableExpenseRuleV1(ConfigModel):
    """Monthly variable-expense bounds and their funding account."""

    count_min: int = Field(ge=0, le=500)
    count_max: int = Field(ge=0, le=500)
    amount_min_minor: int = Field(gt=0)
    amount_max_minor: int = Field(gt=0)
    day_min: int = Field(ge=1, le=28)
    day_max: int = Field(ge=1, le=28)
    merchants: list[Merchant] = Field(min_length=1)
    source_account_ref: ReferenceId

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        errors: list[str] = []
        if self.count_max < self.count_min:
            errors.append("count_max must be greater than or equal to count_min")
        if self.amount_max_minor < self.amount_min_minor:
            errors.append("amount_max_minor must be greater than or equal to amount_min_minor")
        if self.day_max < self.day_min:
            errors.append("day_max must be greater than or equal to day_min")
        if errors:
            raise ValueError("; ".join(errors))
        return self


class OwnTransferRule(ConfigModel):
    """Monthly transfer between two accounts owned by the customer."""

    rule_id: ReferenceId
    source_account_ref: ReferenceId
    destination_account_ref: ReferenceId
    amount_minor: int = Field(gt=0)
    day_of_month: int = Field(ge=1, le=31)
    outgoing_description: NonEmptyString
    incoming_description: NonEmptyString

    @model_validator(mode="after")
    def endpoints_must_differ(self) -> Self:
        if self.source_account_ref == self.destination_account_ref:
            raise ValueError("source_account_ref and destination_account_ref must differ")
        return self


class UtilizationPolicy(ConfigModel):
    """Authorization ceiling applied to the card's outstanding balance."""

    maximum_basis_points: int = Field(gt=0, le=10_000)
    on_exceed: Literal["DECLINE"]


class CreditCardSettings(ConfigModel):
    """One credit card, statement schedule, and payment policy."""

    card_ref: ReferenceId
    institution_ref: ReferenceId
    card_label: NonEmptyString
    credit_limit_minor: int = Field(gt=0)
    statement_close_day: int = Field(ge=1, le=31)
    payment_due_day: int = Field(ge=1, le=31)
    payment_account_ref: ReferenceId
    payment_description: NonEmptyString
    payment_policy: Literal["FULL_AUTOPAY"]
    utilization_policy: UtilizationPolicy


class CardPurchaseRule(ConfigModel):
    """Deterministic recurring credit-card purchase schedule."""

    rule_id: ReferenceId
    card_ref: ReferenceId
    merchant: NonEmptyString
    description: NonEmptyString
    amount_minor: int = Field(gt=0)
    day_of_month: int = Field(ge=1, le=31)
    start_month_index: int = Field(ge=0)
    interval_months: int = Field(gt=0)
    occurrences: int = Field(gt=0, le=1_200)
    installment_count: int = Field(gt=0, le=120)

    @model_validator(mode="after")
    def amount_must_cover_installments(self) -> Self:
        if self.amount_minor < self.installment_count:
            raise ValueError("amount_minor must be greater than or equal to installment_count")
        return self


class ScenarioConfigV1(ConfigModel):
    """Complete validated configuration for contract schema 1.1."""

    schema_version: Literal["1.1"]
    scenario: ScenarioSettings
    customer: CustomerSettingsV1
    institutions: list[InstitutionSettings] = Field(min_length=2)
    accounts: list[AccountSettingsV1] = Field(min_length=2)
    salary: SalaryRuleV1
    fixed_expenses: list[FixedExpenseRuleV1] = Field(min_length=5, max_length=5)
    variable_expenses: VariableExpenseRuleV1
    own_transfers: list[OwnTransferRule] = Field(min_length=1)
    credit_cards: list[CreditCardSettings] = Field(min_length=1)
    card_purchase_rules: list[CardPurchaseRule] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_references_and_uniqueness(self) -> Self:
        errors: list[str] = []

        def require_unique(values: list[str], label: str) -> None:
            if len(values) != len(set(values)):
                errors.append(f"{label} values must be unique")

        institution_refs = [item.institution_ref for item in self.institutions]
        institution_ids = [item.institution_id for item in self.institutions]
        account_refs = [item.account_ref for item in self.accounts]
        fixed_rule_ids = [item.rule_id for item in self.fixed_expenses]
        transfer_rule_ids = [item.rule_id for item in self.own_transfers]
        card_refs = [item.card_ref for item in self.credit_cards]
        purchase_rule_ids = [item.rule_id for item in self.card_purchase_rules]

        require_unique(institution_refs, "institutions.institution_ref")
        require_unique(institution_ids, "institutions.institution_id")
        require_unique(account_refs, "accounts.account_ref")
        require_unique(fixed_rule_ids, "fixed_expenses.rule_id")
        require_unique(transfer_rule_ids, "own_transfers.rule_id")
        require_unique(card_refs, "credit_cards.card_ref")
        require_unique(purchase_rule_ids, "card_purchase_rules.rule_id")

        if sum(rule.occurrences for rule in self.card_purchase_rules) > 10_000:
            errors.append("card_purchase_rules may schedule at most 10000 purchase attempts")
        if (
            sum(rule.occurrences * rule.installment_count for rule in self.card_purchase_rules)
            > 250_000
        ):
            errors.append("card_purchase_rules may create at most 250000 installment items")

        known_institutions = set(institution_refs)
        accounts_by_ref = {item.account_ref: item for item in self.accounts}
        known_accounts = set(accounts_by_ref)
        known_cards = set(card_refs)

        if self.customer.primary_account_ref not in known_accounts:
            errors.append("customer.primary_account_ref must reference an account")
        else:
            primary_account = accounts_by_ref[self.customer.primary_account_ref]
            if primary_account.account_type != "CHECKING":
                errors.append("customer.primary_account_ref must reference a CHECKING account")

        for account in self.accounts:
            if account.institution_ref not in known_institutions:
                errors.append(
                    f"account {account.account_ref!r} references unknown institution "
                    f"{account.institution_ref!r}"
                )

        if self.salary.destination_account_ref not in known_accounts:
            errors.append("salary.destination_account_ref must reference an account")

        for rule in self.fixed_expenses:
            if rule.source_account_ref not in known_accounts:
                errors.append(
                    f"fixed expense {rule.rule_id!r} references unknown account "
                    f"{rule.source_account_ref!r}"
                )

        if self.variable_expenses.source_account_ref not in known_accounts:
            errors.append("variable_expenses.source_account_ref must reference an account")

        for rule in self.own_transfers:
            if rule.source_account_ref not in known_accounts:
                errors.append(
                    f"own transfer {rule.rule_id!r} references unknown source account "
                    f"{rule.source_account_ref!r}"
                )
            if rule.destination_account_ref not in known_accounts:
                errors.append(
                    f"own transfer {rule.rule_id!r} references unknown destination account "
                    f"{rule.destination_account_ref!r}"
                )

        for card in self.credit_cards:
            if card.institution_ref not in known_institutions:
                errors.append(
                    f"credit card {card.card_ref!r} references unknown institution "
                    f"{card.institution_ref!r}"
                )
            if card.payment_account_ref not in known_accounts:
                errors.append(
                    f"credit card {card.card_ref!r} references unknown payment account "
                    f"{card.payment_account_ref!r}"
                )

        for rule in self.card_purchase_rules:
            if rule.card_ref not in known_cards:
                errors.append(
                    f"card purchase {rule.rule_id!r} references unknown card {rule.card_ref!r}"
                )

        if errors:
            raise ValueError("; ".join(errors))
        return self


ScenarioConfigV1_1 = ScenarioConfigV1

__all__ = [
    "AccountSettingsV1",
    "CardPurchaseRule",
    "CreditCardSettings",
    "CustomerSettingsV1",
    "FixedExpenseRuleV1",
    "InstitutionSettings",
    "OwnTransferRule",
    "SalaryRuleV1",
    "ScenarioConfigV1",
    "ScenarioConfigV1_1",
    "UtilizationPolicy",
    "VariableExpenseRuleV1",
]
