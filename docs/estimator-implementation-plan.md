# Income Estimator Implementation Plan

## 1. Objective

Build an explainable income estimator in two layers:

1. reconstruct visible income from observed financial events;
2. infer sustainable income when observation is incomplete or ambiguous.

The final system should produce realized and sustainable income estimates, annual projections,
calibrated uncertainty intervals, confidence, and auditable evidence. Machine learning should
correct measured limitations of deterministic methods rather than replace an unexplained baseline.

## 2. Current state

The financial simulator and estimator support evaluated milestones through `0.6`:

- simulator orchestrator `0.7.0` generates deterministic populations;
- observation contract `1.5` includes incomplete-consent and data-quality artifacts;
- estimator boundary contracts `1.0` and `1.1` expose immutable observation-only requests;
- automatic evaluation joins predictions with physically isolated private truth;
- estimator `0.1.0` freezes the strict observation-only rule baseline;
- estimator `0.2.0` adds deterministic stream detection and coverage-aware gap reconstruction;
- input `1.1` optionally adds provider-visible counterparty, transaction type, balance-after, and
  balance context;
- experimental estimator `0.3.0` adds point-in-time features, customer-isolated training, and a
  portable supervised transaction classifier;
- fixed held-out artifacts compare promoted versions and record the `0.3` promotion decision;
- feature set `customer-month-features-1.1.0` publishes point-in-time customer-month rows built by
  replaying the promoted `0.2` estimator at each reference-month cutoff;
- estimator input `1.2` exposes observed cards, limits, card transactions, invoices, loan payments,
  loan balances, investments, and investment balances, so the capacity feature group is computed;
- promoted capacity estimator `capacity-gbdt-stumps-0.5.0` predicts sustainable monthly income from
  the customer-month table and beats every deterministic baseline on held-out data;
- estimator output `1.1` separates realized from sustainable income and carries component
  estimates, disagreement, confidence, and excluded evidence;
- estimator `0.6` routes both targets deterministically and beats its best individual component;
- private contract `income-targets-1.0` projects all five income targets from the hidden run, so
  `sustainable_monthly_income` now exists as a trainable label.

Boundary contract `1.1` is backward compatible with `1.0`, but the current simulator observations do
not populate its optional provider transaction type or counterparty fields, so counterparty-aware
stream clustering still falls back to normalized descriptions. Contract `1.2` closes the product
gap: card behavior, loan servicing and outstanding principal, and investment positions are now
observable. Private contract `income-targets-1.0` closes the target gap: realized, expected, and
sustainable income are now distinct labels rather than one generic `true_income_minor`.

## 3. Income target definitions

Implementation must begin with a versioned architecture decision that fixes exact target semantics.
Recommended private targets are:

```text
realized_income_month
expected_income_month
sustainable_monthly_income
realized_income_trailing_12m
expected_income_next_12m
```

Proposed definitions:

- **Realized monthly income:** sum of economic `INCOME` events realized during the calendar month.
- **Expected monthly income:** expected contribution of income sources active at the reference date,
  using their frequency, payment probability, seasonality, and amount distribution before random
  realization.
- **Sustainable monthly income:** robust monthly central estimate of active non-one-off income over
  the next 12 months, excluding extraordinary inflows.
- **Realized trailing annual income:** sum of realized income over the previous 12 complete months.
- **Expected forward annual income:** sum of expected income over the next 12 months from the state
  known at the reference date.

The final architecture decision must also specify treatment of bonuses, source start/end dates, job
changes, zero-income months, partial months, and fewer than 12 months of history. No supervised model
should be trained before these definitions are fixed.

[ADR 0001](adr/0001-income-target-definitions.md) fixes the definitions and
[ADR 0002](adr/0002-income-target-construction.md) fixes their construction, including every rule
deferred above. Targets are projected by the simulator, beside the engine whose parameters define
the expectation, and consumed by the estimator training zone. Expectations are exact rather than
sampled: the engine's volatility shock has zero mean, so one attempt's expected amount is its base
amount scaled by source seasonality, scenario seasonality, and payment probability. Forward-looking
targets apply the state effective at the reference cutoff and never a later life event, so
`sustainable_monthly_income` measures capacity known at the reference date rather than a forecast
privileged with future knowledge.

