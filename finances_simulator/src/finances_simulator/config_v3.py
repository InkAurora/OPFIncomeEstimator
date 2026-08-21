"""Validated configuration contract for Phase 4 income-diversity scenarios."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from finances_simulator.config import ConfigModel, NonEmptyString, ScenarioSettings
from finances_simulator.config_v1 import (
    AccountSettingsV1,
    CardPurchaseRule,
    CreditCardSettings,
    CustomerSettingsV1,
    FixedExpenseRuleV1,
    InstitutionSettings,
    OwnTransferRule,
    ReferenceId,
    VariableExpenseRuleV1,
)
from finances_simulator.config_v2 import (
    InvestmentFlowRule,
    InvestmentSettings,
    LoanSettings,
)
from finances_simulator.domain.income import (
    MAX_INCOME_AMOUNT_MINOR,
    BehaviorProfile,
    IncomeFrequency,
    IncomeKind,
    IncomeProfile,
    SeasonalityBasisPoints,
    WealthBand,
)


class UniformMinorAmount(ConfigModel):
    """Bounded uniform distribution sampled once for an income source."""

    minimum_minor: int = Field(gt=0, le=MAX_INCOME_AMOUNT_MINOR)
    maximum_minor: int = Field(gt=0, le=MAX_INCOME_AMOUNT_MINOR)
    step_minor: int = Field(gt=0, le=MAX_INCOME_AMOUNT_MINOR)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.maximum_minor < self.minimum_minor:
            raise ValueError("maximum_minor must be greater than or equal to minimum_minor")
        if (self.maximum_minor - self.minimum_minor) % self.step_minor != 0:
            raise ValueError("maximum_minor - minimum_minor must be divisible by step_minor")
        return self


class IncomeSourceTemplate(ConfigModel):
    """Conditional source template from which one source is sampled."""

    source_ref: ReferenceId
    income_kind: IncomeKind
    payer: NonEmptyString
    description: NonEmptyString
    destination_account_ref: ReferenceId
    amount_distribution: UniformMinorAmount
    day_of_month: int = Field(ge=1, le=31)
    frequency: IncomeFrequency
    start_month_index: int = Field(ge=0, le=1_199)
    occurrences: int = Field(ge=1, le=1_200)
    payment_probability_basis_points: int = Field(ge=0, le=10_000)
    volatility_basis_points: int = Field(ge=0, le=10_000)
    seasonality_basis_points: tuple[SeasonalityBasisPoints, ...] = Field(
        min_length=12,
        max_length=12,
    )


class IncomeSourceBundle(ConfigModel):
    """One weighted set of sources conditional on an income profile."""

    source_bundle_ref: ReferenceId
    weight_basis_points: int = Field(gt=0, le=10_000)
    sources: list[IncomeSourceTemplate] = Field(max_length=8)

    @model_validator(mode="after")
    def source_references_must_be_unique(self) -> Self:
        refs = [source.source_ref for source in self.sources]
        if len(refs) != len(set(refs)):
            raise ValueError("sources source_ref values must be unique within a bundle")
        return self


class IncomeProfileDistribution(ConfigModel):
    """Weighted profile with a nested conditional source-bundle distribution."""

    income_profile: IncomeProfile
    weight_basis_points: int = Field(gt=0, le=10_000)
    source_bundles: list[IncomeSourceBundle] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_conditional_bundles(self) -> Self:
        errors: list[str] = []
        if sum(bundle.weight_basis_points for bundle in self.source_bundles) != 10_000:
            errors.append("source_bundles weight_basis_points must sum to 10000")
        bundle_refs = [bundle.source_bundle_ref for bundle in self.source_bundles]
        if len(bundle_refs) != len(set(bundle_refs)):
            errors.append("source_bundles source_bundle_ref values must be unique")

        required_kind = {
            IncomeProfile.SALARIED: IncomeKind.SALARY,
            IncomeProfile.SELF_EMPLOYED: IncomeKind.SELF_EMPLOYMENT,
            IncomeProfile.BUSINESS_OWNER: IncomeKind.BUSINESS_PROFIT,
            IncomeProfile.RETIRED: IncomeKind.PENSION,
            IncomeProfile.INVESTOR: IncomeKind.INVESTMENT_DISTRIBUTION,
        }.get(self.income_profile)
        for bundle in self.source_bundles:
            kinds = {source.income_kind for source in bundle.sources}
            if required_kind is not None and required_kind not in kinds:
                errors.append(
                    f"bundle {bundle.source_bundle_ref!r} for {self.income_profile} "
                    f"requires income kind {required_kind}"
                )
            if self.income_profile is IncomeProfile.MIXED and len(kinds) < 2:
                errors.append(
                    f"bundle {bundle.source_bundle_ref!r} for MIXED requires at least "
                    "two distinct income kinds"
                )
            if self.income_profile is IncomeProfile.UNEMPLOYED and bundle.sources:
                errors.append(
                    f"bundle {bundle.source_bundle_ref!r} for UNEMPLOYED must contain no sources"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self


class BehaviorProfileDistribution(ConfigModel):
    """Independent weighted behavioral dimension."""

    behavior_profile: BehaviorProfile
    weight_basis_points: int = Field(gt=0, le=10_000)
    spending_multiplier_basis_points: int = Field(ge=0, le=100_000)
    saving_multiplier_basis_points: int = Field(ge=0, le=100_000)


class WealthProfileDistribution(ConfigModel):
    """Independent weighted opening-wealth dimension."""

    wealth_band: WealthBand
    weight_basis_points: int = Field(gt=0, le=10_000)
    deposit_balance_multiplier_basis_points: int = Field(ge=0, le=100_000)
    investment_balance_multiplier_basis_points: int = Field(ge=0, le=100_000)


class CustomerFactorySettings(ConfigModel):
    """Bounded conditional and independent distributions for one population."""

    income_profiles: list[IncomeProfileDistribution] = Field(min_length=1, max_length=7)
    behavior_profiles: list[BehaviorProfileDistribution] = Field(min_length=1, max_length=3)
    wealth_profiles: list[WealthProfileDistribution] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_weighted_dimensions(self) -> Self:
        errors: list[str] = []

        def validate_axis(
            values: list[IncomeProfileDistribution]
            | list[BehaviorProfileDistribution]
            | list[WealthProfileDistribution],
            labels: list[IncomeProfile] | list[BehaviorProfile] | list[WealthBand],
            name: str,
        ) -> None:
            if len(labels) != len(set(labels)):
                errors.append(f"{name} values must be unique")
            if sum(value.weight_basis_points for value in values) != 10_000:
                errors.append(f"{name} weight_basis_points must sum to 10000")

        validate_axis(
            self.income_profiles,
            [item.income_profile for item in self.income_profiles],
            "income_profiles.income_profile",
        )
        validate_axis(
            self.behavior_profiles,
            [item.behavior_profile for item in self.behavior_profiles],
            "behavior_profiles.behavior_profile",
        )
        validate_axis(
            self.wealth_profiles,
            [item.wealth_band for item in self.wealth_profiles],
            "wealth_profiles.wealth_band",
        )

        bundle_refs = [
            bundle.source_bundle_ref
            for profile in self.income_profiles
            for bundle in profile.source_bundles
        ]
        if len(bundle_refs) != len(set(bundle_refs)):
            errors.append("source_bundle_ref values must be unique across customer_factory")
        if errors:
            raise ValueError("; ".join(errors))
        return self


class ScenarioConfigV3(ConfigModel):
    """Complete validated configuration for contract schema 1.3."""

    schema_version: Literal["1.3"]
    scenario: ScenarioSettings
    customer: CustomerSettingsV1
    customer_factory: CustomerFactorySettings
    institutions: list[InstitutionSettings] = Field(min_length=1, max_length=32)
    accounts: list[AccountSettingsV1] = Field(min_length=1, max_length=32)
    fixed_expenses: list[FixedExpenseRuleV1] = Field(default_factory=list, max_length=64)
    variable_expenses: VariableExpenseRuleV1
    own_transfers: list[OwnTransferRule] = Field(default_factory=list, max_length=32)
    credit_cards: list[CreditCardSettings] = Field(default_factory=list, max_length=32)
    card_purchase_rules: list[CardPurchaseRule] = Field(default_factory=list, max_length=256)
    loans: list[LoanSettings] = Field(default_factory=list, max_length=32)
    investments: list[InvestmentSettings] = Field(default_factory=list, max_length=32)
    investment_contribution_rules: list[InvestmentFlowRule] = Field(
        default_factory=list,
        max_length=256,
    )
    investment_redemption_rules: list[InvestmentFlowRule] = Field(
        default_factory=list,
        max_length=256,
    )

    @model_validator(mode="after")
    def validate_references_uniqueness_and_work(self) -> Self:
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
        loan_refs = [item.loan_ref for item in self.loans]
        investment_refs = [item.investment_ref for item in self.investments]
        contribution_rule_ids = [item.rule_id for item in self.investment_contribution_rules]
        redemption_rule_ids = [item.rule_id for item in self.investment_redemption_rules]

        require_unique(institution_refs, "institutions.institution_ref")
        require_unique(institution_ids, "institutions.institution_id")
        require_unique(account_refs, "accounts.account_ref")
        require_unique(fixed_rule_ids, "fixed_expenses.rule_id")
        require_unique(transfer_rule_ids, "own_transfers.rule_id")
        require_unique(card_refs, "credit_cards.card_ref")
        require_unique(purchase_rule_ids, "card_purchase_rules.rule_id")
        require_unique(loan_refs, "loans.loan_ref")
        require_unique(investment_refs, "investments.investment_ref")
        require_unique(
            contribution_rule_ids + redemption_rule_ids,
            "investment flow rule_id",
        )

        if sum(rule.occurrences for rule in self.card_purchase_rules) > 10_000:
            errors.append("card_purchase_rules may schedule at most 10000 purchase attempts")
        if (
            sum(rule.occurrences * rule.installment_count for rule in self.card_purchase_rules)
            > 250_000
        ):
            errors.append("card_purchase_rules may create at most 250000 installment items")
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

        known_institutions = set(institution_refs)
        accounts_by_ref = {item.account_ref: item for item in self.accounts}
        known_accounts = set(accounts_by_ref)
        known_cards = set(card_refs)
        known_investments = set(investment_refs)

        if self.customer.primary_account_ref not in known_accounts:
            errors.append("customer.primary_account_ref must reference an account")
        elif accounts_by_ref[self.customer.primary_account_ref].account_type != "CHECKING":
            errors.append("customer.primary_account_ref must reference a CHECKING account")

        for account in self.accounts:
            if account.institution_ref not in known_institutions:
                errors.append(
                    f"account {account.account_ref!r} references unknown institution "
                    f"{account.institution_ref!r}"
                )
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

        for profile in self.customer_factory.income_profiles:
            for bundle in profile.source_bundles:
                for source in bundle.sources:
                    if source.destination_account_ref not in known_accounts:
                        errors.append(
                            f"income source {source.source_ref!r} references unknown account "
                            f"{source.destination_account_ref!r}"
                        )

        if errors:
            raise ValueError("; ".join(errors))
        return self


ScenarioConfigV1_3 = ScenarioConfigV3

__all__ = [
    "BehaviorProfileDistribution",
    "CustomerFactorySettings",
    "IncomeProfileDistribution",
    "IncomeSourceBundle",
    "IncomeSourceTemplate",
    "ScenarioConfigV1_3",
    "ScenarioConfigV3",
    "UniformMinorAmount",
    "WealthProfileDistribution",
]
