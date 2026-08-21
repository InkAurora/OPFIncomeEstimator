# Finances Simulator

This component generates deterministic synthetic financial histories for estimator development,
automated tests, and evaluation. Phase-7 orchestrator `0.7.0` adds deterministic parallel
populations, partitioned Parquet datasets, a versioned estimator boundary, and automatic evaluation
reports. It runs frozen engine `0.6.0`/contract `1.5` members without changing their economics or
record schemas. Older engines `0.5.0` through `0.1.0` remain available.

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

Run the current schema `1.5` scenario from this directory:

```console
python -m finances_simulator generate --config configs/scenarios/incomplete_observation.yaml --seed 42 --months 12 --output runs/incomplete-observation-seed-42
```

Run frozen schema `1.4`, `1.3`, `1.2`, `1.1`, or `1.0` scenarios with:

```console
python -m finances_simulator generate --config configs/scenarios/life_events.yaml --seed 42 --months 24 --output runs/life-events-seed-42
python -m finances_simulator generate --config configs/scenarios/income_diverse.yaml --seed 42 --months 24 --output runs/income-diverse-seed-42
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

## Generate and evaluate a population

```console
python -m finances_simulator generate-batch --config configs/scenarios/incomplete_observation.yaml --seed 100 --population-size 1000 --months 12 --workers 8 --partitions 64 --output runs/population-1000
```

Member `n` uses seed `--seed + n`. `--workers` changes throughput only: population identity,
records, Parquet bytes, manifests, estimates, and reports remain unchanged. `--partitions` selects
the stable SHA-256 customer bucket count. Batch generation validates every Pydantic record again at
the simulation boundary, then validates estimator identity, months, currency, and evidence links.

The default `baseline` estimator is a transparent integration harness. It excludes visible
duplicate/reversal lineage, observed own-transfer pairs, loan disbursement links, and investment
redemption links. It is not a production income model. Integrate another estimator with
`--estimator package.module:attribute`; the attribute may be a no-argument class, an object exposing
`estimate(request)`, or a callable. Results must satisfy estimator contract `1.0`.

## Configuration behavior

The loader dispatches on the required top-level `schema_version`:

- [`incomplete_observation.yaml`](configs/scenarios/incomplete_observation.yaml) uses contract `1.5`
  and engine `0.6.0`: standard 100%, 70%, and 40% consent, account overrides, provider
  descriptions, and deterministic missing, late, duplicate, and reversal records.
- [`life_events.yaml`](configs/scenarios/life_events.yaml) uses contract `1.4` and engine `0.5.0`:
  income and household transitions, exceptional expenses and inflows, seasonal multipliers, and
  four correctly typed anomaly classes.
- [`income_diverse.yaml`](configs/scenarios/income_diverse.yaml) uses frozen contract `1.3` and engine
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

Contract `1.5` separates the frozen V4 world fingerprint from V5 observation policy. Changing only
consent or degradation settings preserves every hidden event, ledger posting, balance, product
state, and private truth row. Default and institution coverage applies to all dated product
streams; account coverage overrides its institution for deposit balances and transactions.

Deposit transactions receive independently configured `0..10000` basis-point rates for missing,
late, duplicate, and reversal records. `observed_at` measures delayed arrival. Duplicate and
reversal records have unique IDs and reference their emitted original without exposing private
labels. Provider prefixes format deposit, card, and investment descriptions. Per-account coverage
rows and an aggregate manifest summary measure effective coverage using original records only.

Contract `1.3` replaces the single salary rule with `CustomerFactory`. It samples member index `0`
for a CLI run from weighted income, conditional source-bundle, behavior, and wealth distributions.
The reusable in-memory factory supports addressable samples of up to 100,000 members; Phase 7
provides multi-customer history output. Income schedules support five month frequencies, per-attempt
payment probability, symmetric volatility, and 12 calendar-month seasonality factors. All choices
use isolated deterministic streams.

Contract `1.4` adds `initial_life_state`, two 12-month scenario seasonality vectors,
`life_events`, and `anomalies`. Income transitions retain one materialized source ID while changing
its effective active flag, base amount, payer, or description. A transition takes effect before
financial activity on its effective date. Private truth records customer and source state
immediately before and after every transition, including comparable annualized base-income totals.

Recurring source income combines source seasonality, scenario seasonality, and volatility in one
integer half-up realization. Scenario expense multipliers apply to recurring deposit expenses and
configured card-purchase occurrences. Explicit life-event and anomaly amounts are not seasonally
scaled. Large PIX anomalies are balanced own-account transfers; refunds and asset sales are
non-income credits; anomalous investment redemptions reconcile investment and deposit balances.

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

- [batch and estimator contract 1.0](docs/contracts-batch-v1.md)
- [contract schema 1.5](docs/contracts-v1-5.md)
- [contract schema 1.4](docs/contracts-v1-4.md)
- [contract schema 1.3](docs/contracts-v1-3.md)
- [contract schema 1.2](docs/contracts-v1-2.md)
- [contract schema 1.1](docs/contracts-v1-1.md)
- [frozen contract schema 1.0](docs/contracts-v1.md)

## Output layout and trust boundary

Batch output is separate from single-run JSONL and uses this layout:

```text
<output>/
|-- population_manifest.json
|-- observed/<dataset>/customer_bucket=<nn>/part-00000.parquet
|-- private/<dataset>/customer_bucket=<nn>/part-00000.parquet
`-- evaluation/
    |-- estimates/customer_bucket=<nn>/part-00000.parquet
    `-- report.json
