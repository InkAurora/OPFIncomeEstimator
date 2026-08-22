"""Conservative rule-based income classifier with stable reason codes."""

from __future__ import annotations

from dataclasses import dataclass

from income_estimator.contracts.audit import TransactionDecision
from income_estimator.transaction_intelligence.features import TransactionFeatures


@dataclass(frozen=True, slots=True)
class RuleConfig:
    exclusion_keywords: tuple[str, ...] = (
        "TRANSFER FROM",
        "OWN TRANSFER",
        "LOAN DISBURSEMENT",
        "INVESTMENT REDEMPTION",
        "REFUND",
        "REVERSAL",
        "ESTATE DISTRIBUTION",
        "INHERITANCE",
        "SALE PROCEEDS",
        "CASH ADVANCE",
    )
    strong_income_keywords: tuple[str, ...] = (
        "SALARY",
        "PAYROLL",
        "WAGE",
        "PENSION",
        "SERVICE RECEIPT",
        "SERVICE PAYMENT",
        "PROFIT DISTRIBUTION",
        "CASH DISTRIBUTION",
    )
    supporting_income_keywords: tuple[str, ...] = (
        "BONUS",
        "COMMISSION",
        "DIVIDEND",
        "RENTAL INCOME",
        "BENEFIT",
    )


class IncomeRuleClassifier:
    """Apply precedence-ordered rules; observed product links beat descriptions."""

    def __init__(self, config: RuleConfig | None = None) -> None:
        self.config = config or RuleConfig()

    def classify(self, features: TransactionFeatures) -> TransactionDecision:
        item = features.transaction
        transaction = item.source
        description = item.normalized_description

        if not item.available_at_cutoff:
            return self._decision(features, "EXCLUDED", 0, "OBSERVED_AFTER_CUTOFF")
        if not item.inside_window:
            return self._decision(features, "EXCLUDED", 0, "OUTSIDE_ESTIMATION_WINDOW")
        if transaction.direction != "CREDIT":
            return self._decision(features, "EXCLUDED", 0, "DEBIT_NOT_INCOME")
        if transaction.duplicate_of_transaction_id is not None:
            return self._decision(features, "EXCLUDED", 0, "DUPLICATE_OBSERVATION")
        if transaction.reversal_of_transaction_id is not None:
            return self._decision(features, "EXCLUDED", 0, "REVERSAL_OBSERVATION")
        if features.is_reversed_original:
            return self._decision(features, "EXCLUDED", 0, "REVERSED_ORIGINAL")
        if features.is_known_loan_disbursement:
            return self._decision(features, "EXCLUDED", 0, "LOAN_DISBURSEMENT_LINK")
        if features.is_known_investment_redemption:
            return self._decision(features, "EXCLUDED", 0, "INVESTMENT_REDEMPTION_LINK")
        if features.has_visible_own_transfer_pair:
            return self._decision(features, "EXCLUDED", 0, "VISIBLE_OWN_TRANSFER_PAIR")

        exclusion = next(
            (keyword for keyword in self.config.exclusion_keywords if keyword in description),
            None,
        )
        if exclusion is not None:
            reason = f"EXCLUDED_DESCRIPTION_{exclusion.replace(' ', '_')}"
            return self._decision(features, "EXCLUDED", 0, reason)

        strong = next(
            (keyword for keyword in self.config.strong_income_keywords if keyword in description),
            None,
        )
        if strong is not None:
            reason = f"STRONG_INCOME_DESCRIPTION_{strong.replace(' ', '_')}"
            return self._decision(features, "INCOME", 9_500, reason)

        supporting = next(
            (
                keyword
                for keyword in self.config.supporting_income_keywords
                if keyword in description
            ),
            None,
        )
        if supporting is not None:
            reason = f"SUPPORTING_INCOME_DESCRIPTION_{supporting.replace(' ', '_')}"
            return self._decision(features, "INCOME", 8_000, reason)

        return self._decision(features, "AMBIGUOUS", 2_500, "UNRECOGNIZED_CREDIT")

    @staticmethod
    def _decision(
        features: TransactionFeatures,
        classification: str,
        probability: int,
        reason: str,
    ) -> TransactionDecision:
        item = features.transaction
        return TransactionDecision(
            transaction_id=item.source.transaction_id,
            posted_month=item.posted_month,
            direction=item.source.direction,
            amount_minor=item.source.amount_minor,
            normalized_description=item.normalized_description,
            classification=classification,
            income_probability_basis_points=probability,
            reason_codes=(reason,),
        )


__all__ = ["IncomeRuleClassifier", "RuleConfig"]
