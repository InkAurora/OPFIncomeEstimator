# Open Finance Income Estimator

Income-estimation project powered by a client's financial data from [Open Finance Brasil](https://openfinancebrasil.org.br/).

The project aims to transform consented financial data into explainable income estimates. A separate simulator can generate representative financial histories so estimator behavior can be developed and evaluated without depending on real client data.

## Project structure

```text
.
|-- demo_app/              # Single-process Streamlit demo of the whole flow
|-- docs/                  # Architecture and implementation plans
|-- estimator/             # Income-estimation logic and interfaces
`-- finances_simulator/    # Synthetic financial-data generation
```

- [`estimator`](estimator/README.md) consumes normalized financial observations and produces an estimate with supporting evidence and confidence information.
- [`finances_simulator`](finances_simulator/README.md) creates synthetic client scenarios for development, testing, and validation.
- [`demo_app`](demo_app/README.md) runs the simulator and the promoted estimator together in one
  Streamlit page, for demonstration rather than for production use.
- [`docs/implementation-plan.md`](docs/implementation-plan.md) defines the simulator implementation plan.
- [`docs/estimator-implementation-plan.md`](docs/estimator-implementation-plan.md) defines the estimator architecture, model progression, evaluation strategy, and acceptance criteria.

## Intended data flow

```text
Open Finance Brasil data or synthetic scenario
                    |
                    v
          validation and normalization
                    |
                    v
             income estimator
                    |
                    v
       estimate + evidence + confidence
```

## Design principles

- **Consent and purpose limitation:** process only data authorized by the client and required for the estimate.
- **Privacy by design:** avoid committing real client data, credentials, access tokens, or raw API responses.
- **Explainability:** retain the observations and assumptions that support each estimate.
- **Reproducibility:** version estimation rules and simulator scenarios so results can be reproduced.
- **Uncertainty awareness:** distinguish recurring income from transfers, refunds, loans, and other non-income credits; report confidence rather than presenting every result as exact.

## Project conventions

All project-authored content must be written in English, including source code, identifiers, schemas, configuration keys, documentation, tests, logs, commit messages, issues, and pull requests. Team discussion may happen in another language without changing this repository convention. Raw external values, realistic transaction descriptions, official names such as Open Finance Brasil, and Brazilian financial terms such as PIX may retain their original form when translation would alter the represented data.

The shared data contract is **Open Finance-inspired, not Open Finance wire-compatible**. It models the useful categories of information available through Open Finance, such as accounts, balances, transactions, cards, loans, and investments, but does not reproduce official endpoint payloads, field names, nesting, or versioning exactly. Provider-specific payloads will be handled by adapters outside the estimator and simulator domain models.

See [CONTRIBUTING.md](CONTRIBUTING.md) for repository-wide language and data-contract rules.

## Development status

Simulator orchestrator `0.7.0` implements deterministic parallel populations, partitioned Parquet,
schema validation at component boundaries, estimator contracts `1.0` and `1.1`, and automatic
evaluation.
Its members use frozen engine `0.6.0` and observation contract `1.5`, including consent coverage and
observation artifacts. Contracts `1.4` through `1.0` remain supported with byte-stable reference
outputs. Estimator `0.2.0` adds evidence-backed recurring-stream reconstruction to the frozen
`0.1.0` rule baseline. Held-out incomplete-observation MAE improves `97.36%` without complete-data
regression or added false-income classifications. Input contract `1.1` now accepts optional observed
counterparty, provider transaction type, transaction-balance, and balance records. An isolated,
customer-split transaction-classifier pipeline produced experimental candidate `0.3.0`; it tied the
rule baseline on held-out F1 (`0.99552372`) with zero critical false positives, so the promotion gate
kept `0.2.0` as the default. Estimator `0.4` adds feature set `customer-month-features-1.1.0`: a
versioned, point-in-time table of `98` features per `customer_id` and `reference_month`, covering
rolling cash flow, income stability, source structure, coverage, account activity, observed balance,
loan, and investment context, and card, credit, and investment capacity. Each reference month
replays the promoted `0.2` estimator on a request narrowed to that month's cutoff, so no feature can
read a later arrival. Both `0.5` prerequisites are now met.
Private contract `income-targets-1.0` projects all five income targets from the hidden simulation
run, so `sustainable_monthly_income` exists as a trainable label; its construction is fixed by
[`docs/adr/0002-income-target-construction.md`](docs/adr/0002-income-target-construction.md).
Estimator input `1.2` adds observed credit cards, limits, card transactions, invoices, loan
payments, loan balances, investments, and investment balances, so feature set
`customer-month-features-1.1.0` computes the capacity group instead of declaring it unavailable.
Estimator `0.5` adds a promoted capacity estimator for `sustainable_monthly_income`: a hurdle model
whose logistic gate decides whether capacity is zero and whose anchored regressor sizes it
otherwise. On held-out data it improves mean absolute error from `55,455` to `25,055` minor units
against the best deterministic baseline, improves both full-coverage and partial-consent segments,
and predicts zero-income customers exactly. Estimator output `1.1` separates realized from sustainable income and carries component estimates,
disagreement, confidence, and excluded evidence without disturbing any `1.0` field. Estimator `0.6`
routes both targets deterministically and reaches held-out MAE `21,227` against `23,236` for its
best individual component. Estimator `0.7` fills those quantiles with split-conformal intervals calibrated on out-of-fold
residuals, reaching held-out coverage `0.8365` against a nominal `0.80` with confidence monotonic
against relative error. Estimator `0.8` adds explanation contract `1.0` with exact feature contributions, model cards for
every promoted artifact, and six separately reported stress suites. Two suites sit outside the
training distribution and expose the current limits: noisy observation gives the worst realized
error, and high-volatility income gives the worst sustainable error with interval coverage of
`0.375` against a nominal `0.80`. Every milestone in the estimator plan is now implemented. Provider adapters must still populate the optional
counterparty context before counterparty-aware gains can be measured. See
[`docs/estimator-implementation-plan.md`](docs/estimator-implementation-plan.md).

## Simulator quick start

```bash
cd finances_simulator
python -m pip install -c constraints-dev.txt -e ".[dev]"
finances-simulator generate \
  --config configs/scenarios/incomplete_observation.yaml \
  --seed 42 \
  --months 12 \
  --output output/incomplete-observation-seed-42
