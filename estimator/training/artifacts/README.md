# Frozen training artifacts

## Quantile calibration 0.11 — conditional cell selector, PROMOTED

`conditional-selector-intervals-0.11.0` is the promoted calibration. Artifact schema `1.5`; the
reader accepts `1.0` through `1.5` and the writer emits only `1.5`.

- `quantile-calibration-0.11.0.json` holds the residual quantile model, the per-band offsets and
  adjustments, the conditional selector, the support envelope, and the capacity artifact hash it was
  fitted against;
- `quantile-calibration-0.11.0-report.json` records every gate on the validation population;
- `lockbox-conditional-selector-intervals-0.11.0-report.json` records the release lockbox read;
- `conditioner-preregistration.json` records how the selector's conditioner was chosen, and what it
  beat, before the selector was built.

### What it does

Rows are partitioned into quartiles of one conditioner crossed with the confidence band. Each cell
chooses the learned band or the fixed band, and carries its own two tail corrections. The `low` band
is not selected over at all: it holds both its tails already, at `0.1091` and `0.0922` against
`0.10`, and it keeps its band-level correction untouched.

The branch is decided out-of-fold inside the uncertainty-training population, one quantile refit per
fold, comparing the two branches only after both have been corrected to hold their tails, so the
narrower one wins on like-for-like terms. The corrections are then fitted on calibration customers
against the branch each cell selected — the same split-conformal step as before, on a finer
partition.

Five of six cells chose `fixed`. The learned band earned its place in one cell, `q2/high`. That is
the diagnostic's finding restated by the model: the learned band was over-wide almost everywhere.

### The conditioner was pre-registered

`observed_domain_count`, cut at `2`/`3`/`5`, ranked first of `64` candidates on `8,028` out-of-fold
rows across `709` uncertainty-training customers. It was chosen without any final-test population
being loaded, and `conditioner-preregistration.json` carries the full ranking and the criterion.

This matters more than the choice. An earlier scan ranked the same features against final test and
picked the same winner, and that route is disqualifying: selecting a model on the population that
then measures it means the measurement is no longer a test. The pre-registration exists so the
choice can be checked rather than trusted.

The lockbox bears this out. Validation and lockbox agree almost exactly — `income_diverse` coverage
`0.8021` against `0.8059`, lower tail `0.1014` against `0.0969`, upper tail `0.0965` against
`0.0972`. A selection effect is precisely what would have shown up as a gap there.

### Measured on validation

`8,635` of `8,640` rows publish; `5` are refused as out of support. Coverage `0.9050` against a
nominal `0.80`.

| Suite | coverage | floor | lower tail | upper tail | width | sharpness bound | margin |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `income_diverse` | `0.8025` | `0.7500` | `0.1014` | `0.0965` | `168,704` | `-44,297` | `6,176` |
| `incomplete_observation` | `0.9295` | `0.7500` | `0.0427` | `0.0278` | `45,698` | `-66,053` | `2,785` |
| `life_events` | `0.9830` | `0.7500` | `0.0000` | `0.0170` | `51,616` | `-31,263` | `1,676` |

By band: high `0.9176`, medium `0.9128`, low `0.7987`, each against a floor of `0.7500` and each
holding both tails. Zero-truth coverage `0.9983`.

Against `0.9`, `income_diverse` gains coverage, `0.7670` to `0.8025`, while its width falls from
`286,952` to `168,704` and its failing upper tail drops from `0.1372` to `0.0965`.
`incomplete_observation` narrows from `162,927` to `45,698`, a `72%` reduction, and gains coverage.
Both of the diagnostic's targets, widen where it misses and shrink where it over-covers, are met at
once.

### Confirmed on the release lockbox

`RELEASE_CONFIRMED` on seeds `710_000`+, generated for the first time by that run and read once.
Published coverage `0.9116` on `8,640` of `8,640` rows, every band and suite inside its floor and
both tails, every suite passing sharpness.

Reproduce from the `estimator` directory, after the capacity model and the pre-registration:

```bash
python -m training.select_conditioner --population-size-per-suite 240 --workers 4
python -m training.calibrate_quantiles --population-size-per-suite 240 --workers 4
python -m training.evaluate_lockbox --population-size-per-suite 240 --workers 4
```

### Known limits

- **The lockbox was read before abstention was added.** The promoted artifact differs from the one
  it measured by exactly two things, the added `support_envelope` and the schema bump, with every
  offset, adjustment, cell policy and tree identical. Nothing in the offset path reads the envelope,
  so for any in-support row the published bounds are the bounds `RELEASE_CONFIRMED` measured. What
  is untested is whether any lockbox row now falls outside the envelope, which could only remove
  intervals, never change one. Confirming it needs a second lockbox read, and a lockbox read twice
  is a validation set.