## 4. Target architecture

```text
Observed estimator contract
            |
            v
      Preprocessing
            |
            v
 Transaction Intelligence
            |
            v
  Income Stream Detection
            |
            v
 Monthly Income Reconstruction
            |
            +--------------------------+
            |                          |
            v                          v
   Cashflow Estimator          Customer Features
                                       |
                                       v
                              Capacity Estimator
            |                          |
            +-------------+------------+
                          v
                       Ensemble
                          |
                          v
             Quantiles + Confidence + Evidence
```

### 4.1 Trust boundary

Three dependency zones must remain separate:

```text
estimator runtime  -> observed data only
training pipeline  -> observed features plus private labels
evaluation harness -> predictions plus private truth
```

Runtime estimator code must never import simulator ground-truth modules. Training may join observed
records with private labels in an isolated dataset-building step. Evaluation may access both sides
only after inference has completed.

Truth fields prohibited from estimator input include:

```text
economic_type
is_income
income_source_id
is_self_transfer
truth_transaction_id
life_event_id
```

Automated leakage tests must enforce this allow-list boundary.

## 5. Target package structure

```text
estimator/
|-- pyproject.toml
|-- src/income_estimator/
|   |-- contracts/
|   |-- preprocessing/
|   |   |-- normalize.py
|   |   |-- deduplicate.py
|   |   |-- counterparties.py
|   |   `-- transfers.py
|   |-- transaction_intelligence/
|   |   |-- features.py
|   |   |-- rules.py
|   |   `-- classifier.py
|   |-- income_streams/
|   |   |-- clustering.py
|   |   |-- recurrence.py
|   |   `-- detector.py
|   |-- features/
|   |   |-- cashflow.py
|   |   |-- cards.py
|   |   |-- credit.py
|   |   |-- investments.py
|   |   `-- coverage.py
|   |-- models/
|   |   |-- cashflow.py
|   |   |-- capacity.py
|   |   |-- ensemble.py
|   |   `-- quantiles.py
|   |-- confidence/
|   |-- explainability/
|   |-- pipeline.py
|   `-- cli.py
|-- training/
|   |-- datasets.py
|   |-- splits.py
|   |-- train_transaction_classifier.py
|   `-- train_capacity_estimator.py
|-- evaluation/
`-- tests/
```

Only `src/income_estimator` belongs to runtime inference. Training and evaluation code must remain
outside the runtime package and must not be imported by it.

## 6. Contract evolution

Project contracts remain Open Finance-inspired and project-owned. They do not reproduce official
Open Finance payloads.

### 6.1 Estimator input `1.1`

Extend the transaction view with provider-visible, optional information needed for recurrence and
counterparty analysis:

```text
provider_transaction_type
counterparty_name
counterparty_document_hash
balance_after_minor
balances[]
```

Optional fields are required because not every institution or consent scope will expose the same
information. Counterparty identifiers must be synthetic or appropriately transformed; private
simulator income-source identifiers must never be substituted for observed identifiers.

### 6.2 Estimator input `1.2` — implemented

Add the complete observed product data needed by the capacity model:

```text
credit_cards[]
credit_limits[]
card_transactions[]
card_invoices[]
loan_payments[]
loan_balances[]
investments[]
investment_balances[]
```

Input `1.0` remains supported for the first baseline. New fields should be introduced through a
versioned adapter rather than by allowing estimator code to consume simulator bundles directly.

Implemented as `EstimatorInputV12` with `build_estimator_input_v1_2` on the simulator side. Every
new collection is optional, product records carry their provider-visible date rather than an
arrival timestamp, and card, loan, and investment records must reference a product present in the
same request. `card_invoice_items` is deliberately omitted: installment commitment is derived from
the purchase and its installment count, which is what a provider view exposes. Arrival delay for
product records is not modeled at this version.

### 6.3 Estimator output `1.1` — implemented

The eventual output should include:

```text
customer_id
reference_month
currency

realized_income_estimate_minor
sustainable_income_p10_minor
sustainable_income_p50_minor
sustainable_income_p90_minor

annual_income_p10_minor
annual_income_p50_minor
annual_income_p90_minor

confidence_score
confidence_components
income_streams
contributing_transaction_ids
excluded_transaction_ids
estimator_version
feature_version
model_versions
```

Initial versions may expose only the fields they can support honestly. Point estimates must not be
presented with false precision when history is short or income is volatile.

Implemented as `IncomeEstimateV11`, a strict extension of output `1.0`: every `1.0` field keeps its
meaning, so an existing consumer reads a `1.1` record unchanged. Realized and sustainable income
never share a field. A quantile is present only when calibrated; `0.6` therefore leaves `p10` and
`p90` absent with `quantile_unavailable_reason` set to `UNCALIBRATED_INTERVAL`, and an absent
quantile is never a point estimate widened by a guess. Component estimates stay visible with their
weights even when routing gave a component zero weight.

## 7. Versioned delivery roadmap

### Estimator `0.0` — Foundation — implemented

Implement:

- target-definition architecture decision;
- Python package and runtime/training/evaluation separation;
- versioned input and output models;
- point-in-time cutoff utilities;
- deterministic configuration and artifact metadata;
- contract compatibility and leakage tests.

Acceptance criteria:

- runtime package cannot import private truth;
- every target has one testable definition;
- input rejects undeclared fields;
- reference-month features cannot inspect later observations;
- estimator version, feature version, contract version, and model version are recorded.

### Estimator `0.1` — Rule-based cash-flow baseline — implemented

Implement:

```text
Observed Transactions
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

This version must not use a trained model.

Acceptance criteria:

- deterministic output for the same input and estimator version;
- duplicate and reversal handling is auditable;
- visible own-account transfers, loan disbursements, and investment redemptions are excluded when
  supported by observed evidence;
- every included or excluded credit has reason codes;
- monthly estimates contain valid contributing transaction IDs;
- simulator evaluation produces overall and segmented baseline reports.

### Estimator `0.2` — Income streams — implemented for contract `1.0`

Implement recurring-source and income-ecosystem detection.

One stream should contain:

```text
stream_id
counterparty_cluster
first_seen
last_seen
frequency
median_amount_minor
amount_coefficient_of_variation
recurrence_score
income_probability
transaction_ids
```

Two supported patterns:

- **Recurring source:** few payers, strong periodicity, relatively stable amounts.
- **Income ecosystem:** many payers, weak individual recurrence, persistent aggregate inflow.

Begin with deterministic normalized-counterparty grouping. Add fuzzy clustering only after errors
show that exact grouping is insufficient.

Contract `1.0` implementation uses normalized description as observed cluster proxy. Stable streams
impute missing months only when measured account coverage is incomplete; complete-coverage zeros are
preserved. Many-payer ecosystem detection remains limited until contract `1.1` exposes observed
counterparty fields.

Acceptance criteria:

- stream output is reproducible;
- each stream is traceable to observed transactions;
- salaried and self-employed scenarios are evaluated separately;
- recurring-income reconstruction improves over `0.1` on the held-out population without relying
  on private identifiers.

### Estimator `0.3` — Supervised transaction classifier — evaluated, not promoted

Build a labeled training dataset through an isolated join:

```text
observed transaction features -> X
private is-income label        -> y
```

Start with a gradient-boosted tree classifier. Preserve `0.1` rules as the academic and operational
baseline.

Acceptance criteria:

- customer-level train/validation/test isolation;
- no future observations in transaction features;
- model improves held-out classification metrics over the rule baseline;
- false-positive rates for own transfers, loan disbursements, and investment redemptions remain
  within promotion thresholds established from the baseline;
- model artifact records dataset, simulator, feature, and hyperparameter versions.

Implemented candidate `supervised-transactions-0.3.0` uses deterministic gradient-boosted decision
stumps with dependency-free JSON inference. Features are computed in observation order using only
prior recurrence history. Synthetic private labels are joined only after observed feature
extraction, and SHA-256 customer partitioning produces disjoint 70/15/15 train/validation/test
groups. Rule exclusions for duplicates, reversals, visible own transfers, loan disbursements,
investment redemptions, and exclusion descriptions cannot be overridden by the model.

On the fixed 360-customer population, held-out candidate and baseline F1 both equal `0.99552372`,
with precision `1.0`, recall `0.99108734`, and zero false positives for own transfers, loan
disbursements, investment redemptions, and refunds. The strict-improvement criterion therefore
records `NOT_PROMOTED`. The remaining false negatives in this synthetic population are protected
reversed originals, so weakening the safety layer solely to improve this benchmark is rejected.

### Estimator `0.4` — Customer-month features — implemented for contract `1.1`

Create point-in-time features keyed by:

```text
customer_id
reference_month
```

Feature groups:

```text
cash flow:
  credits_1m/3m/6m/12m
  probable_income_mean_3m/6m/12m
  probable_income_median_3m/6m/12m