finances-simulator generate-batch \
  --config configs/scenarios/incomplete_observation.yaml \
  --seed 100 \
  --population-size 100 \
  --workers 4 \
  --output output/population-100
pytest
```

Use `configs/scenarios/noisy_observation.yaml` or `configs/scenarios/high_volatility.yaml` for the
estimator stress suites, `configs/scenarios/life_events.yaml` for frozen schema `1.4`,
`configs/scenarios/income_diverse.yaml` for frozen schema `1.3`,
`configs/scenarios/salaried_loans_investments.yaml` for frozen schema `1.2`,
`configs/scenarios/salaried_multi_account_card.yaml` for frozen schema `1.1`, or
`configs/scenarios/salaried_basic.yaml` for frozen schema `1.0`. See
[`finances_simulator/README.md`](finances_simulator/README.md) for every profile, output contract,
and current limitations.

## Demo quick start

One page that runs the whole flow: pick a client profile, a seed, and a history length, and the
simulator generates a hidden financial life, the estimator answers from its consented projection
alone, and the private truth is joined afterwards to score the answer.

```bash
python -m streamlit run demo_app/app.py
```

It runs the promoted pair exactly: capacity model `capacity-gbdt-stumps-0.6.0` and interval
calibration `conditional-selector-intervals-0.11.0`, under estimator `ensemble-0.6.0`. Two of the
five profiles are the documented weak cases, and the page shows their interval coverage falling
below nominal rather than hiding it. See [`demo_app/README.md`](demo_app/README.md).

## Getting started

1. Read the component documentation.
2. Generate a versioned deterministic income-diverse scenario.
3. Implement estimator rules against the versioned observation contract.
4. Extend simulator scenarios only after preserving reconciliation and leakage tests.
5. Validate with de-identified, consented data before any production use.

## Security and data handling

Keep secrets in an approved secret manager or local environment variables. Keep personal and financial data out of Git. Use synthetic or properly de-identified fixtures for tests, and define retention and deletion rules before ingesting production data.

This software should be treated as decision-support infrastructure. Its estimates require validation, monitoring, and appropriate human or policy controls before they are used in consequential financial decisions.
