"""The wheel plus one bundle directory is enough, on a machine that has nothing else.

Every other test in this suite imports from the source tree, which proves the code works and proves
nothing about what ships. This one builds the wheel, installs it into an empty interpreter, copies
the bundle somewhere unrelated, and runs the console script from a working directory that is not
the repository. If any of that quietly depended on a repository-relative path, this is where it
shows.

Opt-in. It builds a wheel and creates a virtual environment, which is too slow for the inner loop
and needs a package index for ``pydantic``. CI sets ``INCOME_ESTIMATOR_CLEAN_INSTALL_TEST=1``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ESTIMATOR_ROOT = Path(__file__).parents[1]
BUNDLE_ROOT = ESTIMATOR_ROOT / "bundles" / "production-0.11.0"
FIXTURE_ROOT = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skipif(
    not os.environ.get("INCOME_ESTIMATOR_CLEAN_INSTALL_TEST"),
    reason="set INCOME_ESTIMATOR_CLEAN_INSTALL_TEST=1 to build a wheel and a virtual environment",
)


def _run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        text=True,
        check=False,
        **kwargs,  # type: ignore[arg-type]
    )


def _venv_script(root: Path, name: str) -> Path:
    directory = "Scripts" if sys.platform == "win32" else "bin"
    suffix = ".exe" if sys.platform == "win32" else ""
    return root / directory / f"{name}{suffix}"


@pytest.fixture(scope="module")
def clean_environment(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An interpreter that has the wheel and its declared dependencies, and nothing of this repo."""

    root = tmp_path_factory.mktemp("clean-install")
    wheelhouse = root / "wheelhouse"
    build = _run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheelhouse), "."],
        cwd=str(ESTIMATOR_ROOT),
    )
    if build.returncode != 0:
        pytest.skip(f"could not build a wheel: {build.stderr[-2000:]}")
    wheels = sorted(wheelhouse.glob("income_estimator-*.whl"))
    assert wheels, "pip wheel produced no income_estimator wheel"

    environment = root / "venv"
    created = _run([sys.executable, "-m", "venv", str(environment)])
    if created.returncode != 0:
        pytest.skip(f"could not create a virtual environment: {created.stderr[-2000:]}")

    installed = _run(
        [str(_venv_script(environment, "python")), "-m", "pip", "install", str(wheels[0])]
    )
    if installed.returncode != 0:
        pytest.skip(f"could not install the wheel: {installed.stderr[-2000:]}")
    return environment


def test_wheel_carries_no_artifacts(tmp_path: Path) -> None:
    """Model bytes and code have different lifecycles; the wheel ships only one of them."""

    wheelhouse = tmp_path / "wheelhouse"
    build = _run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheelhouse), "."],
        cwd=str(ESTIMATOR_ROOT),
    )
    if build.returncode != 0:
        pytest.skip(f"could not build a wheel: {build.stderr[-2000:]}")
    wheel = sorted(wheelhouse.glob("income_estimator-*.whl"))[0]
    names = zipfile.ZipFile(wheel).namelist()

    assert any(name.endswith("production.py") for name in names)
    assert not [
        name
        for name in names
        if "bundles/" in name or "training/" in name or "evaluation/" in name
    ]


def test_installed_wheel_imports_without_the_repository(clean_environment: Path) -> None:
    python = _venv_script(clean_environment, "python")
    completed = _run(
        [str(python), "-c", "import income_estimator; print(income_estimator.__version__)"],
        cwd=str(clean_environment),
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "0.11.0"


def test_console_script_runs_a_relocated_bundle(
    clean_environment: Path,
    tmp_path: Path,
    request_v1_2: dict[str, object],
) -> None:
    """The stop condition: fresh machine, one bundle directory, reproduced output."""

    deployment = tmp_path / "deployment"
    deployment.mkdir()
    shutil.copytree(BUNDLE_ROOT, deployment / "bundle")
    request_path = deployment / "request.json"
    request_path.write_text(json.dumps(request_v1_2), encoding="utf-8")

    completed = _run(
        [
            str(_venv_script(clean_environment, "income-estimator")),
            str(request_path),
            "--bundle",
            "bundle",
        ],
        cwd=str(deployment),
    )
    assert completed.returncode == 0, completed.stderr

    expected = json.loads(
        (FIXTURE_ROOT / "production-0.11.0-expected.json").read_text(encoding="utf-8")
    )
    payload = json.loads(completed.stdout)
    assert payload["bundle_digest"] == expected["bundle_digest"]
    assert payload["model_versions"] == expected["model_versions"]
    assert [
        {
            "month": item["month"],
            "realized_income_estimate_minor": item["realized_income_estimate_minor"],
            "sustainable_income_p10_minor": item["sustainable_income_p10_minor"],
            "sustainable_income_p50_minor": item["sustainable_income_p50_minor"],
            "sustainable_income_p90_minor": item["sustainable_income_p90_minor"],
            "confidence_score_basis_points": item["confidence_score_basis_points"],
            "quantile_unavailable_reason": item["quantile_unavailable_reason"],
            "routing_reason_codes": item["routing_reason_codes"],
        }
        for item in payload["estimate"]["monthly_estimates"]
    ] == expected["months"]


def test_relocated_bundle_still_refuses_a_corrupted_byte(
    clean_environment: Path,
    tmp_path: Path,
    request_v1_2: dict[str, object],
) -> None:
    deployment = tmp_path / "corrupt"
    deployment.mkdir()
    shutil.copytree(BUNDLE_ROOT, deployment / "bundle")
    target = deployment / "bundle" / "artifacts" / "capacity-estimator-0.6.0.json"
    target.write_bytes(target.read_bytes() + b"\n")
    request_path = deployment / "request.json"
    request_path.write_text(json.dumps(request_v1_2), encoding="utf-8")

    completed = _run(
        [
            str(_venv_script(clean_environment, "income-estimator")),
            str(request_path),
            "--bundle",
            "bundle",
        ],
        cwd=str(deployment),
    )

    assert completed.returncode == 2
    assert "hashes to" in completed.stderr
    assert completed.stdout == ""
