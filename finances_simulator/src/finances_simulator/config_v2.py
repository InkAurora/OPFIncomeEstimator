"""Validated configuration contract for Phase 3 loan/investment scenarios."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from finances_simulator.config import ConfigModel, NonEmptyString
from finances_simulator.config_v1 import (
    AccountSettingsV1,
    CreditCardSettings,
    InstitutionSettings,
    OwnTransferRule,
    ReferenceId,
    ScenarioConfigV1,
)


class LoanSettings(ConfigModel):
    """One deterministic constant-principal loan contract."""

    loan_ref: ReferenceId
    institution_ref: ReferenceId
    loan_label: NonEmptyString
    loan_type: Literal["PERSONAL"]
    principal_minor: int = Field(gt=0)
    annual_interest_basis_points: int = Field(ge=1, le=10_000)
    term_months: int = Field(ge=1, le=480)
    amortization_system: Literal["CONSTANT_PRINCIPAL"]
    disbursement_account_ref: ReferenceId
    disbursement_month_index: int = Field(ge=0, le=1_199)
    disbursement_day_of_month: int = Field(ge=1, le=31)
    payment_account_ref: ReferenceId
    payment_day_of_month: int = Field(ge=1, le=31)
    disbursement_description: NonEmptyString
    payment_description: NonEmptyString
    payment_policy: Literal["FULL_AUTOPAY"]

    @model_validator(mode="after")
    def principal_must_cover_installments(self) -> Self:
        """Keep every constant-principal component positive."""

        if self.principal_minor < self.term_months:
            raise ValueError("principal_minor must be greater than or equal to term_months")
        return self


class InvestmentSettings(ConfigModel):
    """One fixed-income investment account with deterministic monthly returns."""

    investment_ref: ReferenceId
    institution_ref: ReferenceId
    investment_label: NonEmptyString
    investment_type: Literal["FIXED_INCOME"]
    opening_balance_minor: int = Field(ge=0)
    monthly_return_basis_points: int = Field(ge=0, le=10_000)
    return_description: NonEmptyString


class InvestmentFlowRule(ConfigModel):
    """Deterministic contribution or redemption schedule."""

    rule_id: ReferenceId
    investment_ref: ReferenceId
    account_ref: ReferenceId
    amount_minor: int = Field(gt=0)
    day_of_month: int = Field(ge=1, le=31)
    start_month_index: int = Field(ge=0, le=1_199)
    interval_months: int = Field(ge=1, le=1_200)
    occurrences: int = Field(ge=1, le=1_200)
    description: NonEmptyString


class ScenarioConfigV2(ScenarioConfigV1):
    """Complete validated configuration for contract schema 1.2."""

    schema_version: Literal["1.2"]
    institutions: list[InstitutionSettings] = Field(min_length=2, max_length=32)
    accounts: list[AccountSettingsV1] = Field(min_length=2, max_length=32)
    own_transfers: list[OwnTransferRule] = Field(min_length=1, max_length=32)
    credit_cards: list[CreditCardSettings] = Field(min_length=1, max_length=32)
    loans: list[LoanSettings] = Field(min_length=1, max_length=32)
    investments: list[InvestmentSettings] = Field(min_length=1, max_length=32)
    investment_contribution_rules: list[InvestmentFlowRule] = Field(
        min_length=1,
        max_length=256,
    )
    investment_redemption_rules: list[InvestmentFlowRule] = Field(
        min_length=1,
        max_length=256,
    )

    @model_validator(mode="after")
    def validate_phase_three_references_and_bounds(self) -> Self:
        """Reject ambiguous IDs, dangling references, and amplified schedules."""

        errors: list[str] = []

        def require_unique(values: list[str], label: str) -> None:
            if len(values) != len(set(values)):
                errors.append(f"{label} values must be unique")

        loan_refs = [item.loan_ref for item in self.loans]
        investment_refs = [item.investment_ref for item in self.investments]
        contribution_rule_ids = [item.rule_id for item in self.investment_contribution_rules]
        redemption_rule_ids = [item.rule_id for item in self.investment_redemption_rules]

        require_unique(loan_refs, "loans.loan_ref")
        require_unique(investment_refs, "investments.investment_ref")
        require_unique(
            contribution_rule_ids + redemption_rule_ids,
            "investment flow rule_id",
        )

        if sum(loan.term_months for loan in self.loans) > 10_000:
            errors.append("loans may schedule at most 10000 installments")
        if (
            sum(
                rule.occurrences
                for rule in (
                    *self.investment_contribution_rules,
                    *self.investment_redemption_rules,
                )
            )
            > 10_000
        ):
            errors.append("investment flow rules may schedule at most 10000 attempts")

        known_institutions = {item.institution_ref for item in self.institutions}
        known_accounts = {item.account_ref for item in self.accounts}
        known_investments = set(investment_refs)

        for loan in self.loans:
            if loan.institution_ref not in known_institutions:
                errors.append(
                    f"loan {loan.loan_ref!r} references unknown institution "
                    f"{loan.institution_ref!r}"
                )
            if loan.disbursement_account_ref not in known_accounts:
                errors.append(
                    f"loan {loan.loan_ref!r} references unknown disbursement account "
                    f"{loan.disbursement_account_ref!r}"
                )
            if loan.payment_account_ref not in known_accounts:
                errors.append(
                    f"loan {loan.loan_ref!r} references unknown payment account "
                    f"{loan.payment_account_ref!r}"
                )

        for investment in self.investments:
            if investment.institution_ref not in known_institutions:
                errors.append(
                    f"investment {investment.investment_ref!r} references unknown institution "
                    f"{investment.institution_ref!r}"
                )

        for label, rules in (
            ("investment contribution", self.investment_contribution_rules),
            ("investment redemption", self.investment_redemption_rules),
        ):
            for rule in rules:
                if rule.investment_ref not in known_investments:
                    errors.append(
                        f"{label} {rule.rule_id!r} references unknown investment "
                        f"{rule.investment_ref!r}"
                    )
                if rule.account_ref not in known_accounts:
                    errors.append(
                        f"{label} {rule.rule_id!r} references unknown account {rule.account_ref!r}"
                    )

        if errors:
            raise ValueError("; ".join(errors))
        return self


ScenarioConfigV1_2 = ScenarioConfigV2

__all__ = [
    "InvestmentFlowRule",
    "InvestmentSettings",
    "LoanSettings",
    "ScenarioConfigV1_2",
    "ScenarioConfigV2",
]
