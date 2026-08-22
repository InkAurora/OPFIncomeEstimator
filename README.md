# Open Finance Income Estimator

Income-estimation project powered by a client's financial data from [Open Finance Brasil](https://openfinancebrasil.org.br/).

The project aims to transform consented financial data into explainable income estimates. A separate simulator can generate representative financial histories so estimator behavior can be developed and evaluated without depending on real client data.

## Project structure

```text
.
|-- docs/                  # Architecture and implementation plans
|-- estimator/             # Income-estimation logic and interfaces
`-- finances_simulator/    # Synthetic financial-data generation
```

- [`estimator`](estimator/README.md) consumes normalized financial observations and produces an estimate with supporting evidence and confidence information.
- [`finances_simulator`](finances_simulator/README.md) creates synthetic client scenarios for development, testing, and validation.
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
schema validation at component boundaries, estimator contract `1.0`, and automatic evaluation.
Its members use frozen engine `0.6.0` and observation contract `1.5`, including consent coverage and
observation artifacts. Contracts `1.4` through `1.0` remain supported with byte-stable reference
outputs. A transparent baseline exercises integration; production estimator logic and provider
adapters remain unimplemented. The next milestone is the rule-based estimator baseline defined in
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

Use `configs/scenarios/life_events.yaml` for frozen schema `1.4`,
`configs/scenarios/income_diverse.yaml` for frozen schema `1.3`,
`configs/scenarios/salaried_loans_investments.yaml` for frozen schema `1.2`,
`configs/scenarios/salaried_multi_account_card.yaml` for frozen schema `1.1`, or
`configs/scenarios/salaried_basic.yaml` for frozen schema `1.0`. See
[`finances_simulator/README.md`](finances_simulator/README.md) for every profile, output contract,
and current limitations.

## Getting started

1. Read the component documentation.
2. Generate a versioned deterministic income-diverse scenario.
3. Implement estimator rules against the versioned observation contract.
4. Extend simulator scenarios only after preserving reconciliation and leakage tests.
5. Validate with de-identified, consented data before any production use.

## Security and data handling

Keep secrets in an approved secret manager or local environment variables. Keep personal and financial data out of Git. Use synthetic or properly de-identified fixtures for tests, and define retention and deletion rules before ingesting production data.

This software should be treated as decision-support infrastructure. Its estimates require validation, monitoring, and appropriate human or policy controls before they are used in consequential financial decisions.
