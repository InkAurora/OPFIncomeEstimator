"""High-level generation service joining isolated simulator layers."""

from dataclasses import dataclass

from finances_simulator.config import ScenarioConfig
from finances_simulator.ground_truth import GroundTruthBundle, project_ground_truth
from finances_simulator.observations import ObservationBundle, project_observations
from finances_simulator.simulation.engine import SimulationRun, simulate
from finances_simulator.simulation.primitives import simulation_namespace


@dataclass(frozen=True, slots=True)
class GeneratedScenario:
    simulation: SimulationRun
    ground_truth: GroundTruthBundle
    observations: ObservationBundle


def generate_scenario(
    config: ScenarioConfig,
    *,
    seed: int,
    months: int | None = None,
) -> GeneratedScenario:
    """Generate all V0 layers while preserving their boundaries."""

    simulation = simulate(config, seed=seed, months=months)
    ground_truth = project_ground_truth(simulation)
    observations = project_observations(
        account=simulation.customer_twin.primary_account,
        ledger_entries=simulation.ledger_entries,
        start_date=simulation.start_date,
        months=simulation.months,
        namespace=simulation_namespace(simulation.config_sha256, simulation.seed),
    )
    return GeneratedScenario(
        simulation=simulation,
        ground_truth=ground_truth,
        observations=observations,
    )
