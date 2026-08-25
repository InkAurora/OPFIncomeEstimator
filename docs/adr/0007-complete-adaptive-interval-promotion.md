# ADR 0007: Complete adaptive interval promotion

- Status: Proposed
- Date: 2026-08-25
- Supersedes: the promotion semantics of [ADR 0005](0005-conditional-conformal-calibration.md) and
  the single-widening conformal step of
  [ADR 0006](0006-uncertainty-protocol-and-gate-semantics.md)
- Calibration contract: `adaptive-intervals-0.9.0`
- Capacity contract: `capacity-gbdt-stumps-0.6.0`
- Estimator milestone: `0.9`

## Context

ADR 0006 promoted `conformal-intervals-0.8.0` with two of three bands publishing and stated, in the
same document, that it intends every band to publish and that the intent was not met. A gate that
records `PROMOTED` alongside "this ADR intends every band to publish, and that intent is not yet
met" is not measuring what the product ships.

Three defects make that outcome possible, and they compound.

### Promotion did not mean complete promotion

`calibrate_quantiles.py` built the artifact *from* the final-test result: a band that missed its
floor was removed from `published_bands`, and the artifact was then measured with that band absent.
A failing band therefore could not fail. It withdrew, the remaining rows were scored, and promotion
required only that the survivors cover "more than half" the evaluation population.

Under that rule the low band's `0.7026` against a floor of `0.7490` cost nothing. 770 of 8640
supported months lost their interval and the run still recorded `PROMOTED` with an empty failure
list. Withholding was introduced by ADR 0005 as an honest refusal to publish an uncalibrated band,
and it is that. It is not a promotion criterion, and using it as one lets the gate's own output
choose the thing being gated.

### One widening cannot serve three bands

The conformal step corrected both tails of every band by a single constant, the finite-sample `0.90`
quantile of the pooled conformity score `max(lower - residual, residual - upper)`.

High and medium supplied 7,325 of the 8,016 calibration scores, 92% of the mass, and both
over-covered at `0.9146` and `0.9132`. The pooled quantile they chose was **negative**, `-0.0077`.
A negative widening narrows. Every band was shrunk, including the one band already below its floor.
The low band's own scores were outvoted roughly twelve to one by bands that needed the opposite
correction.

Taking the maximum of the two tails also collapses two claims into one. `p10` promises the truth
falls below the lower bound at most a tenth of the time; `p90` promises the same above the upper
bound. A joint `80%` coverage figure is satisfied by a lower tail missing `0.02` and an upper tail
missing `0.18`, and nothing in the `0.8` report could tell the difference.

### The sharpness policy was optional

`--interval-score-ceiling` defaulted to `None`, which made the Winkler check report-only unless a
caller opted in. No caller did. ADR 0006 states that the interval score "is what prevents
withholding or infinite widths from passing a one-sided coverage gate", and then shipped it
disabled. A one-sided coverage gate with no active sharpness gate can be satisfied by widening.

### Two artifacts shared one name

The feature-`1.2.0` capacity retrain, fitted on 510 training customers, was written over
`capacity-estimator-0.5.0.json` while keeping `model_version: capacity-gbdt-stumps-0.5.0`. The
committed `0.5.0` was fitted on 174 customers under feature set `1.1.0`. For one milestone the two
were distinguishable only by content hash.

This is the defect ADR 0005 added the hash binding to catch, reappearing one level up: the hash
correctly identified *which bytes* the calibration was fitted against, and the version string still
named the wrong model.

## Decision

### Artifacts are written, never overwritten

| Artifact | Identity | Status |
| --- | --- | --- |
| `capacity-estimator-0.5.0.json` | `capacity-gbdt-stumps-0.5.0`, features `1.1.0`, 174 customers | superseded, unread |
| `capacity-estimator-0.6.0.json` | `capacity-gbdt-stumps-0.6.0`, features `1.2.0`, 510 customers | promoted, read by everything |
| `quantile-calibration-0.8.0.json` | `conformal-intervals-0.8.0` | frozen, limited promotion |
| `quantile-calibration-0.9.0.json` | `adaptive-intervals-0.9.0` | candidate |