stability:
  income_std_3m/6m/12m
  income_cv_6m/12m
  zero_income_months_6m/12m
  income_p25/p50/p75_12m

sources:
  source_count
  largest_source_share
  source_concentration
  recurring_source_count
  recurrence_score_mean

capacity:
  card_spend
  credit_utilization
  installment_commitment
  monthly_debt_payment
  outstanding_debt
  investment_balance
  net_investment_contributions

coverage:
  months_observed
  institutions_observed
  accounts_observed
  effective_consent_coverage
  data_completeness_score
```

Acceptance criteria:

- all features are computed as of `observed_at`, not from future arrival knowledge;
- repeated computation produces identical values;
- feature schemas and formulas are versioned;
- missing product domains produce explicit missingness indicators rather than invented zero values.

Implemented feature set `customer-month-features-1.0.0` emits `98` features per
`customer_id` and `reference_month` through contract `CustomerMonthFeatureTableV1`. Point-in-time
safety is enforced on the input rather than per formula: every reference month narrows the request
to records observable at that month's cutoff and replays the promoted `0.2` pipeline on it, so no
feature can read a later arrival. Product-domain availability is evaluated at the same cutoff, so a
loan or investment becomes visible only once its linked transaction is observed.

Every group is now computed. Feature set `customer-month-features-1.1.0` fills the capacity group
from estimator input `1.2`; a request on contract `1.0` or `1.1` still reports those six features
as `CONTRACT_DOMAIN_UNAVAILABLE`, which stays distinct from the `NO_OBSERVED_RECORDS` reported when
a contract carries a domain but nothing has been observed by the cutoff. Consent-coverage features
are the one documented exception to per-cutoff recomputation: they carry provider-declared
window-level metadata, because no contract yet publishes a monthly coverage measurement.

`FEATURE_SET_VERSION` and `FEATURE_SCHEMA_FINGERPRINT` freeze names, groups, units, windows, and
formulas; tests assert both, so a formula change fails until the version is bumped. The rejected
`0.3` classifier remains optional and is recorded in `model_versions` when supplied.

### Estimator `0.5` — Capacity estimator — implemented

Both prerequisites are in place. Private contract `income-targets-1.0` supplies
`sustainable_monthly_income` for scenarios on contract `1.3` and above, and estimator input `1.2`
supplies the observed card, loan, and investment data behind the capacity feature group. Training
uses the full customer-month feature table against a real capacity label.

Train a customer-level tabular regressor using cash-flow, cards, loans, investments, balances,
behavior, history, and coverage. Initial target:

```python
y = log1p(sustainable_monthly_income)
```

Back-transform predictions before monetary evaluation. Compare against historical median, cash-flow
estimate, recurring-stream estimate, and simulator integration baseline.

Acceptance criteria:

- improves partial-consent performance over the cash-flow estimator;
- does not materially regress full-coverage performance;
- evaluation is segmented by income type, range, volatility, history length, and consent coverage;
- zero-income customers remain supported;
- output never infers complete observation merely from high apparent activity.

Implemented as a hurdle: a logistic gate decides whether sustainable income is zero, and an
anchored regressor sizes it when it is not. Both parts are decision stumps over binned features
with a per-stump missing direction, exported as one dependency-free JSON artifact.

Two findings shaped the design and are worth preserving. A single regressor on the raw log target
loses to a trivial baseline, because zero-income rows carry a log residual near `-13` where
ordinary rows sit near `0.3`; squared error then spends the model on them and drags every other
estimate down. And piecewise-constant stumps approximate a near-linear relationship badly, so the
regressor boosts the log-ratio around `income_mean_3m_minor` instead of around a constant, making
an empty model reproduce that anchor exactly. The gate threshold is chosen on validation by
monetary error, not by classification score.

Held-out results on 240 customers: MAE `25,055` against `55,455` for the best baseline, WAPE
`0.0459` against `0.1015`, full coverage `33,629` against `81,919`, partial consent `14,031`
against `21,429`, and exact prediction for zero-income customers. The only segment where a
baseline wins is perfectly stable salaried income, where last month's reconstruction is already the
answer. The report records `PROMOTED`.

### Estimator `0.6` — Ensemble — implemented

Combine:

```text
cashflow estimate
capacity estimate
recurring-stream estimate
coverage
model disagreement
```

Start with deterministic, documented weights or routing rules. A learned meta-model may follow only
after base-model out-of-fold predictions exist; fitting an ensemble on in-sample base predictions
would leak training performance.

Acceptance criteria:

- ensemble improves held-out monthly error over every individual component or documents segments
  where routing deliberately selects one component;
- weights and routing decisions are explainable;
- disagreement lowers confidence;
- component estimates remain visible in output diagnostics.

Implemented as two routed ensembles rather than one. The plan draws a single box, but cash-flow and
recurring-stream reconstruction produce `realized_income_month` while the capacity model produces
`sustainable_monthly_income`; those are distinct targets under ADR 0001, so blending them would
produce a number with no definition.

Realized income keeps the promoted `0.2` reconstruction with frozen `0.1` visible at zero weight.
Sustainable income routes to the capacity model except where income is stable, where last month's
reconstruction is already the answer. Conditioning that exception on full coverage as well was
measured and rejected: on the intersection the model wins again, and the narrower rule made the
ensemble worse than its own best component. Routing stays deterministic because a learned
meta-model needs out-of-fold base predictions, which do not exist yet.

Held-out on 312 rows the routed estimate reaches MAE `21,227` against `23,236` for the best
individual component, WAPE `0.0388`, improving the stable, partial-consent, short-history, and
middle- and high-income segments. The complete-coverage segment is `0.35%` worse and the report
records it. Confidence combines coverage, history, stability, classification certainty, and
component agreement under documented weights, then caps the result at observed coverage, so high
confidence cannot coexist with known low coverage.

### Estimator `0.7` — Quantiles and confidence

Produce sustainable and annual income quantiles:

```text
P10
P50
P90
```

Use calibrated quantile regression or conformal calibration. Treat confidence as a separate score
based on:

```text
data coverage
history length
transaction classification certainty
income-stream stability
model agreement
prediction interval width
out-of-distribution score
```

Acceptance criteria:

- interval coverage is measured against its nominal level;
- coverage and width are reported by income profile and consent level;
- confidence is empirically monotonic with observed accuracy;
- high confidence cannot coexist with known low coverage without a documented override;
- interval width responds to volatility and incomplete observation.

### Estimator `0.8` — Explainability and stress evaluation

Add:

- included and excluded transaction explanations;
- detected income-stream summaries;
- cash-flow, capacity, and ensemble component estimates;
- coverage and confidence decomposition;
- model feature contributions where appropriate;
- stress-suite reports and model cards.

Acceptance criteria:

- every estimate can be traced to model versions, features, and observed evidence;
- explanations contain no private labels;
- normal, noisy, partial-consent, high-volatility, life-event, adversarial, and out-of-distribution
  suites are reported separately;
- production-facing output distinguishes estimate, uncertainty interval, and confidence.

## 8. Transaction intelligence specification

### 8.1 Preprocessing

- sort by `observed_at`, then `posted_at`, then stable transaction ID;
- validate currency and customer/account scope;
- remove or mark exact duplicates;
- process reversals without treating both sides as income;
- normalize descriptions while preserving original text;
- resolve observed counterparties;
- identify visible paired transfers between the customer's accounts;
- record every transformation and exclusion reason.

### 8.2 Point-in-time transaction features

Use only observations available by the classification cutoff:

```text
amount_minor
direction
description tokens
account
institution
day_of_month
same_counterparty_count_30d/90d/365d
counterparty_amount_mean/std
payment_interval_mean/std
day_of_month_consistency
same_amount_frequency
similar_amount_frequency
```

Recurrence features should use preceding observations only. Later occurrences must not make an
earlier transaction look recurring during historical backtesting.

### 8.3 Classifier output

For each credit transaction:

```text
p_income
p_self_transfer
p_loan
p_investment_redemption
p_refund
p_other
reason_codes
```

Probabilities should support weighted reconstruction instead of forcing an early binary decision:

```text
probable_income_month = sum(credit_amount * p_income)
```

Monthly reconstruction should also expose:

```text
gross_credits
high_confidence_income
recurring_income
variable_income
excluded_own_transfers
excluded_loan_disbursements
excluded_investment_redemptions
excluded_refunds
```

## 9. Dataset splitting and point-in-time safety

Use a deterministic customer-level split:

```text
70% train
15% validation
15% test
```

No customer may appear in multiple partitions. Random month-level splitting is prohibited because
it leaks customer behavior across partitions.

Within each partition, every `reference_month` is a historical cutoff. Features may use only records
whose `observed_at` is at or before that cutoff. A transaction posted earlier but observed later is
not available retroactively.

Maintain additional evaluation suites:

```text
clean
normal
noisy
partial_consent
high_volatility
life_events
adversarial
out_of_distribution
```

Training distribution and stress distributions should remain separately versioned.

## 10. Evaluation metrics

### 10.1 Transaction classification

Primary metrics:

```text
precision
recall
F1
PR-AUC
ROC-AUC
```

Critical error rates:

```text
self_transfer_false_positive_rate
loan_disbursement_false_positive_rate
investment_redemption_false_positive_rate
refund_false_positive_rate
```

### 10.2 Income estimation

Primary metrics:

```text
mean absolute error
median absolute error
root mean squared error
WAPE
SMAPE
error normalized by mean income
```

MAPE must not be the primary metric because realized income may be zero. Report error by income
type, income range, consent coverage, history length, volatility, and proximity to life events.

### 10.3 Intervals and confidence

Measure:

- empirical interval coverage;
- mean and median interval width;
- coverage by profile and consent level;
- error by confidence band;
- monotonic relationship between confidence and accuracy;
- out-of-distribution behavior.

Promotion thresholds should be established after the deterministic baseline is measured, then
frozen for comparisons between model versions.

## 11. First implementation slice

The immediate delivery should contain:

1. a target-definition architecture decision;
2. an installable `income-estimator` package;
3. explicit runtime/training/evaluation boundaries;
4. contract compatibility and truth-leakage tests;
5. point-in-time normalization and transaction features;
6. deterministic rule classifier with reason codes;
7. deterministic income-stream detection;
8. weighted monthly income reconstruction;
9. integration with simulator population evaluation;
10. a report and true-versus-estimated chart over a held-out synthetic population.

Do not add a trained transaction classifier or capacity model in this slice. The baseline must first
show which transaction types, customer profiles, observation levels, and life events produce error.

## 12. Definition of done

A milestone is complete only when:

- project-authored content is in English;
- contracts, targets, feature formulas, and artifacts are versioned;
- runtime inference has no dependency on private truth;
- point-in-time and customer-split leakage tests pass;
- predictions are deterministic for fixed inputs and artifacts;
- applicable metrics are reported by relevant customer and observation segments;
- included and excluded evidence is auditable;
- simulator, dataset, estimator, feature, and model versions are recorded;
- no real client records or credentials are committed.
