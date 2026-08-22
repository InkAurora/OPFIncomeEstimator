# Estimator

This component estimates a client's income from normalized financial data obtained with consent through Open Finance Brasil.

Simulator Phase 7 now exposes estimator boundary contract `1.0`. An integrated estimator accepts an
immutable observation-only request and returns ordered monthly amounts, confidence bounds, and
contributing transaction IDs. Use `finances-simulator generate-batch --estimator
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

Implement Estimator `0.1` without machine learning:

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

This baseline must establish where and why deterministic reconstruction fails before supervised
models are introduced.

Estimator `0.1.0` now provides this first executable pipeline. It validates a strict local copy of
boundary contract `1.0`, applies point-in-time normalization, emits reason-coded transaction
decisions, groups selected credits into deterministic streams, and reconstructs monthly realized
income with observation-coverage adjustment. It imports no simulator or private-truth module.

Install and test it with:

```bash
cd estimator
python -m pip install -e ".[dev]"
pytest
```

Use it directly from Python:

```python
from income_estimator import RuleBasedIncomeEstimator

estimate = RuleBasedIncomeEstimator().estimate(request)
audit = RuleBasedIncomeEstimator().explain(request)
```

`estimate` matches shared output contract `1.0`. `audit` additionally contains every transaction
classification reason, detected stream, feature version, and artifact version. The JSON CLI accepts
one input-contract file and prints either view:

```bash
income-estimator request.json
income-estimator --audit request.json
```

Run it through the simulator population harness with:

```bash
finances-simulator generate-batch \
  --config ../finances_simulator/configs/scenarios/income_diverse.yaml \
  --seed 100 \
  --population-size 100 \
  --workers 4 \
  --estimator income_estimator:RuleBasedIncomeEstimator \
  --output ../finances_simulator/output/estimator-0.1-baseline
```

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
