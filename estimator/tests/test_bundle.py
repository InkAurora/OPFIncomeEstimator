"""Bundle contract 1.0 and the production loader.

Most of these tests are about refusal. The research estimator answers with whatever it can load,
which is the behaviour a deployment must not inherit, so what is worth asserting here is that every
way of handing the loader a bundle that is not exactly the promoted one ends in an exception rather
than in a number.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from income_estimator import __version__ as PACKAGE_VERSION
from income_estimator.cli import main as cli_main
from income_estimator.features import FEATURE_SCHEMA_FINGERPRINT, FEATURE_SET_VERSION
from income_estimator.pipeline import EnsembleIncomeEstimator
from income_estimator.production import (
    BundleCompatibilityError,
    BundleError,
    BundleIntegrityError,
    BundleManifestError,
    ProductionIncomeEstimator,
    verify_bundle,
)

BUNDLE_ROOT = Path(__file__).parents[1] / "bundles" / "production-0.11.0"


@pytest.fixture
def bundle_copy(tmp_path: Path) -> Path:
    """A writable bundle outside the repository, so a test can corrupt it safely."""

    destination = tmp_path / "deployed" / "production-0.11.0"
    shutil.copytree(BUNDLE_ROOT, destination)
    return destination


@pytest.fixture
def rewrite_manifest(bundle_copy: Path) -> Callable[[dict[str, object]], Path]:
    def rewrite(changes: dict[str, object]) -> Path:
        path = bundle_copy / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(changes)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return bundle_copy

    return rewrite


def test_committed_bundle_verifies() -> None:
    manifest, digest = verify_bundle(BUNDLE_ROOT)

    assert manifest.bundle_id == "production-0.11.0"
    assert manifest.capacity.version == "capacity-gbdt-stumps-0.6.0"
    assert manifest.calibration.version == "conditional-selector-intervals-0.11.0"
    assert manifest.feature_set_version == FEATURE_SET_VERSION
    assert manifest.feature_schema_fingerprint == FEATURE_SCHEMA_FINGERPRINT
    assert digest == hashlib.sha256((BUNDLE_ROOT / "manifest.json").read_bytes()).hexdigest()


def test_bundle_digest_covers_every_pinned_file() -> None:
    """The manifest digest is the bundle identity because it pins everything else."""

    manifest, _ = verify_bundle(BUNDLE_ROOT)
    for reference in manifest.files():
        target = BUNDLE_ROOT / reference.path
        assert hashlib.sha256(target.read_bytes()).hexdigest() == reference.sha256


def test_loads_from_a_path_outside_the_repository(
    bundle_copy: Path, request_v1_2: dict[str, object]
) -> None:
    estimator = ProductionIncomeEstimator.from_bundle(bundle_copy)
    result = estimator.estimate_production(request_v1_2)

    assert result.bundle_id == "production-0.11.0"
    assert result.estimate is not None
    assert result.estimate.monthly_estimates


def test_estimate_reports_exact_bundle_identity(request_v1_2: dict[str, object]) -> None:
    estimator = ProductionIncomeEstimator.from_bundle(BUNDLE_ROOT)
    result = estimator.estimate_production(request_v1_2)
    expected = hashlib.sha256((BUNDLE_ROOT / "manifest.json").read_bytes()).hexdigest()

    assert result.bundle_digest == expected
    assert result.estimator_package_version == PACKAGE_VERSION
    assert result.feature_set_version == FEATURE_SET_VERSION
    assert result.model_versions == (
        "capacity-gbdt-stumps-0.6.0",
        "conditional-selector-intervals-0.11.0",
    )
    assert result.explanation is None


def test_explanation_reports_exact_bundle_identity(request_v1_2: dict[str, object]) -> None:
    estimator = ProductionIncomeEstimator.from_bundle(BUNDLE_ROOT)
    result = estimator.explain_production(request_v1_2)
    expected = hashlib.sha256((BUNDLE_ROOT / "manifest.json").read_bytes()).hexdigest()

    assert result.bundle_digest == expected
    assert result.explanation is not None
    assert result.estimate is None
    assert result.explanation.model_versions == result.model_versions


def test_explanation_agrees_with_the_estimate_it_explains(
    request_v1_2: dict[str, object],
) -> None:
    estimator = ProductionIncomeEstimator.from_bundle(BUNDLE_ROOT)
    estimate = estimator.estimate_production(request_v1_2).estimate
    explanation = estimator.explain_production(request_v1_2).explanation

    assert estimate is not None and explanation is not None
    estimated = {
        item.month: item.realized_income_estimate_minor
        for item in estimate.monthly_estimates
    }
    explained = {
        item.month: item.realized_income_estimate_minor for item in explanation.monthly_explanations
    }
    assert estimated == explained


def test_one_corrupted_byte_in_a_model_fails(bundle_copy: Path) -> None:
    target = bundle_copy / "artifacts" / "capacity-estimator-0.6.0.json"
    payload = bytearray(target.read_bytes())
    payload[-2] = payload[-2] ^ 0x01
    target.write_bytes(bytes(payload))

    with pytest.raises(BundleIntegrityError, match="hashes to"):
        ProductionIncomeEstimator.from_bundle(bundle_copy)


def test_one_corrupted_byte_in_the_evidence_fails(bundle_copy: Path) -> None:
    """Provenance is pinned too. A bundle whose evidence was edited is not the bundle."""

    target = (
        bundle_copy / "provenance" / "lockbox-conditional-selector-intervals-0.11.0-report.json"
    )
    target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(BundleIntegrityError, match="hashes to"):
        ProductionIncomeEstimator.from_bundle(bundle_copy)


def test_missing_calibration_fails_closed(bundle_copy: Path) -> None:
    """No silent degradation to the recurring-stream component."""

    (bundle_copy / "artifacts" / "quantile-calibration-0.11.0.json").unlink()

    with pytest.raises(BundleIntegrityError, match="bundle file missing"):
        ProductionIncomeEstimator.from_bundle(bundle_copy)


def test_missing_capacity_fails_closed(bundle_copy: Path) -> None:
    (bundle_copy / "artifacts" / "capacity-estimator-0.6.0.json").unlink()

    with pytest.raises(BundleIntegrityError, match="bundle file missing"):
        ProductionIncomeEstimator.from_bundle(bundle_copy)


def test_missing_manifest_fails(bundle_copy: Path) -> None:
    (bundle_copy / "manifest.json").unlink()

    with pytest.raises(BundleManifestError, match="manifest not found"):
        ProductionIncomeEstimator.from_bundle(bundle_copy)


def test_absent_directory_fails() -> None:
    with pytest.raises(BundleManifestError, match="directory not found"):
        ProductionIncomeEstimator.from_bundle(BUNDLE_ROOT.parent / "no-such-bundle")


def test_unknown_bundle_contract_fails(
    rewrite_manifest: Callable[[dict[str, object]], Path],
) -> None:
    bundle = rewrite_manifest({"schema_version": "2.0"})

    with pytest.raises(BundleManifestError, match="not supported"):
        ProductionIncomeEstimator.from_bundle(bundle)


def test_wrong_feature_set_fails(
    rewrite_manifest: Callable[[dict[str, object]], Path],
) -> None:
    bundle = rewrite_manifest({"feature_set_version": "customer-month-features-9.9.9"})

    with pytest.raises(BundleCompatibilityError, match="requires feature set"):
        ProductionIncomeEstimator.from_bundle(bundle)


def test_wrong_feature_fingerprint_fails(
    rewrite_manifest: Callable[[dict[str, object]], Path],
) -> None:
    """Names can agree while formulas do not, which is what the fingerprint is for."""

    bundle = rewrite_manifest({"feature_schema_fingerprint": "0" * 32})

    with pytest.raises(BundleCompatibilityError, match="feature schema fingerprint"):
        ProductionIncomeEstimator.from_bundle(bundle)


def test_manifest_that_misnames_its_capacity_artifact_fails(
    rewrite_manifest: Callable[[dict[str, object]], Path],
) -> None:
    """A digest match proves the bytes, not that the manifest described them honestly."""

    manifest_path = BUNDLE_ROOT / "manifest.json"
    capacity = json.loads(manifest_path.read_text(encoding="utf-8"))["capacity"]
    bundle = rewrite_manifest({"capacity": {**capacity, "version": "capacity-gbdt-stumps-9.9.9"}})

    with pytest.raises(BundleCompatibilityError, match="manifest names capacity"):
        ProductionIncomeEstimator.from_bundle(bundle)


def test_bundle_newer_than_the_package_fails(
    rewrite_manifest: Callable[[dict[str, object]], Path],
) -> None:
    bundle = rewrite_manifest({"estimator_package_version": "99.0.0"})

    with pytest.raises(BundleCompatibilityError, match="or newer"):
        ProductionIncomeEstimator.from_bundle(bundle)


def test_older_bundle_than_the_package_is_accepted(
    rewrite_manifest: Callable[[dict[str, object]], Path],
) -> None:
    """The package floor is a floor, not an equality check."""

    bundle = rewrite_manifest({"estimator_package_version": "0.1.0"})

    assert ProductionIncomeEstimator.from_bundle(bundle).manifest is not None


def test_wrong_estimator_version_fails(
    rewrite_manifest: Callable[[dict[str, object]], Path],
) -> None:
    bundle = rewrite_manifest({"estimator_version": "ensemble-9.9.9"})

    with pytest.raises(BundleCompatibilityError, match="expects estimator"):
        ProductionIncomeEstimator.from_bundle(bundle)


def test_wrong_output_contract_fails(
    rewrite_manifest: Callable[[dict[str, object]], Path],
) -> None:
    bundle = rewrite_manifest({"output_contract_version": "1.2"})

    with pytest.raises(BundleCompatibilityError, match="expects output contract"):
        ProductionIncomeEstimator.from_bundle(bundle)


def test_misdeclared_support_envelope_fails(
    rewrite_manifest: Callable[[dict[str, object]], Path],
) -> None:
    bundle = rewrite_manifest({"support_envelope_version": None})

    with pytest.raises(BundleCompatibilityError, match="support envelope"):
        ProductionIncomeEstimator.from_bundle(bundle)


def test_a_loosely_constructed_estimator_cannot_claim_bundle_identity(
    request_v1_2: dict[str, object],
) -> None:
    """The class is reachable without a bundle; the production result is not."""

    estimator = ProductionIncomeEstimator(
        BUNDLE_ROOT / "artifacts" / "capacity-estimator-0.6.0.json",
        None,
        BUNDLE_ROOT / "artifacts" / "quantile-calibration-0.11.0.json",
    )

    assert estimator.estimate_v1_1(request_v1_2).monthly_estimates
    with pytest.raises(BundleError, match="not loaded from a bundle"):
        estimator.estimate_production(request_v1_2)


def test_research_profiles_remain_available(request_v1_2: dict[str, object]) -> None:
    """The laboratory path still degrades on purpose, and still says so."""

    estimate = EnsembleIncomeEstimator().estimate_v1_1(request_v1_2)
    reasons = {code for item in estimate.monthly_estimates for code in item.routing_reason_codes}

    assert "CAPACITY_MODEL_UNAVAILABLE" in reasons
    assert estimate.model_versions == ()


def test_cli_bundle_path_emits_a_production_result(
    tmp_path: Path, request_v1_2: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request_v1_2), encoding="utf-8")

    assert cli_main([str(request_path), "--bundle", str(BUNDLE_ROOT)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["bundle_id"] == "production-0.11.0"
    assert payload["estimate"] is not None
    assert payload["explanation"] is None


def test_cli_bundle_explain_emits_an_explanation(
    tmp_path: Path, request_v1_2: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request_v1_2), encoding="utf-8")

    assert cli_main([str(request_path), "--bundle", str(BUNDLE_ROOT), "--explain"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["explanation"] is not None
    assert payload["estimate"] is None


@pytest.mark.parametrize(
    "flag",
    ["--ensemble", "--features", "--audit", "--baseline-0.1"],
)
def test_cli_refuses_to_mix_production_and_research_surfaces(
    flag: str,
    tmp_path: Path,
    request_v1_2: dict[str, object],
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request_v1_2), encoding="utf-8")

    assert cli_main([str(request_path), "--bundle", str(BUNDLE_ROOT), flag]) == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_cli_reports_a_corrupted_bundle_rather_than_estimating(
    bundle_copy: Path,
    tmp_path: Path,
    request_v1_2: dict[str, object],
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = bundle_copy / "artifacts" / "quantile-calibration-0.11.0.json"
    target.write_bytes(target.read_bytes() + b"\n")
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request_v1_2), encoding="utf-8")

    assert cli_main([str(request_path), "--bundle", str(bundle_copy)]) == 2
    captured = capsys.readouterr()
    assert "hashes to" in captured.err
    assert captured.out == ""


def test_a_calibration_not_fitted_against_the_bundled_capacity_fails(bundle_copy: Path) -> None:
    """The binding the runtime already enforces, reached through the bundle path.

    The manifest is made internally consistent first — path, version, and digest all describe the
    substituted file — so integrity passes and the only thing left to catch the swap is the binding
    itself.
    """

    source = (
        Path(__file__).parents[1] / "training" / "artifacts"
        / "quantile-calibration-0.8.0.json"
    )
    relative = "artifacts/quantile-calibration-0.8.0.json"
    payload = source.read_bytes()
    (bundle_copy / relative).write_bytes(payload)
    (bundle_copy / "artifacts" / "quantile-calibration-0.11.0.json").unlink()

    manifest_path = bundle_copy / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["calibration"] = {
        "path": relative,
        "version": "conformal-intervals-0.8.0",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(BundleCompatibilityError, match="not a bound pair"):
        ProductionIncomeEstimator.from_bundle(bundle_copy)


def test_release_lockbox_report_travels_with_the_bundle() -> None:
    """The reading that measured these exact bytes, not only the one that promoted them."""

    manifest, _ = verify_bundle(BUNDLE_ROOT)
    release_report = manifest.provenance.release_lockbox_report

    assert release_report is not None
    payload = json.loads(
        (BUNDLE_ROOT / release_report.path).read_text(encoding="utf-8")
    )
    assert payload["status"] == "RELEASE_CONFIRMED"
    assert payload["artifact_sha256"] == manifest.calibration.sha256
