"""Verification for the demo layer.

The interesting tests here are not about rendering. They are about the two claims the demo makes to
a room of people: that the estimator never saw the answer, and that the same inputs reproduce the
same numbers.
"""

from __future__ import annotations

import json
import shutil
import time

import pytest
from demo_app.export import build_evidence, evidence_json
from demo_app.profiles import PROFILES, get_profile, supported_months
from demo_app.service import (
    EXPECTED_BUNDLE_ID,
    EXPECTED_MODEL_VERSIONS,
    PRIVATE_FIELD_NAMES,
    PROMOTED_BUNDLE_PATH,
    DemoConfigurationError,
    build_request,
    generate_world,
    join_truth,
    load_estimator,
    project_private_truth,
    run_demo,
    run_inference,
)
from income_estimator.production import ProductionIncomeEstimator

ALL_PROFILE_KEYS = [profile.key for profile in PROFILES]


@pytest.fixture(scope="module")
def estimator():
    return load_estimator()


@pytest.mark.parametrize("profile_key", ALL_PROFILE_KEYS)
def test_every_profile_generates_and_estimates(profile_key: str) -> None:
    """Each registered profile runs the whole flow at each history length it supports."""

    for months in supported_months(profile_key):
        result = run_demo(profile_key, seed=get_profile(profile_key).default_seed, months=months)

        assert len(result.month_rows) == months
        assert result.currency == "BRL"
        assert all(row.realized_estimate_minor >= 0 for row in result.month_rows)
        # Every month was joined against a truth record, so the comparison is never blank.
        assert all(row.truth_realized_minor is not None for row in result.month_rows)
        assert all(row.truth_sustainable_minor is not None for row in result.month_rows)


@pytest.mark.parametrize("profile_key", ALL_PROFILE_KEYS)
def test_the_same_profile_and_seed_reproduce_the_same_numbers(profile_key: str) -> None:
    """A screenshot taken from one run must still be true of the next."""

    months = supported_months(profile_key)[0]
    first = run_demo(profile_key, seed=4242, months=months)
    second = run_demo(profile_key, seed=4242, months=months)

    assert first.estimate.model_dump_json() == second.estimate.model_dump_json()
    assert first.explanation.model_dump_json() == second.explanation.model_dump_json()
    assert first.month_rows == second.month_rows
    assert first.balance_rows == second.balance_rows


def test_a_different_seed_produces_a_different_client() -> None:
    """Determinism is not the same as a constant, so check the seed is actually read."""

    months = supported_months("mixed_income_professional")[0]
    first = run_demo("mixed_income_professional", seed=4242, months=months)
    second = run_demo("mixed_income_professional", seed=4243, months=months)

    assert first.customer_id != second.customer_id
    assert first.estimate.model_dump_json() != second.estimate.model_dump_json()


@pytest.mark.parametrize("profile_key", ALL_PROFILE_KEYS)
def test_the_estimator_request_carries_no_private_field(profile_key: str) -> None:
    """The boundary claim, checked against the serialized request rather than argued.

    Field names are matched over the whole document, so a private value nested anywhere inside any
    record is caught, not only one promoted to a top-level key.
    """

    profile = get_profile(profile_key)
    months = supported_months(profile_key)[0]
    world = generate_world(profile, seed=profile.default_seed, months=months)
    request = build_request(world)

    document = request.model_dump(mode="json")
    found = _keys_in(document) & PRIVATE_FIELD_NAMES
    assert not found, f"private fields reached the estimator request: {sorted(found)}"

    # The private targets do exist for this run; the request simply does not carry them.
    truth = project_private_truth(world)
    assert truth, "the scenario should project private income targets"
    assert PRIVATE_FIELD_NAMES & set(next(iter(truth.values())).model_dump())


