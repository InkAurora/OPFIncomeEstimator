"""Run every ``income-estimator`` invocation that appears in the estimator README.

Documentation rots by being plausible. Four commands in this README paired a capacity model with a
calibration the runtime refuses, and nothing noticed until someone typed one of them. The fix is not
to be more careful; it is to make the README executable.

Commands are extracted rather than listed, so a new documented example is covered the moment it is
written and a stale one fails the moment it stops working. Only lines beginning with
``income-estimator`` are run; the ``python -m evaluation`` and ``python -m training`` examples in
the same file generate populations and are far too expensive for a gate.

Run from the estimator directory:

    python -m release.check_documented_cli
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shlex
from collections.abc import Sequence
from pathlib import Path

from income_estimator.cli import main as cli_main

ESTIMATOR_ROOT = Path(__file__).parents[1]
README = ESTIMATOR_ROOT / "README.md"
PLACEHOLDER = "request.json"

_FENCE = re.compile(r"```bash\n(.*?)```", re.DOTALL)


def documented_commands(readme: Path = README) -> tuple[tuple[str, ...], ...]:
    """Every ``income-estimator`` command in the README, as argument tuples.

    Continuation backslashes are joined first, so a wrapped example is one command rather than
    several fragments.
    """

    commands: list[tuple[str, ...]] = []
    for block in _FENCE.findall(readme.read_text(encoding="utf-8")):
        joined = block.replace("\\\n", " ")
        for line in joined.splitlines():
            stripped = line.strip()
            if not stripped.startswith("income-estimator"):
                continue
            commands.append(tuple(shlex.split(stripped)[1:]))
    return tuple(commands)


def sample_request() -> dict[str, object]:
    """A short contract-1.2 request, deliberately independent of the simulator."""

    return {
        "schema_version": "1.2",
        "source_contract_schema_version": "1.6",
        "run_id": "run-doc-check",
        "customer_id": "customer-doc-check",
        "currency": "BRL",
        "window_start": "2025-01-01",
        "window_end": "2025-03-31",
        "months": 3,
        "accounts": [
            {
                "schema_version": "1.2",
                "customer_id": "customer-doc-check",
                "account_id": "checking",
                "institution_id": "bank-a",
                "currency": "BRL",
            }
        ],
        "transactions": [
            {
                "schema_version": "1.2",
                "transaction_id": f"txn-{index:02d}",
                "customer_id": "customer-doc-check",
                "account_id": "checking",
                "posted_at": f"2025-{index:02d}-05",
                "observed_at": f"2025-{index:02d}-05",
                "direction": "CREDIT",
                "amount_minor": 610_000,
                "currency": "BRL",
                "description": "MONTHLY PAYROLL CREDIT ACME",
            }
            for index in range(1, 4)
        ],
        "coverage": [],
    }


def check_documented_commands(request_path: Path) -> list[tuple[tuple[str, ...], int, str]]:
    """Run each documented command and return the ones that did not exit zero."""

    failures: list[tuple[tuple[str, ...], int, str]] = []
    for command in documented_commands():
        argv = [str(request_path) if part == PLACEHOLDER else part for part in command]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli_main(argv)
        if code != 0:
            failures.append((command, code, stderr.getvalue().strip()))
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check-documented-cli")
    parser.add_argument(
        "--request",
        type=Path,
        default=ESTIMATOR_ROOT / "build" / "documented-cli-request.json",
        help="Where to write the generated sample request",
    )
    args = parser.parse_args(argv)

    args.request.parent.mkdir(parents=True, exist_ok=True)
    args.request.write_text(json.dumps(sample_request(), indent=2), encoding="utf-8")

    commands = documented_commands()
    if not commands:
        print("error: no income-estimator commands found in the README", flush=True)
        return 2

    failures = check_documented_commands(args.request)
    for command, code, error in failures:
        print(f"FAIL (exit {code}) income-estimator {' '.join(command)}\n  {error}")
    print(f"{len(commands) - len(failures)} of {len(commands)} documented commands ran")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
