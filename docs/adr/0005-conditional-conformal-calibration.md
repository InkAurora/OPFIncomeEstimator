# ADR 0005: Conditional conformal calibration

- Status: Accepted
- Date: 2026-08-22
- Supersedes: nothing
- Extends: [ADR 0003](0003-interval-and-confidence-semantics.md)
- Calibration contract: `conformal-intervals-0.8.0`
- Estimator milestone: `0.9`

## Context

ADR 0003 fitted one global pair of conformal offsets and recorded the consequence honestly:
coverage was `1.00`, `0.817`, and `0.412` across the high, medium, and low confidence bands, and it
named conditional conformal calibration as the natural next step, deliberately not attempted.

Repairing the reversal defect in [ADR 0004](0004-observed-balance-and-reversal-semantics.md) made
that limitation binding rather than merely documented. The refit fails the coverage gate at `0.8568`
against nominal `0.80` with a declared tolerance of `0.05`. Nothing about the method degraded; a
more accurate point estimate widened the gap between segments until the pooled figure fell outside
tolerance.

Measured out-of-fold on 2412 calibration rows and 468 test rows:

| Band | Calibration residuals | Test rows | Lower offset | Upper offset | Residual sigma |
| --- | --- | --- | --- | --- | --- |
| high | 1142 | 229 | `-0.068` | `+0.099` | `0.100` |
| medium | 887 | 181 | `-0.157` | `+0.216` | `0.210` |
| low | 207 | 58 | `-0.555` | `+0.433` | `0.546` |
| global, in use today | 2236 | 468 | `-0.156` | `+0.174` | — |

The residual scale differs by a factor of `5.4` between the high and low bands. The global offset is
almost exactly the medium band's own offset, which is why medium measures near nominal while the
other two miss in opposite directions.

The clearest statement of the defect is in interval width. The low band carries the worst relative
error, WAPE `0.2491`, and receives the *narrowest* mean interval, `107,525` minor, against `207,204`
minor for the high band at WAPE `0.0277`. Offsets are applied in log space, so absolute width tracks
the size of the point estimate rather than the uncertainty around it. The product currently tells a
user least about the estimates it understands least.

## Decision

### Offsets are fitted per confidence band

The artifact carries one offset pair per band instead of one pair overall. Bands are the ones the
product publishes, unchanged: `high` at or above `7000` basis points, `medium` at or above `5000`,
`low` below. ADR 0003 requires banding on the score the product actually shows rather than a proxy,
and that requirement is inherited here rather than revisited.

Calibration rows are banded using the fold model that produced their residual, never the promoted
model. The promoted model trained on those customers, so scoring them with it would leak exactly the
in-sample optimism out-of-fold prediction exists to remove.

### A thin band falls back to the global offset

A band fits its own offsets only with at least `100` positive out-of-fold residuals. Below that it
uses the global pair, which stays in the artifact for this purpose and for readers that do not
supply a band.

`100` is chosen so the nearest-rank `0.1` and `0.9` quantiles each sit on at least ten observations
rather than on the two or three that decide a tail at small counts. Every band clears it today, the
thinnest at `207`. The rule exists for populations where a band collapses, not for the current one.

### The gate is per band, and its tolerance follows the sample

Every band with at least `15` test customers must pass, and the pooled figure must pass. A pooled
tolerance is what allowed `0.76` against `0.98` to be reported as a single acceptable `0.8365` for
an entire milestone.

Tolerance is `max(0.05, 2 standard errors)`, and the standard error is measured by resampling
customers rather than rows.

### Coverage error bars are measured on customers, not customer-months

The `0.7` gate computed its standard error as `sqrt(p(1-p)/rows)`. That treats twelve months of one
customer as twelve independent observations. ADR 0003 assigns folds by customer precisely because
they are not, and the gate then contradicted its own reasoning.

The test partition holds `468` rows from `39` customers. A cluster bootstrap over customers gives:

