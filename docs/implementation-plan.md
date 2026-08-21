# Financial Simulator Implementation Plan

## 1. Objective

Build a deterministic, causal, and auditable simulator of individual financial lives over time. The simulator first creates a complete hidden financial reality, then derives the observations that a financial institution could see. This preserves known ground truth for training and evaluating the income estimator.

The implementation should initially target 24-month simulations and allow longer timelines later.

### Current implementation status

Phase 6's implementation slice is available as engine `0.6.0` with contract schema `1.5`. The
bundled `incomplete_observation` scenario adds consent coverage and deterministic observation
artifacts while preserving frozen V4 economics and private truth. Engines `0.5.0` through `0.1.0`
and contracts `1.4` through `1.0` remain available with byte-stable reference outputs. See
[`contracts-v1-5.md`](../finances_simulator/docs/contracts-v1-5.md),
[`contracts-v1-4.md`](../finances_simulator/docs/contracts-v1-4.md),
[`contracts-v1-3.md`](../finances_simulator/docs/contracts-v1-3.md),
[`contracts-v1-2.md`](../finances_simulator/docs/contracts-v1-2.md),
[`contracts-v1-1.md`](../finances_simulator/docs/contracts-v1-1.md) and
[`contracts-v1.md`](../finances_simulator/docs/contracts-v1.md).

## 2. Architectural decisions

### 2.1 Three data levels

The architecture has three explicit levels:

1. **World state:** the complete hidden state of a synthetic customer.
2. **Financial events:** economic events and their account-level ledger effects.
3. **Observed financial data:** the incomplete, potentially noisy view exposed to the estimator.

```text
CustomerFactory
      |
      v
CustomerTwin / World State
      |
      v
Simulation Engine
      |
      v
Financial Events ---> Ground Truth
      |
      v
Account Ledger
      |
      v
Observation Layer ---> Open Finance-inspired data ---> Income Estimator
```

`CustomerTwin` must not create observed records directly. This separation allows descriptions, missing records, partial consent, duplicates, and institution-specific behavior to change without corrupting the underlying financial reality.

### 2.2 Project language

All project-authored artifacts are written in English. This includes code, identifiers, schemas, configuration, documentation, tests, diagnostics, and repository collaboration artifacts. Portuguese discussion is allowed, but committed project content remains English.

Raw or synthetic values may remain in Portuguese when their original language is part of the represented data. Examples include a transaction description such as `PGTO EMPRESA ABC` and established terms such as PIX.

### 2.3 Open Finance-inspired contract

The observed data contract should follow the **types of financial data available through Open Finance**, not the exact official API format.

The contract should cover useful domain categories such as:

- accounts and balances;
- account transactions;
- credit cards, limits, invoices, and card transactions;
- loans and repayment information;
- investments, contributions, returns, and redemptions.

The internal contract intentionally owns its field names, nesting, normalization, and version lifecycle. It should remain compact and estimator-oriented. It must not claim exact Open Finance compatibility.

Future ingestion of official or provider-specific payloads should use dedicated adapters:

```text
Official/provider payload
          |
          v
Provider adapter
          |
          v
Project-owned observation contract
          |
          v
Income estimator
```

### 2.4 Determinism and financial consistency

- The same configuration, seed, and simulator version must produce the same financial result.
- Monetary values must use integer minor units or `Decimal`, never binary floating point.
- Generated identifiers must be deterministic within a run.
- Account balances, card invoices, debt balances, and net worth must reconcile.
- Ground truth and observed data must be written separately.

## 3. Target package structure

The plan assumes Python 3.12 or newer unless Phase 0 records a different decision.

```text
finances_simulator/
|-- pyproject.toml
|-- configs/
|   |-- scenarios/
|   `-- distributions/
|-- src/finances_simulator/
|   |-- domain/
|   |   |-- customer.py
|   |   |-- accounts.py
|   |   |-- income.py
|   |   |-- expenses.py
|   |   |-- cards.py
|   |   |-- loans.py
|   |   |-- investments.py
|   |   `-- events.py
|   |-- factory/
|   |-- simulation/
|   |-- ledger/
|   |-- ground_truth/
|   |-- observations/
|   |-- outputs/
|   |-- validation/
|   `-- cli.py
`-- tests/
    |-- unit/
    |-- integration/
    |-- properties/
    `-- golden/
