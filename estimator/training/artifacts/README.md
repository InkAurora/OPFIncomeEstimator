# Frozen training artifacts

## Quantile calibration 0.9 — bandwise asymmetric conformalized quantile regression

- `quantile-calibration-0.9.0.json` holds the residual quantile model, one asymmetric conformal
  correction pair per confidence band, the joint widening kept only as a thin-band fallback, the
  band offsets kept as a schema 1.0/1.1 fallback, and the capacity artifact hash it was fitted
  against;
- `quantile-calibration-0.9.0-report.json` records coverage, both tail miss rates, width, and
  interval score on the final-test population, segmented by confidence band and suite, plus the
  sharpness comparison against the fixed-band conformal baseline and every gate result.

Artifact schema `1.2`. The reader accepts `1.0`, `1.1`, and `1.2`; the writer emits only `1.2`.

`0.8` corrected both tails of every band by one constant chosen on the pooled conformity score.
High and medium supplied 92% of that score's mass and both over-covered, so the constant came out
negative, `-0.0077`, and shrank the low band that was already under its floor. Each band now
corrects each tail at its own finite-sample `0.90` quantile, which is what makes the lower bound a
`p10` claim and the upper bound a `p90` claim rather than two halves of one `80%` claim. See
[ADR 0007](../../../docs/adr/0007-complete-adaptive-interval-promotion.md).

Promotion means complete promotion. The artifact publishes every band unconditionally; the final
test decides only whether the artifact promotes. Zero withheld rows and all three bands are
required, both tails are gated on their own miss rate, and a band too thin to fit its own pair falls
back to the joint widening and cannot promote. Sharpness is mandatory and has no configurable
ceiling: the candidate's Winkler score is measured against the fixed-band conformal model on the
same final rows, so coverage bought by widening cannot pass a one-sided coverage gate.

Four populations, customer-disjoint, and the report asserts zero overlap:

| Population | Seeds | Purpose |
| --- | --- | --- |
| capacity-train | `110_000`+ | fit the point model |
| uncertainty-train | `210_000`+ | fit the residual quantile model |
| conformal-calibration | `410_000`+ | fit the band corrections |
| final-test | `510_000`+ | measure and gate |

Calibration never refits the capacity model. Residuals are taken around the estimate `combine_month`
publishes, routing included, because that is the number the interval is a claim about.

Reproduce from the `estimator` directory, after the capacity model:

```bash
python -m training.calibrate_quantiles --population-size-per-suite 240 --workers 4
```

## Quantile calibration 0.8 — frozen sharpness baseline, limited promotion

- `quantile-calibration-0.8.0.json` holds the residual quantile model, the conformal widening, the
  band offsets kept as a fallback, and the capacity artifact hash it was fitted against;
- `quantile-calibration-0.8.0-report.json` records coverage, width, and interval score on the
  final-test population, segmented by confidence band, suite, and consent, plus every gate result.

Two boosted stump ensembles predict each row's lower and upper log-residual quantiles under pinball
loss. One constant, fitted on customers the quantile model never saw, widens both to recover the
coverage the learned quantiles do not carry on their own. That recovery is empirical and not a
finite-sample guarantee: the conformity scores behind it are correlated customer-months, roughly
twelve per customer, not independent draws. See
[ADR 0006](../../../docs/adr/0006-uncertainty-protocol-and-gate-semantics.md).

Four populations, customer-disjoint, and the report asserts zero overlap:

| Population | Seeds | Purpose |
| --- | --- | --- |
| capacity-train | `110_000`+ | fit the point model |
| uncertainty-train | `210_000`+ | fit the residual quantile model |
| conformal-calibration | `410_000`+ | fit the conformal widening |
| final-test | `510_000`+ | measure and gate |

Calibration never refits the capacity model. Residuals are taken around the estimate `combine_month`
publishes, routing included, because that is the number the interval is a claim about.

