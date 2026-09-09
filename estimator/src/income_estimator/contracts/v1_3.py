"""Estimator input contract 1.3: consent scope replaces provider coverage counts.

Contracts `1.0` through `1.2` carried a `coverage` record per account holding
`eligible_record_count` and `observed_original_record_count`. Those counts describe records the
receiver never fetched, so no receiver of Open Finance data can produce them; only the simulator,
which generated and then withheld the records, could. Dividing observed income by their ratio
recovered hidden income exactly, and everything downstream of that division measured the generator
rather than an estimator. Contract `1.3` forbids the field.

What replaces it is what a receiver does know: for each consented account, the date range it
actually fetched and whether pagination completed. A month outside that range is a known gap in the
receiver's own evidence. A month inside it is observed. Nothing here says how much the customer
holds at institutions outside the consent, and nothing downstream may scale income upward to guess
at it.

The consent scope is required, exactly one record per declared account, because an absent scope
would be indistinguishable from a complete one. A receiver that fetched in several sessions
declares the contiguous range it holds; session-level gaps are deferred to the provider adapter.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from income_estimator.contracts.v1 import EstimatorContractModel, _parse_date
from income_estimator.contracts.v1_2 import (
    EstimatorAccountV12,
    EstimatorBalanceV12,
    EstimatorCardInvoiceV12,
    EstimatorCardTransactionV12,
    EstimatorCreditCardV12,
    EstimatorCreditLimitV12,
    EstimatorInputV12,
    EstimatorInvestmentBalanceV12,
    EstimatorInvestmentTransactionV12,
    EstimatorInvestmentV12,
    EstimatorLoanBalanceV12,
    EstimatorLoanPaymentV12,
    EstimatorLoanV12,
    EstimatorTransactionV12,
)

ESTIMATOR_INPUT_CONTRACT_VERSION_1_3 = "1.3"


class EstimatorAccountV13(EstimatorAccountV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorTransactionV13(EstimatorTransactionV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorLoanV13(EstimatorLoanV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorInvestmentTransactionV13(EstimatorInvestmentTransactionV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorBalanceV13(EstimatorBalanceV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorCreditCardV13(EstimatorCreditCardV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorCreditLimitV13(EstimatorCreditLimitV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorCardTransactionV13(EstimatorCardTransactionV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorCardInvoiceV13(EstimatorCardInvoiceV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorLoanPaymentV13(EstimatorLoanPaymentV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorLoanBalanceV13(EstimatorLoanBalanceV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorInvestmentV13(EstimatorInvestmentV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorInvestmentBalanceV13(EstimatorInvestmentBalanceV12):
    schema_version: Literal["1.3"] = "1.3"


class EstimatorConsentScopeV13(EstimatorContractModel):
    """What the receiver fetched for one consented account.

    ``fetched_from`` and ``fetched_through`` bound the transaction history the receiver actually
    requested and received; ``pagination_complete`` is false when the provider returned fewer pages
    than it advertised. Every one of these is written by the receiver from its own request log.
    """

    schema_version: Literal["1.3"] = "1.3"
    customer_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    fetched_from: str
    fetched_through: str
    pagination_complete: bool = True

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        start = _parse_date(self.fetched_from, "fetched_from")
        end = _parse_date(self.fetched_through, "fetched_through")
        if end < start:
            raise ValueError("fetched_through must not precede fetched_from")
        return self


class EstimatorInputV13(EstimatorInputV12):
    """Contract 1.2 with the coverage oracle removed and receiver consent scope required."""

    schema_version: Literal["1.3"] = "1.3"
    accounts: tuple[EstimatorAccountV13, ...]
    transactions: tuple[EstimatorTransactionV13, ...]
    loans: tuple[EstimatorLoanV13, ...] = ()
    investment_transactions: tuple[EstimatorInvestmentTransactionV13, ...] = ()
    balances: tuple[EstimatorBalanceV13, ...] = ()
    credit_cards: tuple[EstimatorCreditCardV13, ...] = ()
    credit_limits: tuple[EstimatorCreditLimitV13, ...] = ()
    card_transactions: tuple[EstimatorCardTransactionV13, ...] = ()
    card_invoices: tuple[EstimatorCardInvoiceV13, ...] = ()
    loan_payments: tuple[EstimatorLoanPaymentV13, ...] = ()
    loan_balances: tuple[EstimatorLoanBalanceV13, ...] = ()
    investments: tuple[EstimatorInvestmentV13, ...] = ()
    investment_balances: tuple[EstimatorInvestmentBalanceV13, ...] = ()
    # Typed as an always-empty tuple so a payload carrying the oracle fails validation with a clear
    # message instead of being silently accepted and ignored.
    coverage: tuple[()] = ()
    consent_scopes: tuple[EstimatorConsentScopeV13, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_consent_scopes(self) -> Self:
        if any(record.customer_id != self.customer_id for record in self.consent_scopes):
            raise ValueError("all consent scopes must belong to customer_id")
        account_ids = {record.account_id for record in self.accounts}
        scoped = [record.account_id for record in self.consent_scopes]
        if len(scoped) != len(set(scoped)):
            raise ValueError("consent_scopes must declare each account at most once")
        if set(scoped) != account_ids:
            raise ValueError("consent_scopes must declare exactly the accounts in the request")
        window_start = self.window_start
        window_end = self.window_end
        for record in self.consent_scopes:
            if record.fetched_through < window_start or record.fetched_from > window_end:
                raise ValueError(
                    f"consent scope for account {record.account_id} lies entirely outside the "
                    f"observation window {window_start}...{window_end}"
                )
        return self


__all__ = [
    "ESTIMATOR_INPUT_CONTRACT_VERSION_1_3",
    "EstimatorAccountV13",
    "EstimatorBalanceV13",
    "EstimatorCardInvoiceV13",
    "EstimatorCardTransactionV13",
    "EstimatorConsentScopeV13",
    "EstimatorCreditCardV13",
    "EstimatorCreditLimitV13",
    "EstimatorInputV13",
    "EstimatorInvestmentBalanceV13",
    "EstimatorInvestmentTransactionV13",
    "EstimatorInvestmentV13",
    "EstimatorLoanBalanceV13",
    "EstimatorLoanPaymentV13",
    "EstimatorLoanV13",
    "EstimatorTransactionV13",
]