def _keys_in(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for nested in value.values():
            keys |= _keys_in(nested)
        return keys
    if isinstance(value, (list, tuple)):
        keys: set[str] = set()
        for item in value:
            keys |= _keys_in(item)
        return keys
    return set()


def test_the_truth_is_joined_only_after_inference(estimator) -> None:
    """Inference is reachable with the request alone, and the join needs a finished inference.

    The ordering is enforced by the signatures: ``run_inference`` accepts no scenario, and
    ``join_truth`` cannot be called without the ``Inference`` that ``run_inference`` returns.
    """

    profile = get_profile("salaried_partial_consent")
    world = generate_world(profile, seed=99, months=12)
    request = build_request(world)

    inference = run_inference(estimator, request)
    assert inference.estimate.monthly_estimates
    assert not hasattr(inference, "truth")

    result = join_truth(
        profile,
        seed=99,
        months=12,
        world=world,
        request=request,
        inference=inference,
        generation_seconds=0.0,
    )
    # The join changed nothing about the estimate it was handed.
    assert result.estimate.model_dump_json() == inference.estimate.model_dump_json()
    assert result.month_rows[0].truth_realized_minor is not None


def test_the_exact_promoted_pair_answers_and_is_named_in_the_output(estimator) -> None:
    assert isinstance(estimator, ProductionIncomeEstimator)
    assert estimator.manifest.bundle_id == EXPECTED_BUNDLE_ID
    assert estimator.bundle_digest is not None
    assert estimator.model_versions == EXPECTED_MODEL_VERSIONS

    result = run_demo("mixed_income_professional", seed=1234, months=12)
    assert result.model_versions == EXPECTED_MODEL_VERSIONS
    assert result.bundle_id == EXPECTED_BUNDLE_ID
    assert result.bundle_version == "0.11.0"
    assert result.estimator_package_version == "0.11.0"
    assert result.bundle_digest == estimator.bundle_digest
    assert set(EXPECTED_MODEL_VERSIONS) <= set(result.explanation.model_versions)

    evidence = build_evidence(result)
    assert evidence["evidence_schema_version"] == "1.1"
    assert evidence["promotion_bundle"]["bundle_id"] == EXPECTED_BUNDLE_ID
    assert evidence["promotion_bundle"]["bundle_digest"] == estimator.bundle_digest
    assert evidence["artifact_versions"]["model_versions"] == list(EXPECTED_MODEL_VERSIONS)
    assert evidence["artifact_versions"]["estimator_version"] == "ensemble-0.6.0"
    assert evidence["artifact_versions"]["input_contract_version"] == "1.2"
    assert evidence["artifact_versions"]["output_contract_version"] == "1.1"


def test_an_out_of_support_month_abstains_visibly() -> None:
    """Where no interval is published, the reason travels with the row and into the export."""

    result = run_demo("mixed_income_professional", seed=1234, months=24)

    abstained = [row for row in result.month_rows if not row.has_interval]
    assert abstained, "this run is expected to leave the calibrated support"
    assert all(row.quantile_unavailable_reason for row in abstained)
    assert all(row.sustainable_p10_minor is None for row in abstained)
    assert all(row.interval_contains_truth is None for row in abstained)

    assert result.data_quality.months_abstained == len(abstained)
    assert "OUT_OF_CALIBRATED_SUPPORT" in result.data_quality.abstention_reasons

    exported = json.loads(evidence_json(result))
    reasons = {
        row["quantile_unavailable_reason"]
        for row in exported["private_truth_comparison"]["monthly"]
        if row["quantile_unavailable_reason"]
    }
    assert "OUT_OF_CALIBRATED_SUPPORT" in reasons
    assert exported["data_quality"]["months_abstained"] == len(abstained)


def test_an_altered_bundle_is_refused_with_a_readable_message(
    monkeypatch, tmp_path
) -> None:
    import demo_app.service as service

    altered = tmp_path / EXPECTED_BUNDLE_ID
    shutil.copytree(PROMOTED_BUNDLE_PATH, altered)
    target = altered / "artifacts" / "quantile-calibration-0.11.0.json"
    target.write_bytes(target.read_bytes() + b"\n")
    monkeypatch.setattr(service, "PROMOTED_BUNDLE_PATH", altered)
    service.load_estimator.cache_clear()
    try:
        with pytest.raises(DemoConfigurationError, match="hashes to"):
            service.load_estimator()
    finally:
        service.load_estimator.cache_clear()


def test_a_missing_bundle_is_refused_with_a_readable_message(monkeypatch, tmp_path) -> None:
    import demo_app.service as service

    monkeypatch.setattr(service, "PROMOTED_BUNDLE_PATH", tmp_path / "absent")
    service.load_estimator.cache_clear()
    try:
        with pytest.raises(DemoConfigurationError, match="bundle directory not found"):
            service.load_estimator()
    finally:
        service.load_estimator.cache_clear()


def test_a_history_longer_than_the_scenario_configures_is_refused() -> None:
    """Generating past a scenario's horizon ends every income source, so the demo will not do it.

    Past ``default_months`` the private sustainable target decays toward zero while the estimator,
    which cannot see a source's end date, keeps reporting the level it observed. The resulting
    error measures the stretched configuration, not the estimator.
    """

    profile = get_profile("high_volatility_entrepreneur")
    assert supported_months(profile.key) == (12,)

    with pytest.raises(DemoConfigurationError, match="supports"):
        generate_world(profile, seed=1234, months=24)
    with pytest.raises(DemoConfigurationError, match="supports"):
        run_demo(profile.key, seed=1234, months=24)


def test_supported_months_follows_the_scenario_configuration() -> None:
    from finances_simulator.config import load_scenario_config

    for profile in PROFILES:
        configured = load_scenario_config(profile.scenario_path).scenario.default_months
        allowed = supported_months(profile.key)
        assert allowed, f"{profile.key} offers no history length"
        assert max(allowed) <= configured


def test_the_bundle_the_demo_names_is_the_promoted_one() -> None:
    manifest = json.loads((PROMOTED_BUNDLE_PATH / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["bundle_id"] == EXPECTED_BUNDLE_ID
    payload = json.loads(
        (PROMOTED_BUNDLE_PATH / manifest["capacity"]["path"]).read_text(encoding="utf-8")
    )
    assert payload["model_version"] == "capacity-gbdt-stumps-0.6.0"


@pytest.mark.parametrize("profile_key", ALL_PROFILE_KEYS)
def test_every_profile_answers_inside_the_demo_budget(profile_key: str) -> None:
    """The acceptance criterion is a run under five seconds with the artifacts already loaded."""

    load_estimator()
    months = supported_months(profile_key)[-1]
    started = time.perf_counter()
    result = run_demo(profile_key, seed=get_profile(profile_key).default_seed, months=months)
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, f"{profile_key} took {elapsed:.2f}s"
    assert result.total_seconds < 5.0


def test_the_evidence_bundle_carries_everything_the_demo_showed() -> None:
    result = run_demo("salaried_life_events", seed=1234, months=12)
    exported = json.loads(evidence_json(result))

    assert exported["synthetic_data"] is True
    assert exported["run"]["profile_key"] == "salaried_life_events"
    assert exported["run"]["seed"] == 1234
    assert exported["run"]["months"] == 12
    assert exported["estimate"]["monthly_estimates"]
    assert exported["explanation"]["monthly_explanations"]
    assert len(exported["private_truth_comparison"]["monthly"]) == 12
    assert exported["private_truth_comparison"]["disclaimer"]
    assert exported["summary_metrics"]["months_estimated"] == 12
    assert exported["summary_metrics"]["nominal_interval_coverage"] == 0.80
    # The truth lives under one clearly named key and nowhere else in the document.
    assert "truth_realized_income_minor" not in json.dumps(exported["estimate"])
    assert "truth_realized_income_minor" not in json.dumps(exported["explanation"])


def test_the_evidence_bundle_is_json_serializable_and_stable() -> None:
    result = run_demo("noisy_financial_feed", seed=7, months=12)
    first = evidence_json(result)
    second = evidence_json(result)

    parsed_first = json.loads(first)
    parsed_second = json.loads(second)
    parsed_first.pop("generated_at")
    parsed_second.pop("generated_at")
    assert parsed_first == parsed_second
