"""Record what the bundled estimator answers, so a later change has to admit it changed something.

Version strings agree across a great many edits that move numbers. These fixtures are the other
half of the release gate: the exact monthly output of the promoted bundle on two fixed requests,
committed and compared in ``tests/test_release.py``.

Two requests on purpose. The minimal one is hand-built, needs no simulator, and lands outside the
calibrated support, so it pins the abstention path. The simulator one is a full income-diverse
customer whose every month publishes an interval, so it pins the arithmetic that produces the
band.

Regenerate only when the bundle deliberately changes, and read the diff before committing it:

    python -m release.record_fixtures
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from income_estimator.contracts.production_v1 import ProductionResultV1
from income_estimator.production import ProductionIncomeEstimator

ESTIMATOR_ROOT = Path(__file__).parents[1]
BUNDLE_ROOT = ESTIMATOR_ROOT / "bundles" / "production-0.11.0"
FIXTURE_ROOT = ESTIMATOR_ROOT / "tests" / "fixtures"

MINIMAL_FIXTURE = "production-0.11.0-expected.json"
SIMULATOR_FIXTURE = "production-0.11.0-income-diverse-seed-42.json"

SIMULATOR_SCENARIO = "income_diverse.yaml"
SIMULATOR_SEED = 42
SIMULATOR_MONTHS = 12


def minimal_request() -> dict[str, object]:
    """Must stay identical to the ``request_v1_2`` fixture in ``tests/conftest.py``."""

    return {
        "schema_version": "1.2",
        "source_contract_schema_version": "1.6",
        "run_id": "run-bundle",
        "customer_id": "customer-bundle",
        "currency": "BRL",
        "window_start": "2025-01-01",
        "window_end": "2025-12-31",
        "months": 12,
        "accounts": [
            {
                "schema_version": "1.2",
                "customer_id": "customer-bundle",
                "account_id": "checking",
                "institution_id": "bank-a",
                "currency": "BRL",
            }
        ],
        "transactions": [
            {
                "schema_version": "1.2",
                "transaction_id": f"txn-{index:02d}",
                "customer_id": "customer-bundle",
                "account_id": "checking",
                "posted_at": f"2025-{index:02d}-05",
                "observed_at": f"2025-{index:02d}-05",
                "direction": "CREDIT",
                "amount_minor": 640_000,
                "currency": "BRL",
                "description": "MONTHLY PAYROLL CREDIT ACME",
            }
            for index in range(1, 13)
        ],
        "coverage": [],
    }


def _months(result: ProductionResultV1, *, with_routing: bool) -> list[dict[str, object]]:
    assert result.estimate is not None
    rows: list[dict[str, object]] = []
    for item in result.estimate.monthly_estimates:
        row: dict[str, object] = {
            "month": item.month,
            "realized_income_estimate_minor": item.realized_income_estimate_minor,
            "sustainable_income_p10_minor": item.sustainable_income_p10_minor,
            "sustainable_income_p50_minor": item.sustainable_income_p50_minor,
            "sustainable_income_p90_minor": item.sustainable_income_p90_minor,
            "confidence_score_basis_points": item.confidence_score_basis_points,
            "quantile_unavailable_reason": item.quantile_unavailable_reason,
        }
        if with_routing:
            row["routing_reason_codes"] = list(item.routing_reason_codes)
        rows.append(row)
    return rows


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def record(bundle: Path = BUNDLE_ROOT, fixtures: Path = FIXTURE_ROOT) -> list[Path]:
    """Write both fixtures and return the paths written."""

    estimator = ProductionIncomeEstimator.from_bundle(bundle)
    written: list[Path] = []

    result = estimator.estimate_production(minimal_request())
    minimal = fixtures / MINIMAL_FIXTURE
    _write(
        minimal,
        {
            "bundle_id": result.bundle_id,
            "bundle_digest": result.bundle_digest,
            "estimator_version": result.estimator_version,
            "feature_set_version": result.feature_set_version,
            "model_versions": list(result.model_versions),
            "months": _months(result, with_routing=True),
            "result_sha256": hashlib.sha256(
                result.model_dump_json().encode("utf-8")
            ).hexdigest(),
        },
    )
    written.append(minimal)

    from finances_simulator.config import load_scenario_config
    from finances_simulator.generation import generate_scenario
    from finances_simulator.integration import build_estimator_input_v1_2

    config = load_scenario_config(
        ESTIMATOR_ROOT.parent / "finances_simulator" / "configs" / "scenarios" / SIMULATOR_SCENARIO
    )
    generated = generate_scenario(config, seed=SIMULATOR_SEED, months=SIMULATOR_MONTHS)
    simulated = estimator.estimate_production(build_estimator_input_v1_2(generated))
    assert simulated.estimate is not None

    simulator = fixtures / SIMULATOR_FIXTURE
    _write(
        simulator,
        {
            "scenario": SIMULATOR_SCENARIO,
            "seed": SIMULATOR_SEED,
            "months": SIMULATOR_MONTHS,
            "bundle_id": simulated.bundle_id,
            "bundle_digest": simulated.bundle_digest,
            "model_versions": list(simulated.model_versions),
            "published_interval_count": sum(
                1
                for item in simulated.estimate.monthly_estimates
                if item.sustainable_income_p10_minor is not None
            ),
            "months_detail": _months(simulated, with_routing=False),
            "result_sha256": hashlib.sha256(
                simulated.model_dump_json().encode("utf-8")
            ).hexdigest(),
        },
    )
    written.append(simulator)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="record-fixtures")
    parser.add_argument("--bundle", type=Path, default=BUNDLE_ROOT)
    parser.add_argument("--fixtures", type=Path, default=FIXTURE_ROOT)
    args = parser.parse_args(argv)

    for path in record(args.bundle, args.fixtures):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
