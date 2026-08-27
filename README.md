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

`0.11` is a **research baseline, not a production release.** Every number below was measured against
the synthetic simulator in this repository. Nothing has been validated against real consented client
data, and no Open Finance provider adapter exists yet.

### Simulator

Package `0.8.0`. The Phase-7 orchestrator `0.7.0` generates deterministic parallel populations,
partitioned Parquet, schema validation at component boundaries, and automatic evaluation reports.
Its members run frozen engine `0.7.0` on observation contract `1.6`, which adds mandatory reversal
re-posts. Engine profiles `0.6.0` through `0.1.0`, on contracts `1.5` through `1.0`, remain
selectable; every contract except `1.5` also carries a committed byte-stable reference run under
[`finances_simulator/examples/generated`](finances_simulator/examples/generated).

### Estimator

Package `0.11.0`. One estimate is produced by three artifacts read together:

| Role | Artifact | Version |
|---|---|---|
| Realized monthly income | frozen rules | `recurring-streams-0.2.0` |
| Sustainable monthly income | `capacity-estimator-0.6.0.json` | `capacity-gbdt-stumps-0.6.0` |
| Interval around it | `quantile-calibration-0.11.0.json` | `conditional-selector-intervals-0.11.0` |
| Routing between them | deterministic | `ensemble-0.6.0` |
| Features | 104 point-in-time features | `customer-month-features-1.2.0` |

It accepts input contracts `1.0` through `1.2`, returns output contract `1.1`, and explains itself
under explanation contract `1.0`. The capacity and calibration artifacts are a bound pair: the
calibration records the capacity `model_version` and the SHA-256 of its exact bytes, and the runtime
refuses any other combination.

Measured on held-out synthetic populations: realized-income reconstruction improves
incomplete-observation MAE `99.25%` over the frozen `0.1` rule baseline with no complete-data
regression and no added false-income classification. Routed MAE is `21,227` minor units against
`23,236` for the best individual component. The promoted interval covers `0.9050` against a nominal
`0.80` on validation, publishing `8,635` of `8,640` rows, and confirmed on a release lockbox read
once.

### What this does not yet cover

- **No real-data evidence.** Every result is synthetic. Synthetic WAPE supports no production claim.
- **No provider adapter.** Consented Open Finance payloads cannot reach the estimator yet, and the
  optional counterparty fields of input `1.1` stay empty, so stream clustering falls back to
  description matching.
- **Intervals do not survive new income conditions.** On the two held-out stress suites the
  promoted interval covers `0.348` and `0.158` against a nominal `0.80` while still publishing `93%`
  and `98%` of its rows, and mean confidence does not fall to match. The support envelope abstains
  only on `OUT_OF_CALIBRATED_SUPPORT`, and its nine independent per-feature range checks are far too
  permissive to catch these rows. See
  [`estimator/evaluation/baselines`](estimator/evaluation/baselines/README.md).
- **High-volatility sustainable income is weak.** Sustainable WAPE `0.410` on that suite.
- **No annual quantiles.** `annual_income_p10/p50/p90` stay absent because the dependence structure
  across months has not been measured, and multiplying monthly quantiles by twelve would invent one.

See [`docs/estimator-implementation-plan.md`](docs/estimator-implementation-plan.md) for target
definitions and acceptance criteria, and
[`estimator/docs/model-cards.md`](estimator/docs/model-cards.md) for each artifact's measured
results and known failure modes.

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