The gate is one-sided on under-coverage, per suite and per band, with the error bar measured by
resampling customers. Exceeding nominal is not a failure; sharpness is judged separately by the
Winkler interval score.

`conformal-intervals-0.8.0` is frozen byte-for-byte and is not regenerated. Its report is
relabelled `LIMITED_PROMOTION`: two of three bands publish, roughly 9% of supported months receive
no interval, and the `PROMOTED` it originally recorded came from a gate that let a failing band
withhold itself and still count. It is retained as a research result and as nothing else.

It is **not** the sharpness comparator. The fixed-band model the gate measures against is rebuilt
in-run, `adaptive-intervals-0.9.0-fixed-band-baseline`, from the same calibration rows and bound to
the same capacity bytes as the candidate, because the comparison only means anything on the same
final rows. It is **not** a rollback target either: its binding no longer resolves, so the runtime
refuses to load it at all. Until a matching promoted calibration exists, the rollback is no
intervals.

Its `capacity_artifact_sha256` binding is dangling by design. It was fitted against the retrained
feature-`1.2.0` model at a time when that model was written over the file named
`capacity-estimator-0.5.0.json`; those bytes are now `capacity-estimator-0.6.0.json` with
`model_version` corrected, which changes the hash. The pre-rename hash
`f4f10e8d9930902d3eda26e7b5b64c37ccd75a0d1a518043dd64f0fac6ab7a08` is recorded in
`capacity-estimator-0.6.0-report.json` as `pre_rename_artifact_sha256`, so the binding is traceable
rather than lost. Traceable is not loadable: the runtime compares digests, `f4f10e8d...` is not the
digest of any file here, and so `0.8` cannot be paired with a capacity model at all. Reading it
again would mean refitting it.

## The `0.9` report predates the gate repair

`quantile-calibration-0.9.0-report.json` is `schema_version` `1.3`, written before two gate defects
were fixed, and is kept unregenerated so the repair is auditable against it. In it,
`life_events.yaml`'s lower tail records a miss rate of `0.0` beside a `clustered_standard_error` of
`0.00559`, which cannot both be true: a tail no customer ever misses has a standard error of
exactly zero. The gate read that `0.0` as a missing value and substituted a row-level binomial,
then published the substitute under the clustered name. Replaying the repaired gate over the
report's own stored metrics corrects that one error bar to `0.0` and moves no pass/fail decision,
so all three recorded failures stand.

The rerun that produces a `schema_version` `1.4` report is deliberately deferred until the
sharpness comparison is measured properly: paired, customer-clustered, against a predeclared
non-inferiority margin.

## Quantile calibration 0.7 — superseded, not promoted

- `quantile-calibration-0.7.0.json` and its report are retained for comparison only.

A single global offset pair. It passed its gate at coverage `0.8365` under simulator contract `1.5`
and fails it at `0.8568` under contract `1.6`, because repairing the reversal defect widened the gap
between consent segments until one pooled offset could no longer serve both. Its own report already
recorded coverage of `1.00`, `0.817`, and `0.412` across the high, medium, and low bands, which is
the limitation `0.8` exists to remove.

## Capacity estimator 0.6 — promoted

- `capacity-estimator-0.6.0.json` is the portable, dependency-free model artifact;
- `capacity-estimator-0.6.0-report.json` records versions, artifact SHA-256, customer and row
  counts, train/validation/test metrics for the candidate and all three baselines, segmented
  results, and the promotion decision.

`0.6.0` is the feature-`1.2.0` retrain on 510 training customers. It was written over the file named
`capacity-estimator-0.5.0.json` while carrying `model_version: capacity-gbdt-stumps-0.5.0`, so for
one milestone a model trained on nearly three times the data shipped under the older model's name.
ADR 0007 separates them: `0.5.0` is restored to its committed bytes and `0.6.0` carries the retrain.
Every consumer, calibration, stress evaluation, benchmark, and test, reads `0.6.0`.

