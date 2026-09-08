"""Conservative rule-based income classifier with stable reason codes."""

from __future__ import annotations

from dataclasses import dataclass

from income_estimator.contracts.audit import TransactionDecision
from income_estimator.transaction_intelligence.features import TransactionFeatures


@dataclass(frozen=True, slots=True)
class RuleConfig:
    """Keyword evidence, matched against accent-folded uppercase description text.

    The English terms below are the simulator's vocabulary. They are not the vocabulary of a
    Brazilian bank feed, and until now they were the only vocabulary: changing one credit's
    description from ``SALARY`` to ``SALARIO`` moved R$5,000 of counted income to zero, because
    nothing else could establish a payment as income. The Portuguese terms are the reviewed payroll,
    pension and professional-fee cases a real feed carries, plus the Portuguese counterparts of the
    exclusions, so that ``ESTORNO SALARIO`` is refused for the same reason ``REVERSAL SALARY`` is.

    ``normalize_description`` strips combining marks, so ``SALÁRIO`` and ``PENSÃO`` are matched by
    their unaccented forms and only those forms are listed. Terms whose Brazilian meaning is
    genuinely ambiguous are deliberately absent: ``VENCIMENTO`` singular is a due date, ``PIX
    RECEBIDO`` names a rail rather than a source, and neither establishes income. Recognizing a word
    is evidence about a receipt, never proof that a receipt is earnings.
    """

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
        "TRANSFERENCIA ENTRE CONTAS",
        "TRANSFERENCIA PROPRIA",
        "CONTA PROPRIA",
        "ESTORNO",
        "DEVOLUCAO",
        "LIBERACAO DE EMPRESTIMO",
        "CREDITO CONSIGNADO",
        "RESGATE",
        "HERANCA",
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
        "SALARIO",
        "FOLHA DE PAGAMENTO",
        "PRO LABORE",
        "PROVENTOS",
        "REMUNERACAO",
        "APOSENTADORIA",
        "PENSAO",
        "VENCIMENTOS",
        "HONORARIOS",
    )
    supporting_income_keywords: tuple[str, ...] = (
        "BONUS",
        "COMMISSION",
        "DIVIDEND",
        "RENTAL INCOME",
        "BENEFIT",
        "DECIMO TERCEIRO",
        "FERIAS",
        "COMISSAO",
        "BONIFICACAO",
        "PARTICIPACAO NOS LUCROS",
        "BENEFICIO",
        "INSS",
        "DIVIDENDOS",
        "ALUGUEL RECEBIDO",
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
        if features.has_linked_own_transfer_pair:
            return self._decision(features, "EXCLUDED", 0, "LINKED_OWN_TRANSFER_PAIR")

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

        # An equal-amount debit on another consented account on the same day is what an own
        # transfer looks like, and it is also what an unrelated rent payment looks like. It settles
        # a credit that carries no income evidence of its own. Against an established payroll credit
        # it does not: deleting income the description supports costs more than counting a transfer
        # nothing else identifies, and the coincidence is recorded on the decision either way.
        if features.has_unlinked_same_day_amount_debit:
            return self._decision(
                features, "EXCLUDED", 0, "UNLINKED_EQUAL_DEBIT_NO_INCOME_EVIDENCE"
            )

        return self._decision(features, "AMBIGUOUS", 2_500, "UNRECOGNIZED_CREDIT")

    @staticmethod
    def _decision(
        features: TransactionFeatures,
        classification: str,
        probability: int,
        reason: str,
    ) -> TransactionDecision:
        item = features.transaction
        reason_codes = (reason,)
        # A corrected re-post is decided on its own merits like any other credit. The lineage is
        # recorded so an auditor can see that a counted amount repairs a reversed original rather
        # than adding a second, independent payment.
        if getattr(item.source, "repost_of_transaction_id", None) is not None:
            reason_codes = (*reason_codes, "CORRECTED_REPOST")
        # Income counted despite an unexplained matching debit stays visible as such, so a reviewer
        # sees the pairing the classifier declined to treat as a transfer.
        if classification == "INCOME" and features.has_unlinked_same_day_amount_debit:
            reason_codes = (*reason_codes, "SAME_DAY_EQUAL_DEBIT_UNLINKED")
        return TransactionDecision(
            transaction_id=item.source.transaction_id,
            posted_month=item.posted_month,
            direction=item.source.direction,
            amount_minor=item.source.amount_minor,
            normalized_description=item.normalized_description,
            counterparty_cluster=item.counterparty_cluster,
            classification=classification,
            income_probability_basis_points=probability,
            reason_codes=reason_codes,
        )


__all__ = ["IncomeRuleClassifier", "RuleConfig"]
