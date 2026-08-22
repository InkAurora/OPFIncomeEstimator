# Estimator

This component estimates a client's income from normalized financial data obtained with consent through Open Finance Brasil.

Simulator Phase 7 exposes estimator boundary contracts `1.0` and `1.1`. An integrated estimator
accepts an immutable observation-only request and returns ordered monthly amounts, confidence
bounds, and contributing transaction IDs. Use `finances-simulator generate-batch --estimator
package.module:attribute` to run it over a deterministic population. Contract details live in
[`finances_simulator/docs/contracts-batch-v1.md`](../finances_simulator/docs/contracts-batch-v1.md).

The simulator's `baseline-1.0.0` implementation exists only to exercise this boundary and reporting
pipeline. It is not the estimator proposed by this component.

The estimator will be developed incrementally: observation preprocessing, transaction
intelligence, income-stream detection, monthly cash-flow reconstruction, customer-level capacity
modeling, ensemble estimation, calibrated intervals, confidence, and explainability. See the
[estimator implementation plan](../docs/estimator-implementation-plan.md) for target definitions,
contract changes, milestones, evaluation metrics, and acceptance criteria.

## Responsibilities

- Validate the estimator input contract.
- Identify likely income credits and exclude likely transfers, reversals, refunds, loans, and other non-income movements.
- Detect recurrence, payer consistency, amount stability, and relevant seasonality.
- Produce an estimate for a defined period, such as monthly income.
- Return evidence, assumptions, warnings, and confidence information with the estimate.
- Version estimation behavior so a result can be reproduced and audited.

## Current milestone

Estimator `0.2` adds recurrence-based reconstruction without machine learning:

```text
Observed transactions
        |
        v
TransactionFeatureExtractor
        |
        v
IncomeRuleClassifier
        |
        v
IncomeStreamDetector
        |
        v
MonthlyIncomeReconstructor
```

Estimator `0.1.0` remains frozen as comparison baseline. Estimator `0.2.0` keeps observed classified
income at face value and imputes only evidence-backed gaps from stable streams when measured account
coverage is incomplete. Full-coverage zero months remain zero. Every imputation records amount,
stream IDs, supporting transaction IDs, and reason codes.

Input contract `1.1` is a backward-compatible extension with optional observed counterparty name or
document hash, provider transaction type, transaction balance-after, and balance snapshots. Stream
clustering prefers the document hash, then counterparty name, then exact normalized description.
The simulator adapter maps balances but leaves provider and counterparty fields absent because its
current observation contract does not expose them. Runtime imports no simulator, training, or
private-truth module.

Estimator `0.3` is implemented as an experimental supervised transaction-classifier candidate. Its
training zone extracts point-in-time observed features before joining private synthetic labels,
splits by customer, trains deterministic gradient-boosted decision stumps, and exports a validated
dependency-free JSON artifact. Deterministic exclusions remain non-overridable.

The fixed held-out test result tied the `0.1` classifier baseline at F1 `0.99552372`, precision `1.0`,
and recall `0.99108734`; both recorded zero critical false positives. Because promotion requires a
strict F1 improvement, `0.3` is **not promoted** and `0.2` remains the default. See
[`training/artifacts`](training/artifacts/README.md) for the reproducible artifact and report.

Install and test it with:

```bash
cd estimator
python -m pip install -e ".[dev]"
pytest
```

Use it directly from Python:

```python
from pathlib import Path

from income_estimator import (
    RecurringIncomeEstimator,
    RuleBasedIncomeEstimator,
    SupervisedIncomeEstimator,
)

estimate = RecurringIncomeEstimator().estimate(request)
audit = RecurringIncomeEstimator().explain(request)
baseline = RuleBasedIncomeEstimator().estimate(request)
candidate = SupervisedIncomeEstimator(Path("model.json")).estimate(request)
```

`estimate` matches shared output contract `1.0`. `audit` additionally contains every transaction
classification reason, detected stream, feature version, and artifact version. The JSON CLI accepts
one input-contract file and prints either view:

```bash
income-estimator request.json
income-estimator --audit request.json
income-estimator --baseline-0.1 request.json
income-estimator --model training/artifacts/transaction-classifier-0.3.0.json request.json
```

`--model` is explicit because candidate `0.3` did not pass promotion.

Run it through the simulator population harness with:

```bash
finances-simulator generate-batch \
  --config ../finances_simulator/configs/scenarios/income_diverse.yaml \
  --seed 100 \
  --population-size 100 \
  --workers 4 \
  --estimator income_estimator:RecurringIncomeEstimator \
  --output ../finances_simulator/output/estimator-0.2
```

Frozen held-out results and chart live in
[`evaluation/baselines`](evaluation/baselines/README.md). On 1,200 incomplete-observation
customer-months, MAE falls from `151532.4` to `4000.0` minor units (`97.36%`) without increasing
false-income classifications. Complete income-diverse and life-event suites do not regress.

Target semantics are fixed in
[`docs/adr/0001-income-target-definitions.md`](../docs/adr/0001-income-target-definitions.md).

## Proposed output characteristics

An estimator result should make these points explicit:

- estimated amount and currency;
- estimation period and observation window;
- gross or net interpretation;
- contributing income streams;
- excluded or ambiguous transactions;
- confidence or uncertainty measure;
- estimator version and execution timestamp.

## Boundary

The estimator consumes a stable internal observation contract inspired by the types of data available through Open Finance Brasil. It does not consume or expose an exact copy of official Open Finance API payloads. Provider-specific fields, endpoint nesting, transport metadata, and schema versions must be translated by an adapter before reaching this component.

Authentication, consent management, token storage, raw-provider transport, and official-payload compatibility do not belong in estimation logic.

Do not add real client records to fixtures. Use synthetic data from `finances_simulator` or approved de-identified datasets.
