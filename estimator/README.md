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

Estimator `0.4` adds a point-in-time customer-month feature table on top of the promoted `0.2`
reconstruction. Estimator `0.2` remains the default estimate producer:

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

## Customer-month features (0.4)

`build_customer_month_features` produces one point-in-time row per `customer_id` and
`reference_month`, keyed to the last observable day of that month:

```text
Estimator input 1.0 / 1.1
        |
        v
slice_request(cutoff)          <- observed_at <= cutoff, balances at or before cutoff
        |
        v
RecurringIncomeEstimator.explain
        |
        v
monthly observation series
        |
        v
cash flow | stability | sources | coverage | activity | context
        |
        v
CustomerMonthFeatureTableV1
```

Each reference month re-runs the deterministic pipeline on a request narrowed to the records
observable at that cutoff, so point-in-time safety is a property of the input rather than of each
formula. A transaction posted in January but observed in March is invisible to January, February,
and every rolling window computed before it arrived.

The versioned schema holds 98 features in seven groups:

- **cash flow:** gross credits, debits, probability-weighted probable income, and reconstructed
  income over trailing 1, 3, 6, and 12 months, plus imputed income and excluded own transfers, loan
  disbursements, investment redemptions, and refunds;
- **stability:** mean, median, standard deviation, variance, coefficient of variation, zero-income
  months, quartiles, minimum, and maximum of the reconstructed monthly income series;
- **sources:** active, recurring, and ecosystem stream counts, trailing source income, largest
  source share, Herfindahl-Hirschman concentration, recurrence scores, and months since last
  source activity;
- **coverage:** observed months, accounts, institutions, declared consent coverage, observed
  product domains, and a composite completeness score;
- **activity:** transaction, credit, and debit counts, distinct credit counterparties, and days
  since the last credit or transaction;
- **context:** available balance and staleness, observed loans and disbursements, and investment
  contributions, redemptions, and net flow;
- **capacity:** card spend, credit utilization, installment commitment, monthly debt payment,
  outstanding debt, and investment balance.

Features that cannot be computed are reported with an explicit reason instead of a zero:
`CONTRACT_DOMAIN_UNAVAILABLE` for the capacity group, which needs estimator input `1.2`;
`NO_OBSERVED_RECORDS` when a product domain has not been observed by the cutoff;
`INSUFFICIENT_HISTORY` for dispersion over fewer than two months; and `UNDEFINED_ZERO_DENOMINATOR`
when a ratio would divide by zero. Product domains are themselves point-in-time: a loan counts only
once its disbursement transaction is visible, so a later product cannot make an earlier month look
better covered.

One documented exception to per-cutoff recomputation: `effective_consent_coverage_basis_points` and
`minimum_account_coverage_basis_points` come from provider-declared consent records that describe
the whole window. Contract `1.1` exposes no monthly coverage measurement, so those two features
carry window-level metadata and say so in their schema formula.

`FEATURE_SET_VERSION` and `FEATURE_SCHEMA_FINGERPRINT` freeze every name, unit, window, and formula.
The fingerprint is asserted in tests, so changing a formula fails until the version is bumped
deliberately.

```python
from income_estimator import build_customer_month_features

table = build_customer_month_features(request)
row = table.row("2026-06")
row.to_mapping()["income_median_12m_minor"]
row.missing_features
```

```bash
income-estimator --features request.json
```

The default estimator is promoted `0.2`. The rejected `0.3` classifier stays optional and is
recorded in `model_versions` when passed explicitly:

```python
from pathlib import Path

from income_estimator import SupervisedIncomeEstimator, build_customer_month_features

table = build_customer_month_features(
    request,
    SupervisedIncomeEstimator(Path("training/artifacts/transaction-classifier-0.3.0.json")),
)
```

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
income-estimator --features request.json
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
