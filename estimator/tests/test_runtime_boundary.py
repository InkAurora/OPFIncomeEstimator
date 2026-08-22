from __future__ import annotations

import ast
from pathlib import Path


def test_runtime_does_not_import_simulator_or_private_truth() -> None:
    runtime_root = Path(__file__).parents[1] / "src/income_estimator"
    forbidden_roots = {"finances_simulator", "training", "evaluation"}
    violations: list[str] = []

    for path in sorted(runtime_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module.split(".", maxsplit=1)[0] in forbidden_roots:
                    violations.append(f"{path.name}: {module}")

    assert violations == []