```

Suggested initial tools are Pydantic for validated domain/configuration models, NumPy `Generator` for controlled randomness, PyArrow with Polars or Pandas for datasets, and Pytest with Hypothesis for tests. These choices should be confirmed during Phase 0.

## 4. Core domain contracts

### 4.1 `CustomerTwin`

The hidden customer state contains:

```text
identity
socioeconomic_profile
employment
income_sources
expenses
assets
liabilities
bank_relationships
cards
investments
loans
behavioral_profile
life_events
```

Latent attributes such as true income, wealth, financial stability, spending propensity, and job stability never appear in estimator input.

### 4.2 `FinancialEvent`

Minimum proposed fields:

```text
event_id
customer_id
occurred_at
economic_type
amount
currency
source_entity
destination_entity
income_source_id
caused_by_event_id
metadata
```

Initial economic types:

```text
INCOME
OWN_TRANSFER
EXPENSE
INVESTMENT_CONTRIBUTION
INVESTMENT_REDEMPTION
INVESTMENT_RETURN
LOAN_DISBURSEMENT
LOAN_PAYMENT
REFUND
REVERSAL
GIFT
ASSET_SALE
CARD_PAYMENT
OTHER
```

The economic type belongs to ground truth. It must not be copied into the estimator's transaction view.

### 4.3 `LedgerEntry`

Minimum proposed fields:

```text
entry_id
event_id
account_id
posted_at
direction
amount
balance_after
transfer_group_id
description
```

An own-account transfer creates paired debit and credit entries linked by one `transfer_group_id`. This permits balance reconciliation and prevents the transfer from becoming artificial income.

### 4.4 Output datasets

Private simulator output:

```text
run_manifest
customer_ground_truth
customer_month_ground_truth
transaction_ground_truth
credit_card_transaction_ground_truth
loan_payment_ground_truth
investment_transaction_ground_truth
balance_sheet_ground_truth
```

Observed project-owned output:

```text
accounts
balances
transactions
credit_cards
credit_card_transactions
credit_limits
credit_card_invoices
credit_card_invoice_items
loans
loan_payments
loan_balances
investments
investment_transactions
investment_balances
```

Exact implemented fields live in the versioned `1.0`, `1.1`, and `1.2` contract documents.

## 5. Delivery phases

### Phase 0 — Foundation

Implement:

- Python package and `src` layout;
- validated YAML scenario configuration;
- seeded random-number infrastructure;
- deterministic identifiers and simulation clock;
- schema versioning and run manifest;
- basic command-line interface;
- initial world-state, event, ledger, truth, and observation contracts.

Target command:

```bash
finances-simulator generate \
  --config configs/scenarios/salaried_basic.yaml \
  --seed 42 \
  --months 24 \
  --output output/
