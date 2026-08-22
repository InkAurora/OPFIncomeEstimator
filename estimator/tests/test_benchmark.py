from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from evaluation.benchmark import (
    BenchmarkSuite,
    render_true_vs_estimated_svg,
    run_benchmark,
    write_report,
)


def test_benchmark_artifacts_are_deterministic_and_show_improvement(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[2]
    suite = BenchmarkSuite(
        name="incomplete_observation",
        scenario_path=(
            project_root
            / "finances_simulator/configs/scenarios/incomplete_observation.yaml"
        ),
        first_seed=50_000,
        population_size=4,
        months=12,
    )

    first_report, first_points = run_benchmark((suite,), workers=1)
    second_report, second_points = run_benchmark((suite,), workers=2)
    first_json = tmp_path / "first.json"
    second_json = tmp_path / "second.json"
    first_svg = tmp_path / "first.svg"
    second_svg = tmp_path / "second.svg"
    write_report(first_report, first_json)
    write_report(second_report, second_json)
    render_true_vs_estimated_svg(
        first_points[suite.name], first_svg, suite_name=suite.name
    )
    render_true_vs_estimated_svg(
        second_points[suite.name], second_svg, suite_name=suite.name
    )

    comparison = first_report["suites"][0]["comparison"]
    assert comparison["mae_improvement_minor"] > 0
    assert first_report["promotion"]["status"] == "PASS"
    assert first_json.read_bytes() == second_json.read_bytes()
    assert first_svg.read_bytes() == second_svg.read_bytes()
    assert b"customer_id" not in first_json.read_bytes()
    svg_root = ElementTree.parse(first_svg).getroot()
    assert svg_root.tag.endswith("svg")
    assert len([item for item in svg_root.iter() if item.tag.endswith("circle")]) > 0