| Scope | Coverage | Gate's row-level error | Clustered error | 95% interval |
| --- | --- | --- | --- | --- |
| overall | `0.878` | `0.0185` | `0.0345` | `[0.808, 0.944]` |
| high | `0.939` | `0.0264` | `0.0213` | `[0.896, 0.977]` |
| medium | `0.867` | `0.0297` | `0.0459` | `[0.770, 0.950]` |
| low | `0.672` | `0.0525` | `0.1033` | `[0.482, 0.879]` |

The reported error bar was roughly half its true size, and the `0.05` tolerance sat below the noise
the measurement carries. Two conclusions follow from the corrected bars, and neither was visible
before:

- High-band over-coverage is real. Its interval excludes nominal.
- The low band's `0.672`, which looks like the worst failure in the table, is not distinguishable
  from nominal on `18` customers. Gating it at any tolerance below about `0.20` measures noise.

Because a band's usable sample is its customer count, the gating threshold is stated in customers.

The confidence-monotonicity check is retained unchanged.

### A band that misses its gate publishes no interval

A band whose measured coverage does not hold is withheld: the artifact names the bands it stands
behind in `published_bands`, and a month in any other band reports
`quantile_unavailable_reason = UNCALIBRATED_INTERVAL` with no `p10` or `p90`.

ADR 0003 already refuses to publish a quantile it cannot calibrate. Annual quantiles stay absent
because deriving them needs a dependence structure nobody has measured, and an absent quantile is
never a point estimate widened by a guess. The low band now fails the same test at scale, and the
same answer applies. An interval labelled `80%` that contains the truth `58%` of the time is worse
than no interval, because a reader sizing risk off the band is misled in the direction that costs
them.

Withholding is not a way to pass by publishing almost nothing. Promotion additionally requires at
least one published band and that published months cover more than half the evaluation population.

### Every evaluation suite is gated on its own coverage

Coverage is measured and gated per scenario as well as per band. The three calibration suites report
coverage that shares no common value:

| Suite | Coverage | Customers | Published rows |
| --- | --- | --- | --- |
| `income_diverse` | `0.347` | 36 | 329 |
| `incomplete_observation` | `0.945` | 34 | 361 |
| `life_events` | `1.000` | 34 | 408 |

Pooled, these average to `0.786`, which reads as a healthy result against nominal `0.80` and
describes none of them. The band gate does not catch it: `income_diverse` contributes 209
high-confidence months and still covers a third of them.

That is the same defect this ADR was written to fix, one level up. Conditioning on confidence fixes
pooling across bands and does nothing about pooling across income profiles, because the confidence
score does not separate them. The consequence is stated plainly: **the confidence score is not a
sufficient conditioning variable for interval coverage.** It was assumed to be one, and the
measurement says otherwise.

The earlier observation that complete-consent months cover `0.708` against partial-consent `0.945`
was a shadow of this. `income_diverse` and `life_events` are the full-consent suites, and pooling a
scenario at `0.347` with one at `1.000` produced the split.

### The evaluation population must be large enough to resolve a band

Calibration and promotion run at `240` customers per suite, not `80`.

At `80` the test partition held `39` customers and the measurement was dominated by which customers
happened to land in it. The high band appeared to over-cover at `0.939` on `33` customers, which was
the failure blocking promotion; at `240` the same band sits at `0.776` on `91`. The effect was
composition, not calibration.

| Band | 80 per suite | Customers | 240 per suite | Customers |
| --- | --- | --- | --- | --- |
| overall | `0.878` | 39 | `0.761` | 108 |
| high | `0.939` | 33 | `0.776` | 91 |
| medium | `0.867` | 39 | `0.798` | 104 |
| low | `0.672` | 18 | `0.580` | 47 |

Only one finding survives the change in sample size, and it reverses in meaning: the low band
under-covers, at `0.580` against nominal, `3.3` clustered standard errors low. At `80` per suite its
error bar was wide enough that the same defect passed.