`0.5.0` is restored to its committed bytes. `0.8.0` is frozen byte-for-byte and is not regenerated;
only the `promotion.status` in its *report* changes, from `PROMOTED` to `LIMITED_PROMOTION`, because
the artifact's measured numbers are still true and the claim made on top of them was not. It remains
useful twice over: as the research result ADR 0006 records, and as the fixed-band model the
sharpness gate measures against.

One thing this does **not** restore. `conformal-intervals-0.8.0` names two different models across
ADR 0005 and ADR 0006. The ADR 0005 artifact was the pre-adaptive band-conformal fit; the file
carrying that name today is ADR 0006's adaptive fit, which overwrote it, and the script that
produced the earlier one no longer exists. Recovering it would mean restoring that script and
rerunning, and it is not attempted, because the thing ADR 0007 actually needs from a fixed-band
model is a baseline measured on *the same final rows as the candidate*. A stale file cannot supply
that. The sharpness baseline is therefore rebuilt in-run from the same band offsets, and the
recorded ADR 0005 numbers stay in ADR 0005 where they are already written down.

The `capacity_artifact_sha256` recorded in `0.8.0` is left dangling by that rename, deliberately. It
identifies bytes that are now `capacity-estimator-0.6.0.json` with a corrected `model_version`. The
pre-rename hash is carried forward as `pre_rename_artifact_sha256` in the `0.6.0` report so the
binding is traceable rather than silently broken.

Calibration artifact schema moves to `1.2`. The reader accepts `1.0`, `1.1`, and `1.2`; the writer
emits only `1.2`. A schema `1.1` artifact keeps its exact previous behavior, because the fields
`1.2` adds are absent and absence is the documented fallback.

The runtime CLI default is unchanged until `0.9.0` promotes. Rollback is repointing `--calibration`
at the previous artifact, or omitting it, and no file has to be recovered to do that.

### Promotion means complete promotion

The artifact's shape no longer depends on the measurement. `published_bands` is always every band,
the candidate is built once, and the final test decides one thing: whether that artifact promotes.

Promotion requires all of:

- every gated band passes its coverage floor, and a gated band that fails is a promotion failure
  rather than a band that removes itself;
- `published_rows == row_count`, so no supported month is missing an interval;
- `published_bands == {high, medium, low}`;
- a sharpness result exists. There is no configurable ceiling and no way to run without one.

The "more than half the population" allowance is removed. It existed to stop withholding from
passing a gate by publishing almost nothing, which is a weaker statement than the one actually
needed: withholding must not be able to pass a gate at all.

### Each band corrects each tail on its own scores

The learned quantile models are unchanged. The single conformal widening is replaced by one
correction pair per confidence band, fitted on that band's own calibration scores:

- lower-tail score: `predicted_lower - realized_residual`;
- upper-tail score: `realized_residual - predicted_upper`;
- each correction is the finite-sample-adjusted `0.90` quantile of its own tail's scores,
  `ceil((n + 1) * 0.90) / n`;
- published bounds are `predicted_lower - lower_adjustment` and `predicted_upper + upper_adjustment`;
- both are clamped so `lower <= 0 <= upper`, which keeps every interval bracketing `p50`. A
  correction may narrow a tail; it may never move it past the point estimate.

Both scores are positive when the truth fell outside the learned band on that side and negative when
the band already had room, so a quantile of either widens a tail that is too tight and tightens one
that is too loose. Because each tail is fitted separately, the lower bound is a `p10` claim and the
upper bound a `p90` claim, and each is answerable on its own.

The artifact carries these as `band_adjustments`, one `BandAdjustment` per band holding both
corrections and the score count they were fitted on, rather than two parallel maps. The pair is
never meaningful half-present, and the count is the provenance the thin-band rule is judged against.

The pooled widening stays in the artifact as the documented fallback for a band whose scores are too
thin to fit its own pair, below the `100` that ADR 0005 set so a nearest-rank tail quantile rests on
at least ten observations. That fallback is a safety valve, not a calibration, and a run that uses
it does not promote.

### Both tails are gated

Coverage remains gated one-sided per suite and per band. Each tail is additionally gated on its own
miss rate against `0.10`, one-sided in the same direction: missing less often than promised is not a
failure, and the interval score is what charges for the width that buys.

Tail tolerance is `max(0.025, 2 clustered standard errors)`, half the interval's base tolerance,
because each tail carries half the interval's miss budget. Standard errors are measured by
resampling customers, as ADR 0005 requires for every rate reported here.

