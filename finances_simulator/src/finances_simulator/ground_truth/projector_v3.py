"""Project schema-1.3 private truth for sampled income-diverse customers."""

from dataclasses import dataclass, replace
from uuid import UUID

from pydantic import BaseModel

from finances_simulator.contracts.ground_truth_v3 import (
    BalanceSheetGroundTruthV3,
    CardTransactionGroundTruthV3,
    CustomerGroundTruthV3,
    CustomerMonthGroundTruthV3,
    IncomeSourceGroundTruthV3,
    InvestmentTransactionGroundTruthV3,
    LoanPaymentGroundTruthV3,
    TransactionGroundTruthV3,
)
from finances_simulator.domain.customer import CustomerTwin, CustomerTwinV3
from finances_simulator.ground_truth.projector_v2 import project_ground_truth_v2
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import deterministic_id


@dataclass(frozen=True, slots=True)
class GroundTruthBundleV3:
    """Complete private schema-1.3 datasets."""

    customers: tuple[CustomerGroundTruthV3, ...]
    customer_months: tuple[CustomerMonthGroundTruthV3, ...]
    transactions: tuple[TransactionGroundTruthV3, ...]
    credit_card_transactions: tuple[CardTransactionGroundTruthV3, ...]
    loan_payments: tuple[LoanPaymentGroundTruthV3, ...]
    investment_transactions: tuple[InvestmentTransactionGroundTruthV3, ...]
    balance_sheets: tuple[BalanceSheetGroundTruthV3, ...]
    income_sources: tuple[IncomeSourceGroundTruthV3, ...]


def _upgrade(record: BaseModel, model: type[BaseModel]) -> BaseModel:
    return model.model_validate(record.model_dump(exclude={"schema_version"}))


def project_ground_truth_v3(
    run: SimulationRun,
    *,
    namespace: UUID,
) -> GroundTruthBundleV3:
    """Build causal labels while keeping sampled dimensions private."""

    twin = run.customer_twin
    if not isinstance(twin, CustomerTwinV3):
        raise TypeError("schema-1.3 projection requires CustomerTwinV3")

    # V2's common product/month projector only needs the legacy customer fields
    # for the customer row. That row is discarded and replaced below.
    projection_twin = CustomerTwin(
        customer_id=twin.customer_id,
        scenario_name=twin.scenario_name,
        currency=twin.currency,
        true_monthly_salary_minor=1,
        income_source_id=(
            twin.income_sources[0].income_source_id
            if twin.income_sources
            else deterministic_id(namespace, "income_source", "projection-placeholder")
        ),
        primary_account=twin.primary_account,
        additional_accounts=twin.additional_accounts,
    )
    base = project_ground_truth_v2(
        replace(run, customer_twin=projection_twin),
        namespace=namespace,
    )
    customer = CustomerGroundTruthV3(
        customer_id=twin.customer_id,
        scenario_name=twin.scenario_name,
        currency=twin.currency,
        income_profile=twin.income_profile,
        source_bundle_ref=twin.source_bundle_ref,
        behavior_profile=twin.behavior_profile,
        wealth_band=twin.wealth_band,
        spending_multiplier_basis_points=twin.spending_multiplier_basis_points,
        saving_multiplier_basis_points=twin.saving_multiplier_basis_points,
        deposit_balance_multiplier_basis_points=(twin.deposit_balance_multiplier_basis_points),
        investment_balance_multiplier_basis_points=(
            twin.investment_balance_multiplier_basis_points
        ),
        income_source_ids=tuple(source.income_source_id for source in twin.income_sources),
        primary_account_id=twin.primary_account.account_id,
        opening_balance_minor=twin.primary_account.opening_balance_minor,
        account_ids=tuple(account.account_id for account in twin.accounts),
        card_ids=tuple(card.card_id for card in run.cards),
        loan_ids=tuple(loan.loan_id for loan in run.loans),
        investment_ids=tuple(investment.investment_id for investment in run.investments),
        total_opening_deposit_balance_minor=sum(
            account.opening_balance_minor for account in twin.accounts
        ),
        total_opening_investment_balance_minor=sum(
            investment.opening_balance_minor for investment in run.investments
        ),
        total_opening_loan_principal_minor=sum(
            loan.principal_minor for loan in run.loans if loan.originated_at < run.start_date
        ),
    )
    income_sources = tuple(
        IncomeSourceGroundTruthV3.model_validate(source.model_dump())
        for source in sorted(
            twin.income_sources,
            key=lambda item: item.income_source_id,
        )
    )
    return GroundTruthBundleV3(
        customers=(customer,),
        customer_months=tuple(
            _upgrade(item, CustomerMonthGroundTruthV3) for item in base.customer_months
        ),
        transactions=tuple(_upgrade(item, TransactionGroundTruthV3) for item in base.transactions),
        credit_card_transactions=tuple(
            _upgrade(item, CardTransactionGroundTruthV3) for item in base.credit_card_transactions
        ),
        loan_payments=tuple(
            _upgrade(item, LoanPaymentGroundTruthV3) for item in base.loan_payments
        ),
        investment_transactions=tuple(
            _upgrade(item, InvestmentTransactionGroundTruthV3)
            for item in base.investment_transactions
        ),
        balance_sheets=tuple(
            _upgrade(item, BalanceSheetGroundTruthV3) for item in base.balance_sheets
        ),
        income_sources=income_sources,
    )


__all__ = ["GroundTruthBundleV3", "project_ground_truth_v3"]