### The capacity binding records a hash, not a version string

The artifact records the capacity artifact's SHA-256 alongside its version.

ADR 0003 claims that recording the capacity model version makes a mismatch "visible rather than
silent". Measured against the contract `1.6` refit, it does not. The capacity blob changed while
`model_version` stayed `capacity-gbdt-stumps-0.5.0`, and the calibration bound to that same
unchanged string, so a calibration fitted against a different model would have loaded without
complaint. A content hash closes that, and the version string stays for readability.

## Consequences

- Calibration artifact schema moves from `1.0` to `1.1`. `lower_log_offset` and `upper_log_offset`
  remain as the documented global fallback, so a reader that supplies no band keeps the current
  behavior rather than failing.
- `ConformalIntervalModel.interval_minor` accepts the confidence score. `combine_month` already
  computes that score before it builds the interval, so no caller has to derive anything new.
- High-band intervals narrow substantially and low-band intervals widen by roughly a factor of
  three. This is a visible product change and the intended one: interval width starts tracking
  uncertainty instead of estimate size.
- The zero-income branch is untouched. A predicted zero is a gate decision, and ADR 0003's treatment
  of it stands.
- Calibration is versioned `conformal-intervals-0.8.0`, so the `0.7` artifact and its measured
  numbers remain readable rather than being overwritten by a method that no longer matches them.
- A per-band gate can fail while the pooled figure passes. That is the point, and it means the gate
  can now block a promotion that ADR 0003's gate would have allowed.
- Per-band offsets move every band toward nominal but do not reach it: high `0.982` to `0.939`,
  medium `0.845` to `0.867`, low `0.397` to `0.672`. The remaining gap is a mismatch between the
  residuals the offsets are fitted on and the residuals the model actually makes, not a banding
  problem. Only `21` of `2236` calibration rows change band between fold-model and promoted-model
  scoring, so which model assigns the band is not what matters.
- Raising the fold count from `5` to `10` was measured and rejected. The hypothesis was that fold
  models trained on `4/5` of the data produce residuals too wide; more folds made over-coverage
  worse, not better, at pooled `0.8974` against `0.8782`. Fold count is not the lever.
- The high-band over-coverage that blocked promotion at `80` customers per suite was an artifact of
  that sample. It disappears at `240`. Diagnosing it cost two rejected hypotheses, fold count and
  calibration-versus-inference residual scale, before the population size turned out to be the
  variable that mattered.
- Low-confidence months lose their interval. Coverage on the published set is `0.786` against
  nominal `0.80`, well inside tolerance, on `104` customers.
- Withholding the low band also repairs zero-truth coverage, which rises from `0.772` to `0.909`.
  Every uncovered zero-truth row sat in the low band, where the interval excluded a zero the model
  should not have ruled out.
- `conformal-intervals-0.8.0` does not promote. Per-band and pooled coverage pass; the
  `income_diverse` suite fails at `0.347`, and the per-suite gate blocks it. That is the correct
  outcome and it is the finding this milestone produced.
- The pooled `0.786` figure would have promoted under every gate this repository had before today,
  including the one written earlier in this same ADR. Three successive gates each passed a defect
  that the next one caught: ADR 0003's pooled gate hid the band split, the band gate hid the suite
  split, and only measuring suites separately exposed a scenario covering a third of its nominal
  rate.
- The remaining work is not another conditioning dimension bolted onto the same method.
  `income_diverse` carries roughly five to sixteen times the sustainable-income error of the other
  two suites, and a single set of offsets fitted across all three cannot serve it. Either the
  offsets condition on something that actually tracks error scale, or the capacity model's error on
  mixed income profiles comes down first. Choosing between those is the next milestone.
- Interval width now tracks uncertainty across bands, zero-truth coverage rises from `0.772` to
  `0.909`, and the low band no longer publishes an interval it cannot support. Those results stand
  independently of the promotion decision and are the reason the artifact is kept rather than
  discarded.