- Seeds `610_000`+ were spent on a mechanism smoke test before the real read and are recorded as
  spent in `SPENT_LOCKBOX_SEED_FLOORS`. The release read used `710_000`+.
- `observed_domain_count` is integer-valued, so its quartile cuts at `2`/`3`/`5` collapse to three
  occupied buckets rather than four. No cell fell back, every one carrying at least `720` scores,
  but the pre-registration guard should have caught the discreteness rather than leaving it to be
  noticed afterwards.
- The conformal unit is still the customer-month rather than the customer. This is empirical
  customer-disjoint calibration with customer-clustered error bars, **not** a finite-sample
  guarantee, and must not be described as one.
- Annual quantiles are still not produced.

## Support envelope and out-of-calibration abstention

An `80%` interval is a statement about the population it was calibrated on. Outside it the
corrections were never measured, the label keeps its wording and loses its meaning, and until now
nothing at inference time could say so.

The artifact carries the range each fenced feature took across the calibration population. A row
outside any of those ranges receives no interval and
`quantile_unavailable_reason = OUT_OF_CALIBRATED_SUPPORT`, which is distinct from
`UNCALIBRATED_INTERVAL`: that one says the band has no fitted correction, this one says the
correction exists and does not apply here.

Nine features are fenced: the eight the residual quantile ensembles split on most, plus the
selector's conditioner. Four hundred stumps between them touch almost the whole feature table, most
of it once or twice, and a box drawn around all of it refused `25` of `576` rows at smoke scale —
fencing sampling noise rather than a change of regime. Usage is the ranking because a feature the
ensembles return to is one the width actually depends on.

A missing value is in support. Missingness is modelled rather than imputed everywhere else here, and
calibration saw plenty of it.

The refusal is made in `interval_minor`, so everything that produces an interval refuses the same
rows. The first attempt put it in `combine_month` alone, which left the evaluation harness scoring
rows the runtime would decline — a gate measuring a different model than it ships, which is the
defect this line of work opened with.

Complete promotion accordingly means every **supported** row publishes. The refusal share is gated
against a `1%` ceiling so the envelope cannot quietly fence the calibration distribution itself; on
validation it refuses `0.0006`, five rows, on `available_balance_minor`, `income_6m_minor` and
`transaction_count_1m`.

The envelope does not repair out-of-distribution coverage and is not meant to. It makes the scope of
the claim honest by refusing outside it. Held-out stress behaviour remains unmeasured for this
artifact.

## Quantile calibration 0.10 — width-slope recalibration, not promoted, not written here

`recalibrated-width-intervals-0.10.0` is the candidate ADR 0007's diagnostic pointed to: one
monotone power transform per tail, `corrected = scale * raw ** slope`, on the `high` and `medium`
bands only, fitted customer-out-of-fold inside the uncertainty-training population. Its artifact is
not committed, because it does not promote.

**It solved sharpness completely.** On the validation population, the paired customer-clustered
difference against the fixed-band model, against a predeclared margin of `2%` of that suite's own
baseline score:

| Suite | paired diff | error bar | upper bound | margin | verdict |
| --- | --- | --- | --- | --- | --- |
| `income_diverse` | `-12,218` | `5,780` | `-657` | `6,195` | pass |
| `incomplete_observation` | `-13,466` | `2,461` | `-8,544` | `2,785` | pass |
| `life_events` | `-37,412` | `53` | `-37,307` | `1,676` | pass |

The low band bypassed the transform exactly, `0` of `770` rows divergent, checked by publishing every
low-band row twice from the same artifact with the recalibrator present and removed.

**And it broke coverage on `income_diverse`**, from `0.7670` to `0.6083` against a floor of `0.7500`,
with both tails failing at `0.1809` and `0.2108`. Every width quartile under-covers: `0.406`,
`0.549`, `0.654`, `0.825`.

### Why a width-only transform cannot do this

The fitted lower slope came out at exactly `1.0`, the boundary. A slope of one is a pure rescale
with no compression at all, which is the fit reporting that within the pooled `high` and `medium`
rows, the ordering of raw widths carried no usable signal about which rows needed more of it. Both
scales landed near `0.4`, so what the transform actually applied was a level shrink of about `60%` —
the opposite of the slope correction it was built to make.

