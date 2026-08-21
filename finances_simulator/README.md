# Finances Simulator

This component generates deterministic synthetic financial histories for estimator development,
automated tests, and evaluation. Engine `0.4.0` adds seven income profiles, multiple volatile and
seasonal sources, and independent behavior and wealth sampling to the full product model. Frozen
engines `0.3.0`, `0.2.0`, and `0.1.0` remain available for older configurations.

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

Run the current schema `1.3` scenario from this directory:

```console
python -m finances_simulator generate --config configs/scenarios/income_diverse.yaml --seed 42 --months 24 --output runs/income-diverse-seed-42
```

Run frozen schema `1.2`, `1.1`, or `1.0` scenarios with:

```console
python -m finances_simulator generate --config configs/scenarios/salaried_loans_investments.yaml --seed 42 --months 24 --output runs/salaried-loans-investments-seed-42
python -m finances_simulator generate --config configs/scenarios/salaried_multi_account_card.yaml --seed 42 --months 24 --output runs/salaried-multi-account-card-seed-42
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

- [`income_diverse.yaml`](configs/scenarios/income_diverse.yaml) uses contract `1.3` and engine
  `0.4.0`: seven weighted income profiles, conditional multi-source bundles, independent behavior
  and wealth axes, and the complete account/card/loan/investment world.
- [`salaried_basic.yaml`](configs/scenarios/salaried_basic.yaml) uses frozen contract `1.0` and
  engine `0.1.0`: one checking account, salary, five fixed expenses, and random variable expenses.
- [`salaried_multi_account_card.yaml`](configs/scenarios/salaried_multi_account_card.yaml) uses
  contract `1.1` and engine `0.2.0`: two institutions and accounts, explicit routing, one monthly
  own transfer, one card, utilization policy, statement invoices, and installment purchases.
- [`salaried_loans_investments.yaml`](configs/scenarios/salaried_loans_investments.yaml) uses
  contract `1.2` and engine `0.3.0`: the schema `1.1` world plus one personal constant-principal
  loan, one fixed-income investment, scheduled contributions and redemption, returns, debt
  snapshots, and private monthly balance sheets.

All schemas reject unknown fields and use one uppercase three-letter currency. Contract `1.2`
retains schema `1.1` requirements, caps institutions, accounts, own transfers, and cards at 32 each,
and adds 1 to 32 loans, 1 to 32 investments, and at least one contribution and redemption schedule.
References, rule IDs, installment work, and scheduled-flow work are bounded and validated before
simulation.

Contract `1.3` replaces the single salary rule with `CustomerFactory`. It samples member index `0`
for a CLI run from weighted income, conditional source-bundle, behavior, and wealth distributions.
The reusable in-memory factory supports addressable samples of up to 100,000 members; Phase 7 still
owns multi-customer history output. Income schedules support five month frequencies, per-attempt
payment probability, symmetric volatility, and 12 calendar-month seasonality factors. All choices
use isolated deterministic streams.

Behavior multipliers scale spending and saving flows; wealth multipliers scale opening deposit and
investment balances. Income realization combines seasonality and volatility in one integer half-up
rounding step. Failed or zero-rounded attempts create no event. Loan disbursements, own transfers,
investment redemptions, and investment returns remain non-income credits or movements, so observed
credit totals are not a perfect true-income formula.

Configured days beyond month end clamp to the final calendar day; weekends and holidays cause no
shift. Variable deposit expenses use an isolated deterministic SHA-256 counter stream. Card purchase
attempts follow their configured month schedule. Purchases exceeding the configured used-limit
ceiling are omitted under `DECLINE` policy.

Loans use constant-principal amortization. Principal remainders go to earliest installments, and
monthly interest is derived from annual basis points with exact integer half-up rounding. First
payment falls in the calendar month after origination; in-window payments use full automatic debit.
Loan disbursement is never income.

Investment contributions debit deposit accounts, redemptions credit them, and month-end returns
change investment value without a deposit posting. Same-day contributions run before redemptions;
an over-redemption is omitted. Returns use integer half-up rounding after dated monthly flows.
Contributions, redemptions, and returns never become true income.

All money uses integer minor units. For example, `650000` BRL represents BRL 6,500.00. Account
balances may become negative because expenses, transfers, card or loan payments, and investment
contributions have no available-funds check.

Exact fields and semantics:

- [contract schema 1.3](docs/contracts-v1-3.md)
- [contract schema 1.2](docs/contracts-v1-2.md)
- [contract schema 1.1](docs/contracts-v1-1.md)
- [frozen contract schema 1.0](docs/contracts-v1.md)

## Output layout and trust boundary

Schema `1.3` emits the schema `1.2` observed tree plus private income-source truth:

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
|   |-- credit_card_invoice_items.jsonl
|   |-- loans.jsonl
|   |-- loan_payments.jsonl
|   |-- loan_balances.jsonl
|   |-- investments.jsonl
|   |-- investment_transactions.jsonl
|   `-- investment_balances.jsonl
`-- private/
    |-- customer_ground_truth.jsonl
    |-- customer_month_ground_truth.jsonl
    |-- income_source_ground_truth.jsonl
    |-- transaction_ground_truth.jsonl
    |-- credit_card_transaction_ground_truth.jsonl
    |-- loan_payment_ground_truth.jsonl
    |-- investment_transaction_ground_truth.jsonl
    `-- balance_sheet_ground_truth.jsonl
```

Schemas `1.2`, `1.1`, and `1.0` retain their original version-specific dataset trees.

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
exercises statement-boundary and uneven-installment behavior. The frozen
[schema-1.2 seed-42 reference run](examples/generated/salaried_loans_investments_seed_42/run_manifest.json)
adds 24-month loan, investment, and net-worth reconciliation.
The current [schema-1.3 seed-42 reference run](examples/generated/income_diverse_seed_42/run_manifest.json)
samples a mixed-income, balanced-behavior, high-wealth customer with two variable sources.

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

Schema `1.3` generates one sampled customer and one currency per run. It supports seven stationary
income profiles and variable calendar-month receipts alongside active checking/savings accounts,
fixed-policy cards, personal constant-principal loans, and fixed-income investments. Loan and card
payments are always full and may make deposit balances negative. The simulator does not yet write
batch populations or model revolving cards, loan default or prepayment, investment units or market
prices, fees, taxes, negative returns, job changes, observation degradation, overdraft limits,
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
