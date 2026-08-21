"""High-level generation service joining isolated simulator layers."""

from dataclasses import dataclass

from finances_simulator.config import ScenarioConfig
from finances_simulator.ground_truth import GroundTruthBundle, project_ground_truth
from finances_simulator.ground_truth.projector_v1 import (
    GroundTruthBundleV1,
    project_ground_truth_v1,
)
from finances_simulator.ground_truth.projector_v2 import (
    GroundTruthBundleV2,
    project_ground_truth_v2,
)
from finances_simulator.ground_truth.projector_v3 import (
    GroundTruthBundleV3,
    project_ground_truth_v3,
)
from finances_simulator.ground_truth.projector_v4 import (
    GroundTruthBundleV4,
    project_ground_truth_v4,
)
from finances_simulator.ground_truth.projector_v5 import (
    GroundTruthBundleV5,
    project_ground_truth_v5,
)
from finances_simulator.observations import ObservationBundle, project_observations
from finances_simulator.observations.projector_v1 import (
    ObservationBundleV1,
    project_observations_v1,
)
from finances_simulator.observations.projector_v2 import (
    ObservationBundleV2,
    project_observations_v2,
)
from finances_simulator.observations.projector_v3 import (
    ObservationBundleV3,
    project_observations_v3,
)
from finances_simulator.observations.projector_v4 import (
    ObservationBundleV4,
    project_observations_v4,
)
from finances_simulator.observations.projector_v5 import (
    ObservationBundleV5,
    project_observations_v5,
)
from finances_simulator.simulation.engine import SimulationRun, simulate
from finances_simulator.simulation.primitives import (
    V1_PROFILE,
    V2_PROFILE,
    V3_PROFILE,
    V4_PROFILE,
    V5_PROFILE,
    simulation_namespace,
)
from finances_simulator.validation.v2 import validate_balance_sheet_truth
from finances_simulator.validation.v4 import validate_balance_sheet_truth_v4


@dataclass(frozen=True, slots=True)
class GeneratedScenario:
    simulation: SimulationRun
    ground_truth: (
        GroundTruthBundle
        | GroundTruthBundleV1
        | GroundTruthBundleV2
        | GroundTruthBundleV3
        | GroundTruthBundleV4
        | GroundTruthBundleV5
    )
    observations: (
        ObservationBundle
        | ObservationBundleV1
        | ObservationBundleV2
        | ObservationBundleV3
        | ObservationBundleV4
        | ObservationBundleV5
    )


def generate_scenario(
    config: ScenarioConfig,
    *,
    seed: int,
    months: int | None = None,
) -> GeneratedScenario:
    """Generate hidden, private, and observed layers for the selected contract."""

    simulation = simulate(config, seed=seed, months=months)
    world_namespace = simulation_namespace(
        simulation.world_config_sha256 or simulation.config_sha256,
        simulation.seed,
        simulator_version=(
            simulation.world_simulator_version or simulation.profile.simulator_version
        ),
    )
    if simulation.profile == V5_PROFILE:
        from finances_simulator.config_v5 import ScenarioConfigV5

        if not isinstance(config, ScenarioConfigV5):
            raise TypeError("schema-1.5 generation requires ScenarioConfigV5")
        observation_namespace = simulation_namespace(
            simulation.config_sha256,
            simulation.seed,
            simulator_version=simulation.profile.simulator_version,
        )
        ground_truth = project_ground_truth_v5(simulation, namespace=world_namespace)
        validate_balance_sheet_truth_v4(
            ground_truth.balance_sheets,
            ground_truth.customer_months,
        )
        observations = project_observations_v5(
            simulation,
            config,
            world_namespace=world_namespace,
            observation_namespace=observation_namespace,
        )
    elif simulation.profile == V4_PROFILE:
        ground_truth = project_ground_truth_v4(simulation, namespace=world_namespace)
        validate_balance_sheet_truth_v4(
            ground_truth.balance_sheets,
            ground_truth.customer_months,
        )
        observations = project_observations_v4(simulation, namespace=world_namespace)
    elif simulation.profile == V3_PROFILE:
        ground_truth = project_ground_truth_v3(simulation, namespace=world_namespace)
        validate_balance_sheet_truth(
            ground_truth.balance_sheets,
            ground_truth.customer_months,
        )
        observations = project_observations_v3(simulation, namespace=world_namespace)
    elif simulation.profile == V2_PROFILE:
        ground_truth = project_ground_truth_v2(simulation, namespace=world_namespace)
        validate_balance_sheet_truth(
            ground_truth.balance_sheets,
            ground_truth.customer_months,
        )
        observations = project_observations_v2(simulation, namespace=world_namespace)
    elif simulation.profile == V1_PROFILE:
        ground_truth = project_ground_truth_v1(simulation)
        observations = project_observations_v1(
            accounts=simulation.customer_twin.accounts,
            ledger_entries=simulation.ledger_entries,
            cards=simulation.cards,
            card_purchases=simulation.card_purchases,
            card_installments=simulation.card_installments,
            card_invoices=simulation.card_invoices,
            credit_limit_snapshots=simulation.credit_limit_snapshots,
            start_date=simulation.start_date,
            end_date=simulation.end_date,
            months=simulation.months,
            namespace=world_namespace,
        )
    else:
        ground_truth = project_ground_truth(simulation)
        observations = project_observations(
            account=simulation.customer_twin.primary_account,
            ledger_entries=simulation.ledger_entries,
            start_date=simulation.start_date,
            months=simulation.months,
            namespace=world_namespace,
        )
    return GeneratedScenario(
        simulation=simulation,
        ground_truth=ground_truth,
        observations=observations,
    )
