"""Schema-1.6 engine preserving world state while versioning observation policy."""

from dataclasses import replace

from finances_simulator.config import config_sha256
from finances_simulator.config_v4 import ScenarioConfigV4
from finances_simulator.config_v6 import ScenarioConfigV6
from finances_simulator.simulation.engine import SimulationRun
from finances_simulator.simulation.primitives import V6_PROFILE, make_run_id
from finances_simulator.simulation.v4 import simulate_v4


def _world_config(config: ScenarioConfigV6) -> ScenarioConfigV4:
    payload = config.model_dump(exclude={"observation_degradation"})
    payload["schema_version"] = "1.4"
    return ScenarioConfigV4.model_validate(payload)


def simulate_v6(
    config: ScenarioConfigV6,
    *,
    seed: int,
    months: int | None = None,
) -> SimulationRun:
    """Reuse frozen V4 economics and attach independent V6 observation identity."""

    base = simulate_v4(_world_config(config), seed=seed, months=months)
    fingerprint = config_sha256(config)
    return replace(
        base,
        run_id=make_run_id(
            fingerprint,
            seed,
            base.months,
            simulator_version=V6_PROFILE.simulator_version,
        ),
        config_sha256=fingerprint,
        profile=V6_PROFILE,
        world_config_sha256=base.config_sha256,
        world_simulator_version=base.profile.simulator_version,
    )


__all__ = ["simulate_v6"]
