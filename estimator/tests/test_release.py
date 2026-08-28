"""The committed bundle is reproducible, and what it computes does not drift.

Two different gates. The first says the bundle in this repository is exactly what the builder
produces from the promoted artifacts, so nobody can hand-edit a manifest into the tree. The second
says the estimator bound to that bundle returns the same numbers it returned when the fixture was
recorded, which is the property a deployment actually depends on and the one no version string can
express.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from income_estimator.production import ProductionIncomeEstimator
from release.build_bundle import build_bundle
from release.check_documented_cli import check_documented_commands, documented_commands
from release.record_fixtures import record

BUNDLE_ROOT = Path(__file__).parents[1] / "bundles" / "production-0.11.0"
FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def test_committed_bundle_is_what_the_builder_produces(tmp_path: Path) -> None:
    """Byte-for-byte, manifest included, so the bundle digest is reproducible."""

    from income_estimator import __version__ as package_version

    rebuilt = tmp_path / "production-0.11.0"
    build_bundle(
        rebuilt,
        bundle_id="production-0.11.0",
        bundle_version="0.11.0",
        package_version=package_version,
    )

    committed_files = sorted(
        path.relative_to(BUNDLE_ROOT).as_posix()
        for path in BUNDLE_ROOT.rglob("*")
        if path.is_file()
    )
    rebuilt_files = sorted(
        path.relative_to(rebuilt).as_posix() for path in rebuilt.rglob("*") if path.is_file()
    )
    assert committed_files == rebuilt_files

    for relative in committed_files:
        assert (BUNDLE_ROOT / relative).read_bytes() == (rebuilt / relative).read_bytes(), relative


def test_bundle_build_is_deterministic(tmp_path: Path) -> None:
    """Two builds from the same inputs give one identity, not two."""

    from income_estimator import __version__ as package_version

    digests = []
    for name in ("first", "second"):
        target = tmp_path / name
        build_bundle(
            target,
            bundle_id="production-0.11.0",
            bundle_version="0.11.0",
            package_version=package_version,
        )
        digests.append(hashlib.sha256((target / "manifest.json").read_bytes()).hexdigest())

    assert digests[0] == digests[1]


def test_minimal_request_output_has_not_drifted(request_v1_2: dict[str, object]) -> None:
    expected = json.loads(
        (FIXTURE_ROOT / "production-0.11.0-expected.json").read_text(encoding="utf-8")
    )
    result = ProductionIncomeEstimator.from_bundle(BUNDLE_ROOT).estimate_production(request_v1_2)

    assert result.bundle_digest == expected["bundle_digest"]
    assert list(result.model_versions) == expected["model_versions"]
    assert [
        {
            "month": item.month,
            "realized_income_estimate_minor": item.realized_income_estimate_minor,
            "sustainable_income_p10_minor": item.sustainable_income_p10_minor,
            "sustainable_income_p50_minor": item.sustainable_income_p50_minor,
            "sustainable_income_p90_minor": item.sustainable_income_p90_minor,
            "confidence_score_basis_points": item.confidence_score_basis_points,
            "quantile_unavailable_reason": item.quantile_unavailable_reason,
            "routing_reason_codes": list(item.routing_reason_codes),
        }
        for item in result.estimate.monthly_estimates
    ] == expected["months"]
    assert (
        hashlib.sha256(result.model_dump_json().encode("utf-8")).hexdigest()
        == expected["result_sha256"]
    )


def test_minimal_request_abstains_outside_calibrated_support(
    request_v1_2: dict[str, object],
) -> None:
    """The recorded fixture is an abstention, and that is the point of recording it.

    One salaried account with no product domains and no declared coverage is nothing like the
    calibration population, and the estimator says so instead of publishing a band.
    """

    result = ProductionIncomeEstimator.from_bundle(BUNDLE_ROOT).estimate_production(request_v1_2)
    reasons = {item.quantile_unavailable_reason for item in result.estimate.monthly_estimates}

    assert reasons == {"OUT_OF_CALIBRATED_SUPPORT"}
    assert all(
        item.sustainable_income_p10_minor is None
        for item in result.estimate.monthly_estimates
    )


def test_simulator_request_output_has_not_drifted() -> None:
    """The published-interval path, which the minimal request never reaches."""

    pytest.importorskip("pyarrow")
    from finances_simulator.config import load_scenario_config
    from finances_simulator.generation import generate_scenario
    from finances_simulator.integration import build_estimator_input_v1_2

    expected = json.loads(
        (FIXTURE_ROOT / "production-0.11.0-income-diverse-seed-42.json").read_text(
            encoding="utf-8"
        )
    )
    simulator_root = Path(__file__).parents[2] / "finances_simulator"
    config = load_scenario_config(
        simulator_root / "configs/scenarios" / expected["scenario"]
    )
    generated = generate_scenario(config, seed=expected["seed"], months=expected["months"])
    request = build_estimator_input_v1_2(generated)

    result = ProductionIncomeEstimator.from_bundle(BUNDLE_ROOT).estimate_production(request)

    assert result.bundle_digest == expected["bundle_digest"]
    assert [
        {
            "month": item.month,
            "realized_income_estimate_minor": item.realized_income_estimate_minor,
            "sustainable_income_p10_minor": item.sustainable_income_p10_minor,
            "sustainable_income_p50_minor": item.sustainable_income_p50_minor,
            "sustainable_income_p90_minor": item.sustainable_income_p90_minor,
            "confidence_score_basis_points": item.confidence_score_basis_points,
            "quantile_unavailable_reason": item.quantile_unavailable_reason,
        }
        for item in result.estimate.monthly_estimates
    ] == expected["months_detail"]
    assert expected["published_interval_count"] == 12
    assert (
        hashlib.sha256(result.model_dump_json().encode("utf-8")).hexdigest()
        == expected["result_sha256"]
    )


def test_committed_fixtures_are_what_the_recorder_produces(tmp_path: Path) -> None:
    """Fixtures are evidence about the bundle, so they must not be editable by hand."""

    pytest.importorskip("pyarrow")
    written = record(BUNDLE_ROOT, tmp_path)

    assert written
    for path in written:
        committed = FIXTURE_ROOT / path.name
        assert committed.read_bytes() == path.read_bytes(), committed.name


def test_every_documented_cli_command_runs(tmp_path: Path) -> None:
    """The README is executable, and four of its commands once were not."""

    from release.check_documented_cli import sample_request

    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(sample_request()), encoding="utf-8")

    commands = documented_commands()
    assert len(commands) >= 10, "expected the README to document the full CLI surface"
    assert not check_documented_commands(request_path)