The reason is that the regimes overlap in raw width. At the same learned width an `income_diverse`
row needs a wider interval and a `life_events` row needs a narrower one, and a monotone function of
that width alone has no way to tell them apart. It shrank both, `life_events` correctly and
`income_diverse` catastrophically.

### The prescribed fallback cannot pass either

The fallback was a binary fixed/adaptive selector on `data_completeness` and `confidence_band`,
conformalized per cell. Granting it an oracle on both halves — a selector that sees each cell's
per-suite outcome and picks the better branch, and a correction set to the exact `0.90` quantile of
the very rows it is then scored on, neither of which any customer-out-of-fold and split-conformal
procedure can beat — `income_diverse` still misses both tails, `0.1395` and `0.1438` against a
ceiling of `0.1250`.

The cells do not separate the regimes. `high`/`high` is `66%` `income_diverse`, `23%` `life_events`
and `11%` `incomplete_observation`, and its worst suite still misses `0.1519` after choosing the
better branch; `high`/`medium` misses `0.1667`, `partial`/`low` `0.3043`. A single correction per
cell lands between a hard majority and an easy minority and serves neither.

That upper bound is one-sided and structural: within a cell, one threshold moves every suite the
same direction, so `income_diverse`'s miss cannot be brought down without pushing the cell above the
coverage it was fitted to.

### No conditioner in the feature set is usable

Every feature was then tried as the conditioning variable, cut into quartile cells crossed with the
confidence band, first at the oracle bound and then under an honest customer split: corrections
fitted on half the customers, scored on the other half, selector chosen on the fitting half, ten
seeds.

| Conditioner | honest worst tail, median | min | max |
| --- | --- | --- | --- |
| `observed_domain_count` | `0.1079` | `0.0957` | `0.1213` |
| `investment_balance_minor` | `0.1077` | `0.0873` | `0.1308` |
| `transaction_count_1m` | `0.1133` | `0.0994` | `0.1308` |
| `income_median_12m_minor` | `0.1180` | `0.1063` | `0.1308` |
| `data_completeness_score_basis_points` | `0.1398` | `0.1140` | `0.1496` |

`data_completeness`, the prescribed conditioner, fails on every split. One feature,
`observed_domain_count`, stays under the `0.1250` ceiling on all ten, and it is **not usable
either**, for a reason that has nothing to do with its numbers: it was found by ranking sixty-eight
features against the validation population. Building on it would be selecting a model on the data
the gate then measures, which is the one thing the validation population cannot survive, and its
`0.1213` worst split leaves `0.0037` of headroom to a ceiling chosen before any of this.

The honest reading is that the oracle bound rejects designs and endorses none. It has now rejected
the prescribed fallback outright, and the only conditioner that survives an honest split is
disqualified by how it was found. A conditioner chosen inside the uncertainty-training population,
never having seen final test, is the remaining methodologically clean route, and it is a decision
about the plan rather than a result.

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
back to the joint widening and cannot promote. Sharpness is mandatory: the candidate's Winkler score
is measured against the fixed-band conformal model on the same final rows, so coverage bought by
widening cannot pass a one-sided coverage gate.

Sharpness is a one-sided non-inferiority test on the **paired** difference. Both models score the
same rows, so the difference is taken row by row and the shared variance that dominates a Winkler
score, how hard each customer-month happens to be, cancels. Its error bar comes from resampling
customers, as everywhere else here. The candidate passes when the upper end of that difference sits
below a margin fixed in advance: `SHARPNESS_NONINFERIORITY_MARGIN`, `2%` of that suite's own
baseline score. Suite scores differ by roughly `4x`, so an absolute margin would mean four different
things.

The earlier form compared two independently reported means and failed anything above a ratio of
`1.0`, which calls `1.001` a regression and `0.999` an improvement on a difference whose sampling
noise nobody had measured. A difference with no error bar is now refused rather than passed.

The gate stays unconditional, and the report records why that matters. `baseline_tails_hold` says
whether the fixed-band model holds its own `p10`/`p90` claims on each suite; on `income_diverse` it
does not, so the candidate is being asked to beat a model that under-covers and wins on score by
declining to buy width it owes. That is recorded as a diagnostic, not turned into an exemption:
excluding under-covering baselines would remove exactly the pressure the interval score exists to
apply. `baseline_lower_tail_miss_rate` and `baseline_upper_tail_miss_rate` are recorded beside the
candidate's for the same reason.

`promotion.sharpness_valid_baseline_only` reports the same paired difference restricted to suites
whose baseline holds its own tails, and carries `gates_promotion: false`. It answers how much of the
failure is the contested comparison without becoming an exemption for it.

### `width_allocation` — diagnostic, never a gate

