from __future__ import annotations

import json
from pathlib import Path

import pytest

from income_estimator.cli import main


@pytest.fixture
def request_file(tmp_path: Path, request_payload, transaction) -> Path:
    payload = request_payload(
        transactions=[
            transaction("salary-1", posted_at="2026-01-05"),
            transaction("salary-2", posted_at="2026-02-05"),
        ],
        months=2,
    )
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("flags", "marker"),
    [
        ((), "recurring-streams-0.2.0"),
        (("--baseline-0.1",), "rule-based-0.1.0"),
        (("--audit",), "transaction_decisions"),
        (("--features",), "customer-month-features-1.1.0"),
    ],
)
def test_cli_emits_each_view(request_file: Path, capsys, flags, marker) -> None:
    exit_code = main([*flags, str(request_file)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert marker in captured.out
    assert json.loads(captured.out)


def test_cli_reports_invalid_input(tmp_path: Path, capsys) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"schema_version": "1.0"}', encoding="utf-8")

    exit_code = main([str(path)])

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err
