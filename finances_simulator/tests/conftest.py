"""Shared fixtures for simulator tests."""

from pathlib import Path

import pytest

from finances_simulator.config import ScenarioConfig, load_scenario_config
from finances_simulator.generation import GeneratedScenario, generate_scenario


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def example_config_path(project_root: Path) -> Path:
    return project_root / "configs" / "scenarios" / "salaried_basic.yaml"


@pytest.fixture(scope="session")
def scenario_config(example_config_path: Path) -> ScenarioConfig:
    return load_scenario_config(example_config_path)


@pytest.fixture(scope="session")
def generated_seed_42(scenario_config: ScenarioConfig) -> GeneratedScenario:
    return generate_scenario(scenario_config, seed=42, months=24)
