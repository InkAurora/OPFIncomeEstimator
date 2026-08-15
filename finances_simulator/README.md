# Finances Simulator

This component generates synthetic financial histories for estimator development, automated tests, and evaluation.

## Responsibilities

- Generate deterministic scenarios from an explicit seed.
- Emit observations through the same normalized, Open Finance-inspired contract consumed by `estimator`.
- Model common income patterns and realistic non-income activity.
- Preserve scenario labels as expected outcomes for evaluation.
- Avoid copying or reconstructing identifiable client records.

## Suggested scenario coverage

- fixed monthly salary;
- variable or commission-based income;
- multiple income sources;
- freelance or irregular income;
- seasonal income;
- job change or income interruption;
- transfers between a client's own accounts;
- refunds, reversals, loans, cash deposits, and other confusing credits;
- sparse data, missing periods, duplicates, and inconsistent descriptions.

Each scenario should document its seed, observation window, expected income range, and important edge cases. Deterministic generation makes failed tests reproducible.

## Boundary

The simulator models categories of data that may be available through Open Finance, but it is not an official API mock and does not aim for exact payload compatibility. Its observation layer should expose estimator-relevant accounts, balances, transactions, cards, loans, and investments through project-owned schemas. Exact provider or Open Finance payloads can be supported later through dedicated adapters.

Simulator output is test data, not evidence of estimator accuracy on the target population. Production readiness still requires evaluation with governed, representative, de-identified data and monitoring for systematic error.

The detailed architecture, milestones, and acceptance criteria are defined in [`docs/implementation-plan.md`](../docs/implementation-plan.md).