```

Each populated Parquet row adds `batch_id`, `run_id`, `seed`, and `customer_bucket` provenance.
Nested open objects are canonical JSON columns; primitive repeated fields remain Parquet lists.
Manifest file hashes and embedded Arrow metadata make component-boundary verification explicit.
See [batch contract 1.0](docs/contracts-batch-v1.md) for metric definitions and trust rules.

Schema `1.5` retains the schema `1.4` tree and adds
`observed/observation_coverage.jsonl`. Transaction rows add arrival date and nullable duplicate and
reversal lineage links. Private datasets retain complete undegraded truth.

Schema `1.4` emits the schema `1.3` tree plus private life-event and anomaly truth:

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
    |-- life_event_ground_truth.jsonl
    |-- anomaly_ground_truth.jsonl
    |-- transaction_ground_truth.jsonl
    |-- credit_card_transaction_ground_truth.jsonl
    |-- loan_payment_ground_truth.jsonl
    |-- investment_transaction_ground_truth.jsonl
    `-- balance_sheet_ground_truth.jsonl
```

Schemas `1.4`, `1.3`, `1.2`, `1.1`, and `1.0` retain their original version-specific dataset trees.

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
The frozen [schema-1.3 seed-42 reference run](examples/generated/income_diverse_seed_42/run_manifest.json)
samples a mixed-income, balanced-behavior, high-wealth customer with two variable sources. The
frozen [schema-1.4 seed-42 reference run](examples/generated/life_events_seed_42/run_manifest.json)
exercises all life-event and anomaly types across a 24-month salaried history.
The current
[schema-1.5 seed-42 reference run](examples/generated/incomplete_observation_seed_42/run_manifest.json)
exercises all three coverage levels and every deposit-transaction degradation type.
The Phase-7
[two-member population reference](examples/generated/phase7_population_seed_100_count_2/population_manifest.json)
anchors deterministic Parquet, estimator-boundary, and evaluation output.

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

Each source run still generates one sampled customer and one currency; Phase 7 composes these runs
into populations. Contract `1.5` supports effective-dated
changes to existing materialized income sources and household state alongside active
checking/savings accounts, fixed-policy cards, personal constant-principal loans, and fixed-income
investments. Property and vehicle ownership are counts, not valued balance-sheet assets. Loan and
card payments are always full and may make deposit balances negative. The simulator does not model
revolving cards, loan default or prepayment, investment units or
market prices, fees, taxes, negative returns, observation degradation outside deposit transactions,
consent lifecycle timestamps, overdraft limits, holidays, or inflation.

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
