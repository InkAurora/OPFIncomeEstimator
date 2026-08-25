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

Input contract `1.2` adds the observed product data the capacity model needs: credit cards, credit
limits, card transactions, card invoices, loan payments, loan balances, investments, and investment
balances. Every collection is optional, so a consent scope that omits a domain stays valid and is
reported as unobserved rather than as zero. Product records carry their provider-visible date; the
simulator adapter `build_estimator_input_v1_2` maps every domain its scenario contract exposes and
reads no private field.

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
  outstanding debt, and investment balance, all computed from estimator input `1.2`.

Features that cannot be computed are reported with an explicit reason instead of a zero:
`CONTRACT_DOMAIN_UNAVAILABLE` when the request is on contract `1.0` or `1.1` and cannot express the
capacity group at all; `NO_OBSERVED_RECORDS` when the contract does carry a domain but nothing has
been observed by the cutoff; `INSUFFICIENT_HISTORY` for dispersion over fewer than two months; and
`UNDEFINED_ZERO_DENOMINATOR` when a ratio would divide by zero. The first two are deliberately
distinct: a request that cannot describe cards is not the same as a customer who holds none.
Product domains are themselves point-in-time: a loan counts only once its disbursement transaction
or its own dated product record is visible, so a later product cannot make an earlier month look
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
income-estimator --ensemble --capacity-model training/artifacts/capacity-estimator-0.6.0.json --calibration training/artifacts/quantile-calibration-0.8.0.json request.json
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

## Capacity estimator (0.5)

Estimator `0.5` trains a customer-level regressor for `sustainable_monthly_income` on the `0.4`
feature table, labelled by private contract `income-targets-1.0`. It is a hurdle model: a logistic
gate decides whether sustainable income is zero, and an anchored regressor sizes it when it is not.

```text
customer-month features (0.4)      private income targets (simulator)
            |                                   |
            +---------------+-------------------+
                            v
                  isolated training join
                            |
              +-------------+-------------+
              v                           v
        zero gate (logistic)      anchored regressor
              |                           |
              +-------------+-------------+
                            v
              sustainable_monthly_income_minor
```

The regressor boosts `log1p(sustainable_monthly_income)` around `log1p(income_mean_3m_minor)`
rather than around a constant. Piecewise-constant stumps approximate a near-linear relationship
badly, so anchoring means an empty model reproduces the cash-flow anchor exactly and every tree
moves the estimate away from it only for a measured reason. Missing features are routed by a
direction recorded per stump, never imputed.

On the fixed held-out population the candidate improves MAE from `55,455` to `25,055` minor units
against the best baseline, improves both full-coverage and partial-consent segments, and predicts
zero-income customers exactly, so the report records `PROMOTED`. See
[`training/artifacts`](training/artifacts/README.md) for metrics, segments, and reproduction.

The model predicts a value the shared output contract `1.0` cannot yet carry, so it is not wired
into `estimate`. Ensemble routing is `0.6` and calibrated intervals are `0.7`:

```python
from pathlib import Path

from income_estimator import GradientBoostedCapacityModel, build_customer_month_features

model = GradientBoostedCapacityModel.from_path(
    Path("training/artifacts/capacity-estimator-0.6.0.json")
)
row = build_customer_month_features(request).row("2026-06")
model.predict_minor(row.to_mapping())
```

## Ensemble and output contract 1.1 (0.6)

Output `1.0` carries one number per month. Contract `1.1` adds sustainable income, component
estimates, disagreement, confidence, excluded evidence, and every producing version, while leaving
the `1.0` fields untouched so an existing consumer keeps working.

Estimator `0.6` routes two ensembles, not one. Cash-flow and recurring-stream reconstruction
produce `realized_income_month`; the capacity model produces `sustainable_monthly_income`. Those
are distinct targets under ADR 0001, so blending them would produce a number with no definition.

Routing is deterministic and documented. A learned meta-model needs out-of-fold base predictions,
which do not exist yet; fitting one on in-sample component output would leak training performance
into the weights.

Realized income keeps the promoted `0.2` reconstruction, with frozen `0.1` visible at zero weight.
Sustainable income goes to the capacity model except where income is stable, where last month's
reconstruction is already the answer and the model only adds noise. Conditioning that exception on
full coverage as well was measured and rejected: on the intersection the model wins again, and the
narrower rule made the ensemble worse than its own best component.