```

Acceptance criteria:

- package installs in a clean environment;
- invalid configuration fails with an actionable error;
- identical inputs produce identical financial outputs;
- private and observed schemas are physically separated.

### Phase 1 — V0: basic salaried customer

Implement:

- one salaried `CustomerTwin`;
- one checking account;
- one monthly salary source;
- fixed and variable expenses;
- dated simulation across 24 months;
- ledger, balances, monthly truth, and observed accounts/transactions/balances;
- one manually configured YAML scenario.

Acceptance criteria:

- salary occurs on its configured schedule;
- true income contains only economic income;
- every account reconciles from opening balance through closing balance;
- the scenario is reproducible from its seed;
- the estimator receives no latent fields.

### Phase 2 — V1: multiple accounts and cards — implemented

Engine `0.2.0` and schema `1.1` implement this bounded slice. Current card policy is fixed-limit,
deterministic decline, and full automatic payment; revolving credit, interest, fees, and failed or
partial payments remain future work.

Implement:

- multiple fictional institutions and accounts;
- salary and primary-account routing;
- own-account transfers;
- cards, limits, statement cycles, due dates, and invoices;
- installment purchases.

Acceptance criteria:

- transfers create paired ledger entries;
- own transfers never increase true income;
- card purchases reconcile with invoices;
- credit utilization follows the configured policy.

### Phase 3 — V2: loans and investments — implemented

Engine `0.3.0` and schema `1.2` implement this bounded slice. Loans use fixed nominal annual rates,
constant-principal amortization, calendar-month schedules, integer half-up interest, and full
automatic payment. Investments use money-valued fixed-income balances, scheduled contributions and
redemptions, deterministic non-negative monthly returns, and silent decline of over-redemptions.
Monthly private balance sheets reconcile deposits and investments against card and loan debt.

Implement:

- loan origination, disbursement, interest, installments, and remaining balance;
- investment accounts, contributions, returns, and redemptions;
- monthly balance sheet and net worth.

Acceptance criteria:

- loan disbursement and investment redemption are not income;
- debt balances follow their amortization schedules;
- investment balances reconcile with contributions, returns, and redemptions;
- net worth equals assets minus liabilities.

### Phase 4 — V3: income diversity and population factory

Engine `0.4.0` and schema `1.3` implement this bounded slice. The CLI samples one addressable
factory member and writes one complete history; Phase 7 retains batch population output. Income
profiles select conditional source bundles, while behavior and opening wealth use independent
weighted dimensions. Receipt attempts support calendar frequency, probability, symmetric
volatility, and monthly seasonality with one-step integer half-up realization.

Implement:

- salaried, self-employed, business-owner, retired, investor, mixed, and unemployed income profiles;
- multiple income sources, volatility, frequency, and seasonality;
- independent behavioral and wealth dimensions;
- `CustomerFactory` with configurable conditional distributions.

Acceptance criteria:

- sampled population approximates configured distributions;
- unusual combinations are possible but controlled;
- no single observed feature reveals income through a perfect formula;
- zero-income and highly variable customers are supported.

### Phase 5 — V4: life events, seasonality, and anomalies

Engine `0.5.0` and schema `1.4` implement this bounded slice. Life events are configured with
effective dates and produce continuous before/after customer and income-source state. One-off cash
events and anomalies remain ordinary reconciled financial events; their causal labels are emitted
only in private truth. Calendar multipliers layer over source income seasonality and recurring
deposit/card expenses.

Implement:

- raises, promotions, job loss, and job changes;
- marriage, divorce, dependents, property and vehicle purchases;
- bonuses, inheritance, medical expenses, and vacations;
- seasonal income and expense multipliers;
- labeled anomalies such as large PIX transfers, refunds, asset sales, and redemptions.

Acceptance criteria:

- events change state only from their effective date;
- truth captures income before and after transitions;
- anomalies retain their correct economic type;
- deterministic behavior remains intact.

### Phase 6 — V5: incomplete observation — implemented

Engine `0.6.0` and schema `1.5` implement this bounded slice. Frozen V4 economics run from a
separate world fingerprint, so changing consent or degradation policy cannot change hidden events,
ledger postings, balances, or private truth. Standard 100%, 70%, and 40% coverage applies by
institution with account overrides. Deterministic missing, late, duplicate, and reversal injection
acts on deposit-transaction observations; consent also filters every dated product stream.

Implement:

- consent coverage by institution or account;
- standard 100%, 70%, and 40% coverage scenarios;
- institution-specific descriptions;
- missing, late, duplicated, and reversed records;
- configurable observation degradation.

Acceptance criteria:

- degradation changes observed data only;
- world state and ground truth remain unchanged;
- effective coverage is measurable;
- duplicates and reversals remain traceable without leaking truth labels.

### Phase 7 — Scale and estimator integration

Implement:

- batch population generation;
- deterministic parallel execution;
- partitioned Parquet output;
- schema validation at component boundaries;
- direct integration with `estimator` through the shared contract;
- automatic evaluation reports.

Evaluation should include:

- mean and median absolute error;
- error by income type and income range;
- error by consent coverage;
- error around life events;
- false classification of transfers, loans, and redemptions as income;
- confidence-interval coverage.

## 6. Test strategy

- **Unit tests:** rules for each domain model and engine.
- **Integration tests:** complete scenario from configuration through observed output.
- **Golden tests:** small versioned scenarios with expected stable results.
- **Property-based tests:** financial invariants across many generated seeds.
- **Distribution tests:** generated populations follow configured ranges and dependencies.
- **Leakage tests:** truth-only columns cannot reach estimator input.
- **Reproducibility tests:** stable result hashes for the same version, configuration, and seed.
- **Reconciliation tests:** accounts, invoices, debt, investments, and net worth balance correctly.

Core invariants:

```text
closing_balance = opening_balance + credits - debits
net_worth = assets - liabilities
true_income = sum(events where economic_type = INCOME)
observed_accounts are a subset of consented_accounts
```

## 7. First implementation slice

The completed first delivery contained only:

- one salaried customer;
- one checking account;
- monthly salary;
- five fixed-expense rules;
- variable expenses;
- 24 simulated months;
- deterministic seed;
- observed accounts, balances, and transactions;
- customer-month and transaction ground truth;
- command-line generation;
- reconciliation, leakage, and reproducibility tests.

Cards were outside that first slice and were added by Phase 2. Loans, investments, and net worth
were added by Phase 3, income diversity by Phase 4, effective-dated life events by Phase 5, and
incomplete observations by Phase 6.
Batch population generation and observation degradation remain outside implemented scope. The
first slice proved the three-level architecture before domain expansion.

## 8. Definition of done

A phase is complete only when:

- its schemas are versioned and documented;
- all authored content uses English;
- ground truth is isolated from observed data;
- deterministic tests pass;
- applicable financial invariants reconcile;
- example configuration and generated sample are available;
- component documentation reflects implemented behavior;
- no real client data or credentials are present.