The model is a hurdle. A logistic gate decides whether sustainable income is zero; an anchored
regressor sizes it when it is not. The regressor boosts `log1p(sustainable_monthly_income)` around
`log1p(income_mean_3m_minor)`, so an empty model reproduces that anchor and every tree moves the
estimate away from it only for a measured reason. Both parts are decision stumps over the same
binned features, and missing features are routed by a direction recorded per stump rather than
imputed.

Two design points are worth keeping in mind when the model is retrained:

- A single regressor on the raw log target loses to a trivial baseline. Zero-income rows have a
  log residual near `-13` where ordinary rows sit near `0.3`, so squared error spends the model's
  capacity on them and drags every other estimate down. Splitting the zero decision out is what
  makes the rest of the fit possible.
- The gate threshold is selected on validation by mean absolute error in minor units, not by
  classification score. A wrong zero costs the whole estimate; a wrong positive costs only its
  error.

Features come from estimator input `1.2` through feature set `customer-month-features-1.2.0`.
Labels come from private contract `income-targets-1.0`, joined only after observed features are
built. Customers are split 70/15/15 by the frozen SHA-256 partition shared with the `0.3` dataset,
so no customer spans two partitions.

Feature set `1.2.0` adds six source features the model previously could not see: a
frequency-normalized monthly capacity per income stream, its largest component, cadence confidence,
observation count, source age, and a no-source flag. The detector had reported the median of
*paying* months as the monthly amount, so a quarterly source of `9,000` read as `9,000` rather than
`3,000`, and `source_features` did not expose even that figure. `expected_monthly_amount_minor` is
unchanged, because the realized-income path uses it to impute a paying month, where the paying-month
amount is the correct value.

Held-out test results on 109 customers, against the best baseline (`recurring_stream_mean_3m`):

| metric | candidate | best baseline |
|---|---|---|
| MAE (minor) | 25,217 | 74,469 |
| WAPE | 0.0499 | 0.1473 |

The candidate improves every measured segment except perfectly stable salaried income, where it
loses narrowly to the cash-flow baseline (`11,413` against `11,140`). For a customer whose salary
never moves, last month's reconstruction is already the answer, and estimator `0.6` routes to it.

Reproduce from the `estimator` directory:

```bash
python -m training.train_capacity_estimator --population-size-per-suite 240 --workers 4
```

Held-out test results above are the `0.6.0` retrain's.

## Capacity estimator 0.5 — superseded

- `capacity-estimator-0.5.0.json` and its report are the original feature-`1.1.0` model, trained on
  174 customers, retained for reproducibility.

It is not loadable against feature set `1.2.0` in any meaningful sense: `1.2.0` changed what
`expected_monthly_amount_minor` and the source features mean, so the same inputs no longer carry the
values this model was fitted on. Nothing reads it. It exists so that `0.5`, `0.7`, `0.8`, and `0.9`
each remain independently reproducible.

## Experimental transaction classifier 0.3 — not promoted

These files are frozen outputs from the estimator `0.3` supervised transaction-classifier
evaluation:

- `transaction-classifier-0.3.0.json` is the portable, dependency-free model artifact;
- `transaction-classifier-0.3.0-report.json` records versions, artifact SHA-256, customer and record
  counts, train/validation/test metrics, critical false-positive rates, and promotion status.

The dataset uses three deterministic 120-customer suites (`income_diverse`, `life_events`, and
`incomplete_observation`). Features are extracted from estimator input `1.1` before private labels
are joined. Customers are assigned to disjoint 70/15/15 partitions by a versioned SHA-256 function.

Reproduce from the `estimator` directory:

```bash
python -m training.train_transaction_classifier \
  --project-root .. \
  --output training/artifacts \
  --population-size-per-suite 120 \
  --workers 4
```

The held-out candidate ties the frozen rules at F1 `0.99552372` and keeps every critical
false-positive rate at zero. Promotion requires a strict F1 improvement, so the report records
`NOT_PROMOTED`. The artifact is retained for reproducibility and explicit experiments; it is not the
default estimator.