### Sharpness is mandatory and relative

The candidate's mean Winkler score is compared per suite against the fixed-band conformal model
built from the same band offsets and measured on the same final rows. The candidate must be no
worse. Baseline score, candidate score, their ratio, both mean widths, both coverages, and the
pass/fail are stored in the report.

Comparing against a frozen model on identical rows is what makes the one-sided coverage gate safe.
An absolute ceiling would have to be chosen, and any value chosen after seeing the candidate is a
value the candidate passes.

Point WAPE is asserted equal between candidate and baseline. The interval never moves the point
estimate, so a difference there means the two were measured on different rows, and that is a
measurement bug rather than a model result.

## Consequences

Measured on 720 final-test customers, 8,640 rows, seeds `510_000`–`530_000`, against the frozen
`capacity-gbdt-stumps-0.6.0`.

### The corrections the pooled widening was hiding

| Band | `0.8` lower | `0.8` upper | `0.9` lower | `0.9` upper | Scores |
| --- | --- | --- | --- | --- | --- |
| high | `-0.0077` | `-0.0077` | `-0.000584` | `-0.007700` | 4,322 |
| medium | `-0.0077` | `-0.0077` | `-0.017602` | `-0.011602` | 3,003 |
| low | `-0.0077` | `-0.0077` | **`+0.123644`** | `+0.007916` | 691 |

The low band needed its lower tail widened by `+0.1236`. It was being narrowed by `-0.0077`, a
correction sixteen times too small and pointing the wrong way, because high and medium outvoted it
roughly twelve to one. Its own two tails also disagree by a factor of fifteen, `+0.1236` against
`+0.0079`, which a single symmetric correction cannot express at any magnitude.

### Every band publishes, and every band passes

| | `0.8` | `0.9` |
| --- | --- | --- |
| rows receiving an interval | 7,870 of 8,640 | **8,640 of 8,640** |
| published bands | high, medium | high, medium, low |
| low band coverage | `0.7026`, withheld | **`0.7987`**, published |
| high band coverage | `0.9146` | `0.9174` |
| medium band coverage | `0.9132` | `0.9103` |
| published coverage | `0.9140` | `0.9039` |
| zero-truth coverage | `1.000` | `0.9983` |

Every band clears its `0.75` floor and every band passes both tail gates. The 770 months that ADR
0006 recorded as publishing no interval now publish one that holds.

Per-suite coverage moves slightly, `income_diverse` `0.7788` to `0.7670` and
`incomplete_observation` `0.9688` to `0.9618`, and the two figures are not comparable: `0.8` measured
each suite over published rows only, excluding 394 and 376 low-band months that carry that suite's
worst error. `income_diverse` WAPE reads `0.1273` here against `0.1036` there for the same reason.
The `0.9` figures cover the whole population and the `0.8` figures do not.

### Separating the tails found a defect coverage could not

`income_diverse` clears its coverage floor at `0.7670` and fails its upper tail at `0.1372` against a
ceiling of `0.1250`. Its lower tail is fine at `0.0958`. A joint `80%` figure inside its floor was
concealing a `p90` that holds `86%` of the time, which is the exact substitution ADR 0006's single
correction made structurally possible and the `0.8` report had no field to display.

### The sharpness gate blocks, and one of its two verdicts is contested

| Suite | Baseline coverage | Candidate coverage | Baseline width | Candidate width | Score ratio |
| --- | --- | --- | --- | --- | --- |
| `income_diverse` | `0.5319` | `0.7670` | 71,770 | 286,952 | `1.194` |
| `incomplete_observation` | `0.9844` | `0.9618` | 134,664 | 162,927 | `1.269` |
| `life_events` | `0.9167` | `0.9830` | 63,638 | 75,499 | `0.910` |

`incomplete_observation` is a clean loss. The fixed-band model covers `0.9844` against the
candidate's `0.9618` in 21% less width. It is a valid `80%` interval and it is strictly sharper, so
the candidate has no defence there.

