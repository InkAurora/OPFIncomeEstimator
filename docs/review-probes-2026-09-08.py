"""Reproduce review findings with synthetic inputs, without changing runtime or model files.

Frozen record of the state at `4ee69ca`. It is not expected to run against later commits:
every defect it demonstrates has since been fixed, so the probes now raise or return the
corrected answer, and it still names the `production-0.11.0` bundle. The live regression
tests for these findings are `estimator/tests/test_review_regressions.py`.

Run from the repository root after installing estimator and simulator development dependencies:
    python docs/review-probes-2026-09-08.py

The JSON output records observed behavior, not desired regression-test expectations.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "estimator"))

from income_estimator import ProductionIncomeEstimator
from income_estimator.contracts.v1 import validate_estimator_input
from income_estimator.features import build_customer_month_features
from income_estimator.pipeline import RecurringIncomeEstimator


def transaction(identifier: str, month: int = 1, **changes: object) -> dict:
    result = {
        "transaction_id": identifier,
        "customer_id": "review-customer",
        "account_id": "checking",
        "posted_at": f"2026-{month:02d}-05",
        "observed_at": f"2026-{month:02d}-05",
        "direction": "CREDIT",
        "amount_minor": 500_000,
        "currency": "BRL",
        "description": "SALARY",
    }
    result.update(changes)
    return result


def request(transactions: list[dict], months: int = 1) -> dict:
    from calendar import monthrange

    return {
        "schema_version": "1.0",
        "source_contract_schema_version": "1.6",
        "run_id": "review-synthetic",
        "customer_id": "review-customer",
        "currency": "BRL",
        "window_start": "2026-01-01",
        "window_end": f"2026-{months:02d}-{monthrange(2026, months)[1]}",
        "months": months,
        "accounts": [
            {
                "customer_id": "review-customer",
                "account_id": account,
                "institution_id": f"bank-{account}",
                "currency": "BRL",
            }
            for account in ("checking", "savings")
        ],
        "transactions": transactions,
        "coverage": [],
    }


def coverage(eligible: int, observed: int) -> list[dict]:
    return [
        {
            "customer_id": "review-customer",
            "account_id": "checking",
            "configured_coverage_percent": 90,
            "eligible_record_count": eligible,
            "observed_original_record_count": observed,
            "effective_coverage_basis_points": round(observed * 10_000 / eligible),
        }
    ]


def main() -> None:
    estimator = RecurringIncomeEstimator()
    results: dict[str, object] = {}
    classifications = {}
    for description in (
        "SALARY",
        "SALÁRIO",
        "FOLHA DE PAGAMENTO",
        "PIX RECEBIDO EMPREGADOR",
    ):
        audit = estimator.explain(
            request([transaction("salary", description=description)])
        )
        decision = audit.transaction_decisions[0]
        classifications[description] = {
            "income_minor": audit.estimate.monthly_estimates[0].estimated_income_minor,
            "classification": decision.classification,
            "reasons": decision.reason_codes,
        }
    results["description_sensitivity"] = classifications

    paired = request(
        [
            transaction("salary"),
            transaction(
                "rent", direction="DEBIT", account_id="savings", description="RENT"
            ),
        ]
    )
    audit = estimator.explain(paired)
    results["unrelated_equal_debit"] = {
        "income_minor": audit.estimate.monthly_estimates[0].estimated_income_minor,
        "salary_reasons": next(
            x.reason_codes
            for x in audit.transaction_decisions
            if x.transaction_id == "salary"
        ),
    }

    quarterly = request(
        [
            transaction(
                f"quarter-{month}",
                month,
                amount_minor=900_000,
                description="PROFIT DISTRIBUTION",
            )
            for month in (1, 4, 7)
        ],
        months=7,
    )
    quarterly["coverage"] = coverage(100, 90)
    audit = estimator.explain(quarterly)
    results["quarterly_gap_imputation"] = {
        "observed_total_minor": 2_700_000,
        "estimated_total_minor": sum(
            x.estimated_income_minor for x in audit.estimate.monthly_estimates
        ),
        "stream_frequency": audit.income_streams[0].frequency,
        "monthly": [
            {
                "month": x.month,
                "observed_minor": x.observed_income_minor,
                "imputed_minor": x.imputed_income_minor,
            }
            for x in audit.monthly_reconstructions
        ],
    }

    mismatched = request([transaction("salary")])
    mismatched["months"] = 2
    production = ProductionIncomeEstimator.from_bundle(
        ROOT / "estimator/bundles/production-0.11.0"
    )
    estimate = production.estimate_production(mismatched).estimate
    results["mismatched_window"] = {
        "window_end": mismatched["window_end"],
        "returned_months": [x.month for x in estimate.monthly_estimates],
        "sustainable_points_minor": [
            x.sustainable_income_p50_minor for x in estimate.monthly_estimates
        ],
    }

    empty = production.estimate_production(
        request([], months=12)
    ).estimate.monthly_estimates[-1]
    results["empty_history"] = {
        "sustainable_income_minor": empty.sustainable_income_p50_minor,
        "confidence_basis_points": empty.confidence_score_basis_points,
        "interval_unavailable_reason": empty.quantile_unavailable_reason,
    }

    compact = request(
        [transaction("salary", posted_at="20260105", observed_at="20260105")]
    )
    results["noncanonical_dates"] = {
        "accepted_by_validator": bool(validate_estimator_input(compact)),
        "income_minor": estimator.estimate(compact)
        .monthly_estimates[0]
        .estimated_income_minor,
    }

    prefix = request(
        [transaction(f"salary-{month}", month) for month in (1, 3, 4)], months=4
    )
    before = copy.deepcopy(prefix)
    after = copy.deepcopy(prefix)
    before["coverage"] = coverage(10, 10)
    after["coverage"] = coverage(20, 10)
    january_before = build_customer_month_features(before).rows[0].to_mapping()
    january_after = build_customer_month_features(after).rows[0].to_mapping()
    results["undated_coverage_changes_historical_features"] = {
        key: {"before": january_before[key], "after": january_after[key]}
        for key in january_before
        if january_before[key] != january_after[key]
    }

    from release import build_bundle

    original_report = build_bundle.RELEASE_LOCKBOX_REPORT_SOURCE
    with tempfile.TemporaryDirectory(prefix="income-review-") as temporary:
        directory = Path(temporary)
        failed_report = directory / "failed-release-report.json"
        report = json.loads(original_report.read_text(encoding="utf-8"))
        report["status"] = "RELEASE_REJECTED"
        report["failures"] = ["Synthetic review probe: release gate failed."]
        report["artifact_sha256"] = "0" * 64
        failed_report.write_text(
            json.dumps(report) + "\n", encoding="utf-8", newline="\n"
        )
        build_bundle.RELEASE_LOCKBOX_REPORT_SOURCE = failed_report
        try:
            destination = directory / "new-bundle"
            build_bundle.build_bundle(
                destination,
                bundle_id="review-only",
                bundle_version="0.11.0",
                package_version="0.11.0",
            )
            loaded = ProductionIncomeEstimator.from_bundle(destination)
            results["rejected_release_evidence"] = {
                "builder_accepted": True,
                "loader_accepted": loaded.bundle_digest is not None,
                "reported_status": report["status"],
                "reported_artifact_digest": report["artifact_sha256"],
            }
        finally:
            build_bundle.RELEASE_LOCKBOX_REPORT_SOURCE = original_report

    print(json.dumps(results, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
