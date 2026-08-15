# Finances Simulator

This component generates deterministic synthetic financial histories for estimator development,
automated tests, and evaluation. V0 implements one salaried customer, one checking account, one
monthly salary, five monthly fixed expenses, and uniformly sampled variable expenses.

## Requirements and installation

- Python 3.12 or newer
- A virtual environment is recommended

From this directory, install the package and development tools:

```console
python -m pip install -c constraints-dev.txt -e ".[dev]"
```

The constraints file pins the fully verified development environment. Package metadata retains
compatible dependency ranges so the library can participate in a larger application's resolver.

## Generate a scenario

Run the bundled scenario from this directory:

```console
python -m finances_simulator generate --config configs/scenarios/salaried_basic.yaml --seed 42 --months 24 --output runs/salaried-basic-seed-42
```

`--config`, `--seed`, and `--output` are required. `--months` is optional and defaults to
`scenario.default_months` from the YAML configuration. The effective simulation length must be
between 1 and 1,200 months.

The output path must be a new path or an existing empty directory. Generation refuses to overwrite
a non-empty directory. Invalid configuration or generation errors are written to standard error and
return exit code 2. Runs are staged beside the destination and published by one directory rename, so
a write failure does not expose a partial output tree.

## Configuration behavior

The bundled [`salaried_basic.yaml`](configs/scenarios/salaried_basic.yaml) is the V0 reference.
Configuration schema version `1.0` uses:

- a first-of-month start date and a positive default duration;
- one three-letter uppercase currency code and a non-negative opening balance;
- one positive monthly salary scheduled on days 1 through 31;
- exactly five positive fixed-expense rules with unique IDs;
- inclusive ranges for monthly variable-expense count, amount, and day;
- at least one variable-expense merchant; and
- no unknown fields at any configuration level.

Salary and fixed-expense dates beyond a month's last day are clamped to that last calendar day.
Dates are not shifted for weekends or holidays. Variable days are limited to 1 through 28.

Each variable count, merchant, day, and integer amount is sampled uniformly with the versioned
`sha256-counter-v1` generator seeded by `--seed`. Range bounds are inclusive. Given the same
validated configuration, seed, month count, and simulator version, records, IDs, ordering, and
serialized JSONL are reproducible across supported Python runtimes.

All monetary values use integer minor units; floating-point money is never emitted. For example,
`650000` with currency `BRL` represents BRL 6,500.00. Event amounts are positive and the configured
opening balance is non-negative. Running and closing balances may be negative: V0 permits overdraft
instead of rejecting an expense.

See [contract schema 1.0](docs/contracts-v1.md) for every configuration and output field.

## Output layout and trust boundary

```text
<output>/
|-- run_manifest.json
|-- observed/
|   |-- accounts.jsonl
|   |-- balances.jsonl
|   `-- transactions.jsonl
`-- private/
    |-- customer_ground_truth.jsonl
    |-- customer_month_ground_truth.jsonl
    `-- transaction_ground_truth.jsonl
```

Files under `observed/` form the normalized, Open Finance-inspired estimator input. They contain
account, balance, and transaction observations but omit hidden economic classifications and true
income labels. Files under `private/` contain simulator ground truth for evaluation and must never be
provided to the estimator. `run_manifest.json` records versions, inputs, file counts, and SHA-256
digests.

JSONL files are UTF-8, contain one compact JSON object per line, use stable key ordering, and end each
record with a newline. Dataset ordering is deterministic.

A committed [seed-42 reference run](examples/generated/salaried_basic_seed_42/run_manifest.json)
shows the complete 24-month output and anchors the golden reproducibility test.

## Responsibilities

- Generate deterministic scenarios from an explicit seed.
- Emit observations through the same normalized, Open Finance-inspired contract consumed by the
  estimator.
- Model common income patterns and realistic non-income activity as the simulator expands.
- Preserve scenario labels as expected outcomes in private evaluation data.
- Keep estimator-visible observations separate from ground-truth-only attributes.
- Reconcile every ledger posting with its running balance.
- Avoid copying or reconstructing identifiable client records.

## V0 limitations

V0 proves the hidden-state, ledger, private-truth, and observed-data separation for a basic salaried
case. It does not yet model variable or commission income, multiple income sources, freelance or
seasonal income, job changes, income interruptions, multiple accounts, own-account transfers,
refunds, reversals, loans, cash deposits, cards, investments, missing periods, duplicates, or
institution-specific observation degradation. It also does not model taxes, payroll deductions,
interest, overdraft limits, holidays, or inflation.

## Boundary

The simulator models categories of data that may be available through Open Finance, but it is not an
official API mock and does not aim for exact payload compatibility. Its observation layer exposes
estimator-relevant data through project-owned schemas. Exact provider or Open Finance payloads belong
behind dedicated adapters.

Authentication, consent management, token storage, provider transport, and official API emulation
are out of scope. Generated output is synthetic test data, not evidence of estimator accuracy on the
target population. Production readiness still requires evaluation with governed, representative,
de-identified data and monitoring for systematic error.

The broader architecture, milestones, and acceptance criteria are in the
[implementation plan](../docs/implementation-plan.md).