`income_diverse` is not a clean loss and should not be read as one. The baseline that outscores it
covers `0.5319` against nominal `0.80`. It wins the Winkler comparison because it is four times
narrower and its misses are near misses, which the `2/alpha` penalty charges less than the width it
saves. Taken literally, "no worse than the frozen fixed-band baseline" asks the candidate to reach
nominal coverage without paying more than a model that never reaches it does, and on this suite that
may be unreachable by construction rather than by deficiency. That is the shape of the objection
ADR 0006 raised against two-sided per-suite coverage gating: a criterion no correct behavior can
satisfy is measuring the wrong thing.

The gate is left as specified and the result is left failing. Constraining the sharpness baseline to
models that themselves clear the coverage floor is the obvious repair, and it is a decision about
what the gate means rather than an implementation detail, so it is not taken here.

### The candidate does not promote

`adaptive-intervals-0.9.0` records `NOT_PROMOTED` on three failures: the `income_diverse` upper tail,
and the two sharpness comparisons. Every other criterion passes, including the ones `0.8` could not
be judged against because withholding removed them from the measurement.

### Other effects

- Removing the fold machinery's last consumer from calibration was already done by ADR 0006; this
  ADR removes the `--interval-score-ceiling` flag, so there is no longer a way to run calibration
  with sharpness disabled.
- The clustered bootstrap now carries per-customer numerators and denominators instead of rebuilding
  the resampled row vector, and computes all three rates from one set of draws. Output is unchanged
  bit-for-bit; a full run costs six minutes instead of roughly twenty-five, which is what makes
  gating three rates per segment affordable.
- A schema `1.1` artifact still reads and still behaves exactly as it was measured, because
  `band_adjustments` is absent and absence is the documented fallback to the pooled widening. The
  frozen `0.8` artifact is covered by a test that asserts this.
- `capacity-gbdt-stumps-0.5.0` is now unread by anything. It cannot be meaningfully evaluated under
  feature set `1.2.0`, which changed what its inputs mean, and it is retained only so that the `0.5`
  milestone stays reproducible.

## Open decision

The sharpness gate fails `income_diverse` against a baseline covering `0.5319`. Whether that verdict
stands is a question about what the gate means, and it is open:

- **As specified.** The candidate must be no worse than the frozen fixed-band model, whatever that
  model's coverage. `income_diverse` stays failing, and closing it needs a model that reaches nominal
  coverage more cheaply than the current one, not a wider one.
- **Baseline constrained to valid intervals.** Compare only against baselines that themselves clear
  the coverage floor. `income_diverse` would then have no valid fixed-band comparison and would fall
  back to the coverage and tail gates alone; `incomplete_observation` would still fail, because its
  baseline covers `0.9844` and is genuinely sharper.

The second is the repair this ADR expects to be needed, by the same argument ADR 0006 used to reject
two-sided coverage gating: a criterion no correct behavior can satisfy measures the wrong thing.
It is not taken unilaterally, because it loosens a gate, and a gate loosened by the person whose
candidate it just blocked is worth nobody's confidence.

## Out of scope

This ADR covers artifact identity, promotion semantics, and the bandwise asymmetric correction. Four
items from the same proposal are deliberately not attempted here, and none of them is closed by
anything above:

- **The conformal unit is still the customer-month.** The 8,016 conformity scores are correlated
  rows, roughly twelve per customer, so the finite-sample correction is computed over a sample whose
  effective size is far smaller than its count. Until customer-clustered conformal risk control is
  implemented, no document may describe this result as carrying a finite-sample guarantee. The
  accurate description is empirical customer-disjoint calibration with customer-clustered error
  bars, which is what the reports say.
- **The supported distribution is still three suites.** `noisy_observation` and `high_volatility`
  are held-out stress suites where coverage falls to `0.491` and `0.125`. They are not yet part of
  calibration, and there is no runtime applicability check, so an out-of-distribution month still
  receives an interval labelled `80%` rather than
  `quantile_unavailable_reason = OUT_OF_CALIBRATION_DISTRIBUTION`.
- **The release seeds are not fresh.** Seeds `510_000`–`530_000` were inspected while selecting
  between scale and quantile conditioning, and again here. They are validation seeds, not a release
  lockbox, and a promotion measured on them is a validation result.
- **Capacity work on `high_volatility`.** Point WAPE there is `0.410`. Intervals cannot compensate
  for a bad point model, and widening them until coverage holds is exactly what the sharpness gate
  exists to refuse.

Non-goals, unchanged: annual quantiles, arbitrary deeper trees, support below simulator contract
`1.3`, UI changes.
