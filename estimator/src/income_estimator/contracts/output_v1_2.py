"""Estimator output contract 1.2: what the evidence supports, apart from what the model said.

Contract `1.1` publishes a number for every month it is asked about. It publishes one for a request
with two accounts and no transactions at all: R$4,319.65 of sustainable income, confidence zero, no
interval. Nothing in the record says that amount rests on nothing, because the record has no way to
say it. A consumer reading `sustainable_income_p50_minor` gets the same field, in the same place,
whether the month is a well-observed salaried year or an empty consent scope.

Contract `1.2` adds the missing distinction and nothing else.

`assessment_status` states whether this month's evidence supports using the number at all.
`SUPPORTED` means the estimate rests on established income inside the conditions the calibration was
measured on. `REVIEW_REQUIRED` means an estimate exists but something about the evidence needs a
person: an interval that could not be published, or credits arriving that nothing classified.
`INSUFFICIENT_EVIDENCE` means there is nothing here to qualify.

`qualified_sustainable_income_minor` is the only field a policy may read, and it is present only
when the status is `SUPPORTED`. The research estimate stays visible beside it in
`sustainable_income_point_minor`, so nothing is hidden from evaluation; it simply stops being
mistaken for an amount a bank may lend against.

`sustainable_income_point_minor` is named for what it is. `sustainable_income_p50_minor` carries the
same value and is retained so a `1.1` reader is unaffected, but the value is a routed point
prediction from a model fitted on squared error over a log target. Nothing has measured it as a
conditional median, and a field named `p50` claims that it has.

`confidence_score_basis_points` is inherited unchanged and remains a measure of evidence quality.
It is not a probability that the amount is correct, and the stress suites show it does not track
interval coverage: the noisy suite averages 74% confidence while its intervals cover 35%.

`IncomeEstimateV12.to_v1_1` is the explicit downgrade for consumers that have not moved.

What `SUPPORTED` does not yet mean. The in-support test behind it is the calibration's existing
support envelope, which checks nine features one range at a time and accepts missing values. The
held-out stress suites show that envelope is too permissive: the high-volatility suite publishes
intervals on 234 of 240 rows and covers 15.8% of them against a nominal 80%, and every demo month in
that profile currently assesses as `SUPPORTED`. The status layer is the mechanism for withholding an
amount; the measurement that should make it withhold more is not done. Until a joint support test is
fitted and evaluated, `SUPPORTED` means "established income, inside the envelope as it stands", and
that is weaker than "this interval is reliable".
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from income_estimator.contracts.output_v1_1 import (
    IncomeEstimateV11,
    MonthlyIncomeEstimateV11,
)

ESTIMATOR_OUTPUT_CONTRACT_VERSION_1_2 = "1.2"

# What this package emits. A bundle manifest declares the version it was assembled against, and the
# loader refuses a mismatch, so moving this constant is a deliberate release event rather than a
# detail: the bundle has to be rebuilt to agree with it.
ESTIMATOR_OUTPUT_CONTRACT_VERSION = ESTIMATOR_OUTPUT_CONTRACT_VERSION_1_2

ASSESSMENT_SUPPORTED = "SUPPORTED"
ASSESSMENT_REVIEW_REQUIRED = "REVIEW_REQUIRED"
ASSESSMENT_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
ASSESSMENT_STATUSES = (
    ASSESSMENT_SUPPORTED,
    ASSESSMENT_REVIEW_REQUIRED,
    ASSESSMENT_INSUFFICIENT_EVIDENCE,
)


class MonthlyIncomeEstimateV12(MonthlyIncomeEstimateV11):
    """One month, with the standing of its own evidence stated on the record."""

    schema_version: Literal["1.2"] = "1.2"
    assessment_status: Literal["SUPPORTED", "REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE"]
    assessment_reason_codes: tuple[str, ...] = Field(min_length=1)
    qualified_sustainable_income_minor: int | None = Field(default=None, ge=0)
    sustainable_income_point_minor: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        if self.sustainable_income_point_minor != self.sustainable_income_p50_minor:
            raise ValueError(
                "sustainable_income_point_minor and the retained sustainable_income_p50_minor "
                "must carry the same value; they are one estimate under two names"
            )
        qualified = self.qualified_sustainable_income_minor
        supported = self.assessment_status == ASSESSMENT_SUPPORTED
        # The biconditional is the whole point of the contract. A qualified amount that could appear
        # beside any other status would be exactly the field `1.1` already had.
        if supported and qualified is None:
            raise ValueError(
                "a SUPPORTED month must publish qualified_sustainable_income_minor"
            )
        if not supported and qualified is not None:
            raise ValueError(
                f"qualified_sustainable_income_minor is published only for {ASSESSMENT_SUPPORTED} "
                f"months, not {self.assessment_status}"
            )
        if qualified is not None and qualified != self.sustainable_income_point_minor:
            raise ValueError(
                "qualified_sustainable_income_minor must be the point estimate it qualifies"
            )
        if len(self.assessment_reason_codes) != len(set(self.assessment_reason_codes)):
            raise ValueError("assessment_reason_codes must be unique")
        return self

    def to_v1_1(self) -> MonthlyIncomeEstimateV11:
        """Drop to `1.1`, losing the standing of the evidence and keeping the number."""

        payload = self.model_dump(mode="python")
        for field in (
            "schema_version",
            "assessment_status",
            "assessment_reason_codes",
            "qualified_sustainable_income_minor",
            "sustainable_income_point_minor",
        ):
            payload.pop(field, None)
        return MonthlyIncomeEstimateV11.model_validate(payload)


class IncomeEstimateV12(IncomeEstimateV11):
    """Versioned envelope whose months each carry an assessment status."""

    schema_version: Literal["1.2"] = "1.2"
    monthly_estimates: tuple[MonthlyIncomeEstimateV12, ...]

    def to_v1_1(self) -> IncomeEstimateV11:
        """The explicit adapter for consumers still reading `1.1`.

        A `1.1` reader that received a `1.2` record would see a qualified amount it does not know to
        check and an assessment status it would ignore, which is worse than not seeing them. This
        conversion is deliberate and lossy, and the loss is stated: after it, nothing distinguishes
        an amount a policy may use from one it may not.
        """

        payload = self.model_dump(mode="python")
        payload.pop("schema_version", None)
        payload["monthly_estimates"] = [
            item.to_v1_1().model_dump(mode="python") for item in self.monthly_estimates
        ]
        return IncomeEstimateV11.model_validate(payload)


__all__ = [
    "ASSESSMENT_INSUFFICIENT_EVIDENCE",
    "ASSESSMENT_REVIEW_REQUIRED",
    "ASSESSMENT_STATUSES",
    "ASSESSMENT_SUPPORTED",
    "ESTIMATOR_OUTPUT_CONTRACT_VERSION",
    "ESTIMATOR_OUTPUT_CONTRACT_VERSION_1_2",
    "IncomeEstimateV12",
    "MonthlyIncomeEstimateV12",
]