Held-out, 312 rows: routed MAE `21,227` against `23,236` for the best individual component, WAPE
`0.0388`. Routing improves the stable, partial-consent, short-history, middle-income, and
high-income segments; the complete-coverage segment is `0.35%` worse, which the report records
rather than hides.

```bash
python -m evaluation.ensemble_benchmark --population-size-per-suite 80 --workers 4
```

Quantiles stay absent on purpose. `0.6` produces point estimates and routing, not calibrated
intervals, so `p10` and `p90` are `None` with `quantile_unavailable_reason` set until `0.7`
measures their coverage. An absent quantile is never a point estimate widened by a guess.

```python
from pathlib import Path

from income_estimator import EnsembleIncomeEstimator

estimate = EnsembleIncomeEstimator(
    Path("training/artifacts/capacity-estimator-0.6.0.json")
).estimate_v1_1(request)
month = estimate.monthly_estimates[-1]
month.realized_income_estimate_minor
month.sustainable_income_p50_minor
month.confidence_score_basis_points
month.routing_reason_codes
```

Confidence combines coverage, history length, income stability, classification certainty, and
component agreement under documented weights, then caps the result at observed coverage. High
confidence cannot coexist with known low coverage, so a customer whose consent hides half their
accounts is never reported as well understood however tidy the visible half looks.

The capacity artifact is optional. Without it the ensemble still answers, using the
recurring-stream component, and says `CAPACITY_MODEL_UNAVAILABLE` in the routing reasons.

## Calibrated intervals (0.8 shipped, 0.9 candidate)

`sustainable_income_p10/p50/p90` are filled by conformalized quantile regression around the routed
estimate. Construction rules are fixed by
[ADR 0003](../docs/adr/0003-interval-and-confidence-semantics.md); the protocol and gate semantics
by [ADR 0006](../docs/adr/0006-uncertainty-protocol-and-gate-semantics.md) and
[ADR 0007](../docs/adr/0007-complete-adaptive-interval-promotion.md).

Two boosted stump ensembles predict each row's lower and upper log-residual quantiles under pinball
loss. Each confidence band then corrects each tail separately, on that tail's own scores from
customers the quantile model never saw, so the lower bound is a `p10` claim and the upper bound a
`p90` claim rather than two halves of one `80%` claim. Four customer-disjoint populations train the
point model, train the quantile model, correct it, and gate it.

```bash
python -m training.calibrate_quantiles --population-size-per-suite 240 --workers 4
```

The `0.9` candidate covers `0.9039` against a nominal `0.80` on **8640 of 8640** final-test rows
from 720 customers. Every band publishes and every band clears its `0.75` floor: high `0.9174`,
medium `0.9103`, low `0.7987`. By suite: `income_diverse` `0.7670`, `incomplete_observation`
`0.9618`, `life_events` `0.9830`. Zero-truth coverage is `0.9983`.

The shipped `0.8` artifact covers `0.9140` on 7870 of 8640 rows, withholding the low band. Its
per-suite figures count published rows only and are not comparable with the whole-population figures
above.

The coverage gate is one-sided. Under-coverage understates risk and fails; exceeding nominal does
not, because on a suite whose point estimate is often exact no interval width can bring coverage
down to nominal. Each tail is gated separately against `0.10`, because a joint `80%` figure is
satisfied by a lower tail missing `0.02` and an upper missing `0.18`. Sharpness is mandatory: the
Winkler score is compared per suite against the fixed-band conformal model on the same rows, which
is what stops a one-sided gate from being satisfied by widening.

That comparison is a one-sided non-inferiority test on the paired difference. Both models score the
same rows, so the difference is taken row by row and its error bar comes from resampling customers;
the candidate passes when the upper end of that difference stays below a margin declared in advance,
`2%` of the suite's own baseline score. A ratio just over `1.0` is noise and no longer reads as a
regression, and a difference with no error bar is refused rather than passed. The gate stays
unconditional even where the baseline itself under-covers, which is recorded per suite as
`baseline_tails_hold`.

