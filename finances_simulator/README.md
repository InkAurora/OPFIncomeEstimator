# Finances Simulator

This component generates deterministic synthetic financial histories for estimator development,
automated tests, and evaluation. Engine `0.2.0` adds multiple institutions and deposit accounts,
routed cash flows, own-account transfers, credit cards, utilization limits, invoices, full automatic
payments, and installment purchases. Frozen engine `0.1.0` behavior remains available for schema
`1.0` configurations.

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

Run the schema `1.1` scenario from this directory:

```console
python -m finances_simulator generate --config configs/scenarios/salaried_multi_account_card.yaml --seed 42 --months 24 --output runs/salaried-multi-account-card-seed-42
```

Run the frozen schema `1.0` scenario with:

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

The loader dispatches on the required top-level `schema_version`:

- [`salaried_basic.yaml`](configs/scenarios/salaried_basic.yaml) uses frozen contract `1.0` and
  engine `0.1.0`: one checking account, salary, five fixed expenses, and random variable expenses.
- [`salaried_multi_account_card.yaml`](configs/scenarios/salaried_multi_account_card.yaml) uses
  contract `1.1` and engine `0.2.0`: two institutions and accounts, explicit routing, one monthly
  own transfer, one card, utilization policy, statement invoices, and installment purchases.

Both schemas reject unknown fields and use one uppercase three-letter currency. Contract `1.1`
requires at least two institutions and accounts, exactly five fixed expenses, and at least one own
transfer, card, and card-purchase rule. References and rule IDs are validated before simulation.

Configured days beyond month end clamp to the final calendar day; weekends and holidays cause no
shift. Variable deposit expenses use an isolated deterministic SHA-256 counter stream. Card purchase
attempts follow their configured month schedule. Purchases exceeding the configured used-limit
ceiling are omitted under `DECLINE` policy.

All money uses integer minor units. For example, `650000` BRL represents BRL 6,500.00. Account
balances may become negative because expenses, transfers, and full card payments have no
available-funds check.

Exact fields and semantics:

- [contract schema 1.1](docs/contracts-v1-1.md)
- [frozen contract schema 1.0](docs/contracts-v1.md)

## Output layout and trust boundary

Schema `1.1` emits:

```text
<output>/
|-- run_manifest.json
|-- observed/
|   |-- accounts.jsonl
|   |-- balances.jsonl
|   |-- transactions.jsonl
|   |-- credit_cards.jsonl
|   |-- credit_limits.jsonl
|   |-- credit_card_transactions.jsonl
|   |-- credit_card_invoices.jsonl
|   `-- credit_card_invoice_items.jsonl
`-- private/
    |-- customer_ground_truth.jsonl
    |-- customer_month_ground_truth.jsonl
    |-- transaction_ground_truth.jsonl
    `-- credit_card_transaction_ground_truth.jsonl
```

Schema `1.0` retains its original three observed and three private JSONL datasets.

Files under `observed/` form the normalized, Open Finance-inspired estimator input. They contain
account, balance, and transaction observations but omit hidden economic classifications and true
income labels. Files under `private/` contain simulator ground truth for evaluation and must never be
provided to the estimator. `run_manifest.json` records versions, inputs, file counts, and SHA-256
digests.

JSONL files are UTF-8, contain one compact JSON object per line, use stable key ordering, and end each
record with a newline. Dataset ordering is deterministic.

A committed [schema-1.0 seed-42 reference run](examples/generated/salaried_basic_seed_42/run_manifest.json)
anchors legacy byte-for-byte compatibility. The bundled
[schema-1.1 seed-42 reference run](examples/generated/salaried_multi_account_card_seed_42/run_manifest.json)
exercises statement-boundary and uneven-installment behavior.

## Responsibilities

- Generate deterministic scenarios from an explicit seed.
- Emit observations through the same normalized, Open Finance-inspired contract consumed by the
  estimator.
- Model common income patterns and realistic non-income activity as the simulator expands.
- Preserve scenario labels as expected outcomes in private evaluation data.
- Keep estimator-visible observations separate from ground-truth-only attributes.
- Reconcile every ledger posting with its running balance.
- Avoid copying or reconstructing identifiable client records.

## Current limitations

The implemented profiles model one salaried customer and one currency. Schema `1.1` supports active
checking/savings accounts and active credit cards opened at simulation start, fixed limits, zero
opening card debt, deterministic declines, and full automatic invoice payment. It does not model
interest, fees, revolving balances, partial or failed payments, delinquency, refunds, reversals,
loans, investments, variable income, job changes, observation degradation, taxes, overdraft limits,
holidays, or inflation.

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
