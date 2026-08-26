"""The downloadable evidence bundle.

One JSON document that carries enough to reproduce and to audit the run: which profile and seed
produced it, which artifact versions answered, what the estimator said, why it said it, and how far
from the private truth it landed. The truth block is nested under its own key and labelled, so a
reader cannot mistake it for something the estimator was given.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from demo_app.service import DemoResult

EVIDENCE_SCHEMA_VERSION = "1.0"

TRUTH_DISCLAIMER = (
    "Simulator ground truth. Projected from the hidden simulation after inference finished and "
    "used for evaluation only. It was never part of the estimator request."
)


def build_evidence(result: DemoResult) -> dict[str, Any]:
    """Assemble the evidence document for one run."""

    covered, published = result.interval_coverage
    return {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "synthetic_data": True,
        "run": {
            "profile_key": result.profile.key,
            "profile_label": result.profile.label,
            "scenario_file": result.profile.scenario_file,
            "scenario_contract_schema_version": result.scenario_schema_version,
            "seed": result.seed,
            "months": result.months,
            "run_id": result.run_id,
            "customer_id": result.customer_id,
            "currency": result.currency,
            "generation_seconds": round(result.generation_seconds, 4),
            "inference_seconds": round(result.inference_seconds, 4),
            "total_seconds": round(result.total_seconds, 4),
        },
        "artifact_versions": {
            "estimator_version": result.estimator_version,
            "feature_version": result.feature_version,
            "input_contract_version": result.input_contract_version,
            "output_contract_version": result.output_contract_version,
            "explanation_contract_version": result.explanation.schema_version,
            "model_versions": list(result.model_versions),
            "component_versions": list(result.component_versions),
        },
        "estimator_request_record_counts": dict(result.request_record_counts),
        "estimate": result.estimate.model_dump(mode="json"),
        "explanation": result.explanation.model_dump(mode="json"),
        "data_quality": {
            "coverage_is_declared_by_scenario": result.data_quality.coverage_is_declared,
            "overall_coverage_basis_points": result.data_quality.overall_coverage_basis_points,
            "per_account_coverage": [
                {
                    "account_id": row.account_id,
                    "configured_coverage_percent": row.configured_coverage_percent,
                    "eligible_record_count": row.eligible_record_count,
                    "observed_record_count": row.observed_record_count,
                    "effective_coverage_basis_points": row.effective_coverage_basis_points,
                }
                for row in result.data_quality.coverage_rows
            ],
            "observed_transaction_count": result.data_quality.observed_transaction_count,
            "duplicate_count": result.data_quality.duplicate_count,
            "reversal_count": result.data_quality.reversal_count,
            "repost_count": result.data_quality.repost_count,
            "late_arrival_count": result.data_quality.late_arrival_count,
            "max_late_arrival_days": result.data_quality.max_late_arrival_days,
            "months_with_interval": result.data_quality.months_with_interval,
            "months_abstained": result.data_quality.months_abstained,
            "abstention_reasons": list(result.data_quality.abstention_reasons),
        },
        "observed_net_position": [
            {
                "month": row.month,
                "account_balance_minor": row.account_balance_minor,
                "investment_balance_minor": row.investment_balance_minor,
                "debt_minor": row.debt_minor,
                "net_position_minor": row.net_position_minor,
            }
            for row in result.balance_rows
        ],
        "private_truth_comparison": {
            "disclaimer": TRUTH_DISCLAIMER,
            "monthly": [
                {
                    "month": row.month,
                    "truth_realized_income_minor": row.truth_realized_minor,
                    "truth_sustainable_income_minor": row.truth_sustainable_minor,
                    "truth_active_source_count": row.truth_active_source_count,
                    "estimated_realized_income_minor": row.realized_estimate_minor,
                    "estimated_sustainable_income_minor": row.sustainable_p50_minor,
                    "sustainable_p10_minor": row.sustainable_p10_minor,
                    "sustainable_p90_minor": row.sustainable_p90_minor,
                    "quantile_unavailable_reason": row.quantile_unavailable_reason,
                    "realized_error_minor": row.realized_error_minor,
                    "realized_error_percent": _rounded(row.realized_error_percent),
                    "sustainable_error_minor": row.sustainable_error_minor,
                    "sustainable_error_percent": _rounded(row.sustainable_error_percent),
                    "interval_contains_truth": row.interval_contains_truth,
                }
                for row in result.month_rows
            ],
            "life_events": [
                {
                    "event_type": event.event_type,
                    "effective_date": event.effective_date,
                    "annualized_income_before_minor": event.annualized_income_before_minor,
                    "annualized_income_after_minor": event.annualized_income_after_minor,
                }
                for event in result.life_events
            ],
        },
        "summary_metrics": {
            "months_estimated": len(result.month_rows),
            "realized_mean_absolute_percent_error": _rounded(
                result.realized_mean_absolute_percent_error
            ),
            "sustainable_mean_absolute_percent_error": _rounded(
                result.sustainable_mean_absolute_percent_error
            ),
            "interval_months_published": published,
            "interval_months_containing_truth": covered,
            "interval_empirical_coverage": _rounded(covered / published if published else None, 4),
            "nominal_interval_coverage": 0.80,
            "latest_month": result.latest.month,
            "latest_realized_error_percent": _rounded(result.latest.realized_error_percent),
            "latest_sustainable_error_percent": _rounded(result.latest.sustainable_error_percent),
        },
    }


def _rounded(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def evidence_filename(result: DemoResult) -> str:
    """A filename that says what the bundle is, and reproduces the run."""

    return f"evidence_{result.profile.key}_seed{result.seed}_{result.months}m.json"


def evidence_json(result: DemoResult) -> str:
    """Serialize the evidence bundle."""

    return json.dumps(build_evidence(result), indent=2, ensure_ascii=False, sort_keys=False)


__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "TRUTH_DISCLAIMER",
    "build_evidence",
    "evidence_filename",
    "evidence_json",
]