`0.9` does not promote, and the findings are recorded rather than smoothed over. `income_diverse`
clears its coverage floor while its upper tail misses `0.1372` against a ceiling of `0.1250`, so its
published `p90` holds about `86%` of the time. Sharpness fails on `income_diverse` and
`incomplete_observation`. Calibration is over customer-months rather than customers, so this is
empirical customer-disjoint calibration with customer-clustered error bars and not a finite-sample
guarantee. Coverage is not expected to survive outside the calibration distribution and nothing
detects that at inference time, but how far it falls is unmeasured for `0.9`: the `0.491` noisy and
`0.125` high-volatility figures were measured on `conformal-intervals-0.8.0` in a run whose capacity
binding cannot be reconstructed, and that report is void as evidence. And
`annual_income_p10/p50/p90` stay absent, because deriving them from monthly quantiles needs a
dependence structure across months that nobody has measured.

A calibration artifact is bound to the capacity model it was fitted against, by `model_version` and
by the SHA-256 of the exact artifact bytes, and the runtime checks both when the estimator is
constructed. The offsets are residuals of one particular point estimate; paired with a different
capacity model, or with none, they keep their `p10`/`p90` label over a quantity nobody measured.
`capacity-estimator-0.5.0.json` was once rewritten in place under an unchanged `model_version`,
which is why the digest and not the version string is the load-bearing half of that check.

A predicted zero does not get a symmetric band. When the gate is confident the interval is `[0, 0]`,
a claim the evaluation can falsify; when it is unsure the lower bound stays zero and the upper bound
comes from the positive branch.

```python
from pathlib import Path

from income_estimator import EnsembleIncomeEstimator

estimator = EnsembleIncomeEstimator(
    Path("training/artifacts/capacity-estimator-0.6.0.json"),
    calibration_path=Path("training/artifacts/quantile-calibration-0.8.0.json"),
)
month = estimator.estimate_v1_1(request).monthly_estimates[-1]
month.sustainable_income_p10_minor, month.sustainable_income_p90_minor
```

Without a calibration artifact the estimator still answers, leaving `p10` and `p90` absent with
`quantile_unavailable_reason` set.

## Explainability and stress evaluation (0.8)

`explain_estimate` returns explanation contract `1.0`: every credit with the rule that decided it,
detected streams, component estimates, confidence decomposition, and the capacity model's feature
contributions. Nothing is re-decided, so an explanation can never disagree with the estimate it
explains, and a test asserts they agree field by field.

Feature contributions are exact rather than approximated. The capacity model is additive over
stumps, so each tree attributes to exactly one feature; the decomposition is a property of the model
rather than an estimate of it. Reported contributions are truncated to the largest ones with the
remainder folded into a single entry, so the printed decomposition still reconstructs the
prediction. The contract rejects one that does not.

```bash
income-estimator --explain --capacity-model training/artifacts/capacity-estimator-0.6.0.json --calibration training/artifacts/quantile-calibration-0.8.0.json request.json
```

[Model cards](docs/model-cards.md) cover every promoted artifact, each with its measured results and
its known failure modes. A test asserts no promoted version is missing a card.

### Stress suites

Six suites are reported separately, never pooled, because a pooled average hides the regime where an
estimator fails. Two of them, `noisy` and `high_volatility`, were never in training, so they measure
generalization to new conditions rather than to new customers.

```bash
python -m evaluation.stress_report --population-size 20 --workers 4
```

| suite | in training | realized WAPE | sustainable WAPE | interval coverage |
|---|---|---|---|---|
| clean | no | 0.000 | contract below 1.3 | — |
| normal | yes | 0.000 | 0.127 | 0.739 |
| partial_consent | yes | 0.000 | 0.007 | 0.981 |
| life_events | yes | 0.000 | 0.021 | 0.975 |
| noisy | no | 0.017 | 0.030 | 0.491 |
| high_volatility | no | 0.000 | 0.410 | 0.125 |

Both held-out suites expose real weakness. The noisy suite carries the only nonzero realized error,
now timing rather than classification: a reversal's corrected re-post that has not arrived by the
request cutoff carries income the estimator cannot yet see. The high-volatility suite is the worst
sustainable error, and both held-out suites under-cover badly, at `0.491` and `0.125` against a
nominal `0.80`. The interval is calibrated on `income_diverse`, `life_events`, and
`incomplete_observation`, and its guarantee does not reach conditions outside them.

No suite produced a single false-income month: the estimator never invented income where truth was
zero.

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
income-estimator --ensemble --capacity-model training/artifacts/capacity-estimator-0.6.0.json --calibration training/artifacts/quantile-calibration-0.8.0.json request.json
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