A suite-level mean says the candidate is worse without saying on which rows, and the two sharpness
failures point opposite ways: `income_diverse` needs a slightly wider upper tail while
`incomplete_observation` needs materially less width. Widening globally trades one for the other.

`report["width_allocation"]` breaks the paired per-row score difference down over the covariates
already in the feature table, pooled and per suite, each bucket with its own customer-clustered
error bar and its own coverage and tail miss rates:

| Dimension | Buckets |
| --- | --- |
| `confidence_band` | `high`, `medium`, `low` |
| `candidate_width_quartile` | `q1`–`q4`, cut on the candidate's own published widths |
| `hurdle_probability` | `unsure`, `likely`, `high` |
| `source_count_12m` | `none`, `one`, `two`, `high` |
| `recurrence_score` | `irregular`, `mixed`, `high` |
| `data_completeness` | `sparse`, `partial`, `high` |
| `months_observed` | `under-6`, `6-to-11`, `high` |
| `residual_sign` | `under-estimated`, `over-estimated`, `exact` |

A missing covariate is its own `unknown` bucket and is never folded into the lowest. The question
this answers is whether the existing features separate hard income-diverse rows from easy
incomplete-observation rows well enough to fit a conditional selector, or whether new features are
needed before that is worth attempting.

#### What the first run showed

They separate, and the strongest separator is the candidate's own predicted width. Measured on the
validation population at `240` customers per suite, the paired difference by width quartile:

| Suite | q1 | q2 | q3 | q4 |
| --- | --- | --- | --- | --- |
| `income_diverse` | `+10,319` | `-45,811` | `-56,962` | `+333,439` |
| `incomplete_observation` | `-45,768` | `+20,412` | `+24,902` | `+152,411` |

Both failures are the same defect. The candidate is *better* than the fixed-band model across the
middle of its own width distribution and loses everything in its widest quartile, where it spends
`728,476` against the baseline's `97,830` on `income_diverse` and covers `0.943` against a nominal
`0.80`. Twenty-five percent of rows carry the whole sharpness failure, on `10.6` and `24.3` sigma of
separation respectively.

The tail failure decomposes over the same cut, exactly:

| `income_diverse` quartile | upper-tail miss | coverage | candidate width | baseline width |
| --- | --- | --- | --- | --- |
| q1 | `0.308` | `0.583` | `39,020` | `38,416` |
| q2 | `0.142` | `0.724` | `131,260` | `59,559` |
| q3 | `0.085` | `0.819` | `250,009` | `91,356` |
| q4 | `0.014` | `0.943` | `728,476` | `97,830` |

Their mean is `0.1371`, and the suite's measured upper-tail miss rate is `0.1372`. The `p90` that
fails is almost entirely q1's, on rows where the candidate is no wider than the fixed-band model it
is supposed to improve on.

So the width model is miscalibrated in slope, not in level. It widens the rows it already believes
are hard, which were covered anyway, and leaves the rows it believes are easy at a width that misses
`p90` three times in ten. A global widening moves q4 further into the failure it already owns while
barely touching q1, which is where the misses are.

Two other covariates cut the same way and are available at inference. `data_completeness` separates
at `7.6` sigma inside `income_diverse`, where the candidate costs `+100,934` on `high` rows and gains
`-143,864` on `partial`. `confidence_band` separates at `10.2` sigma, with the entire pooled cost in
the `high` band, `+76,016` at coverage `0.917`, while `medium` is `-18,913`.

The low band is neutral and should be left alone: `-4,507` on a `+/-11,805` error bar, coverage
`0.7987`, tails `0.1091` and `0.0922` against `0.10`. Whatever the reallocation does, it must not
touch it.

Four populations, customer-disjoint, and the report asserts zero overlap:

| Population | Seeds | Purpose |
| --- | --- | --- |
| capacity-train | `110_000`+ | fit the point model |
| uncertainty-train | `210_000`+ | fit the residual quantile model |
| conformal-calibration | `410_000`+ | fit the band corrections |
| final-test | `510_000`+ | measure and gate, as **validation** |
| release lockbox | `610_000`+ | reserved, untouched, read once |

"Final test" names when the population is read in a run, not how independent it is. Seeds
`510_000`–`530_000` have now been read across several method-selection rounds; every look spends
some of their independence and no amount of care gives it back. They are validation seeds
permanently, and the report says so in `populations.final_test_role` rather than leaving a reader to
infer a lockbox from the name. A release lockbox is drawn from `RELEASE_LOCKBOX_SEED_FLOOR` upward,
after every gate passes on validation, and is read exactly once.

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
