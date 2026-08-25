# ADR 0006: Uncertainty protocol and gate semantics

- Status: Accepted
- Date: 2026-08-24
- Supersedes: the calibration protocol of [ADR 0003](0003-interval-and-confidence-semantics.md) and
  the gate semantics of [ADR 0005](0005-conditional-conformal-calibration.md)
- Calibration contract: `adaptive-intervals-0.9.0`
- Estimator milestone: `0.9`

## Context

ADR 0005 refused to promote `conformal-intervals-0.8.0` because coverage differed by suite:
`income_diverse` at `0.347`, `incomplete_observation` at `0.945`, `life_events` at `1.000`. It
concluded that the confidence score is not a sufficient conditioning variable and proposed
conditioning on something else.

Repository inspection says that conclusion, while true, was reached against an invalid benchmark.
Three defects in the measurement apparatus each shift the number being judged, and a fourth makes
part of the gate unsatisfiable in principle. Improving the model against this benchmark would be
measuring the wrong thing more precisely.

### The calibration measures a model that is never shipped

The capacity model trains on seeds `110_000`, `120_000`, `130_000`. Calibration generates seeds
`410_000`, `420_000`, `430_000`. The two populations are customer-disjoint, so the frozen `0.5`
artifact has never seen a calibration customer, and its residuals on that population are already
out of sample.

ADR 0003 nonetheless refits the hurdle once per fold, reasoning that "the `0.5` artifact cannot
supply them: its residuals on `train` are in-sample". That reasoning assumed calibration would reuse
the capacity training population. It does not. The leakage the fold machinery exists to prevent was
already prevented by the disjoint seeds.

What the machinery does instead is calibrate the wrong model. Fold models are refitted on roughly
`490` calibration customers; the shipped artifact was trained on `174`. Offsets fitted to the error
of a model trained on nearly three times the data are then applied to a weaker one. Calibrating the
frozen model directly raises `income_diverse` coverage from `0.347` to `0.461` with no other change.

### The gate scores a number the product does not publish

Calibration measures coverage around `model.predict_minor`. Production publishes the estimate
`combine_month` routes to, which selects `cash_flow_last_month` whenever income looks stable and
`capacity_model` otherwise. On `income_diverse` the split is roughly 62/38, so the gate scores the
capacity prediction on months where the product ships a different number.

Measured end to end through the routed estimate, coverage is approximately `0.498`, `0.986`, `0.917`
across the three suites, not the `0.347`, `0.945`, `1.000` the report states.

### More data helps, and not enough

Retraining capacity at `240` customers per suite improves `income_diverse` sustainable WAPE from
`0.199` to `0.138` on an untouched population. Coverage rises only to `0.530`. The gap is a real
model deficiency and cannot be closed by population size alone.

### Two-sided per-suite gating is unsatisfiable

`life_events` failed ADR 0005's gate at coverage `1.000`. On a suite whose point estimate is often
exactly right, every interval containing the point contains the truth, including the degenerate
`[point, point]`. Coverage of `1.000` there is a property of an accurate estimate, not of a bad
interval, and no interval width can lower it. Treating over-coverage as symmetric with
under-coverage rejects correct behavior.

Under-coverage misleads: a band labelled `80%` that holds `35%` of the time understates risk.
Over-coverage wastes precision. They are different failures and need different tests.

## Decision

### Four customer-disjoint populations

| Population | Purpose | Seed range |
| --- | --- | --- |
| capacity-train | fit the point model | `110_000`+ |
| uncertainty-train | fit the residual-scale model | `210_000`+ |
| conformal-calibration | fit conformal offsets | `410_000`+ |
| final-test | measure and gate | `510_000`+ |

No customer appears in two of them, and no stage reuses a population an earlier stage consumed.
Calibration never refits the capacity model: it loads the frozen artifact and measures it.

### Residuals are fitted around the routed estimate

Residuals are computed against the exact `p50` that `combine_month` publishes, including its routing
choice between components. An interval is a claim about the number the product shows, so it must be
calibrated against that number.

### Under-coverage is gated one-sided, per suite

Every suite must satisfy `coverage >= nominal - tolerance`. Exceeding nominal is never a gate
failure.

### Width is gated separately, by interval score

Sharpness is judged by the mean Winkler interval score, which charges the width of every interval
and adds a penalty proportional to how far the truth falls outside it. A degenerate `[point, point]`
interval on an exact prediction scores zero, which is correct: it is the best possible answer. A
vacuously wide interval scores badly even though it covers everything.

This is what prevents withholding or infinite widths from passing a one-sided coverage gate.

### The hurdle is represented in the interval

When the model's estimated probability of zero income is at least `10%`, `p10` is zero regardless of
the point estimate. A positive log-space interval cannot express "probably something, possibly
nothing", and the current construction excludes zero categorically whenever the point estimate is
positive.

### Conditioning is learned, not banded

Fixed confidence bands are replaced by a model of residual scale fitted on observable features:
history length, volatility, source recurrence and count, component disagreement, classification
certainty, and consent coverage. Its output is conformalized on the untouched calibration population
so the measured coverage survives.

Band withholding was the correct answer to an uncalibrated band and is not the intended end state.
All bands publish once the adaptive model passes its gates.

### Capacity work follows the protocol repair, not the reverse

The capacity model is improved only after the above lands, so improvements are measured against a
benchmark that scores the shipped model on the shipped number.

Three changes are in scope, in order:

1. Frequency-normalized stream capacity. `detect_income_streams` reports
   `expected_monthly_amount_minor` as the median of *paying* months, so a quarterly source of `9,000`
   reports `9,000` rather than `3,000`. Monthly, biweekly, quarterly, and irregular sources are
   normalized to a monthly rate.
2. Exposure of that value. `source_features` publishes counts, concentration, and recurrence scores
   but never the streams' own capacity estimate, so the model cannot use it at all.
3. Identifiability features: observations per source, inferred-frequency confidence, source age,
   count of detected sources, and usable history length.

Model capacity is increased only after these direct features are measured, because a deeper model
fitted over missing information learns to compensate for an absence rather than use a signal.

## Completion gate

`adaptive-intervals-0.9.0` promotes when, on frozen final-test seeds, every suite passes all four:

- under-coverage: `coverage >= nominal - tolerance`;
- zero-truth coverage: at or above nominal;
- interval score: at or below the documented ceiling;
- point error: no regression against the promoted capacity model.

No interval-free release is required or intended.

## Consequences

Measured with the protocol repair alone, before any model change:

| | ADR 0005 protocol | ADR 0006 protocol |
| --- | --- | --- |
| published coverage | `0.786` | `0.8065` |
| high band | `0.776` | `0.8045` |
| medium band | `0.798` | `0.8200` |
| low band | `0.580`, withheld | `0.7588`, published |
| `income_diverse` | `0.347` | `0.5135` |
| `incomplete_observation` | `0.945` | `0.9892` |
| `life_events` | `1.000`, failed | `0.9167`, passes |
| rows receiving an interval | 1098 of 1248 | 8640 of 8640 |

- Every band passes its floor, so band withholding is no longer needed and no month loses its
  interval for want of calibration. ADR 0005's withholding machinery stays in the artifact as a
  safety valve and is inert.
- The one-sided gate stops failing `life_events` for having an accurate point estimate, which is
  what a two-sided gate could never avoid.
- `income_diverse` remained below its floor at `0.5135` after the protocol repair alone. Capacity
  work and adaptive calibration then closed it, in that order:

| Stage | `income_diverse` coverage | Overall | Zero-truth |
| --- | --- | --- | --- |
| ADR 0005, band conformal | `0.347` | `0.786` | `0.909` |
| protocol repair | `0.5135` | `0.8065` | `0.86` |
| capacity retrain, `1.2.0` features, hurdle `p10` | `0.5319` | `0.8110` | `0.9983` |
| conformalized quantile regression | **`0.7788`** | `0.9140` | `1.000` |

- A residual *scale* model was fitted first and rejected on measurement. It spread predictions 55x
  across suites in the correct order, but squared error on the log absolute residual estimates a
  geometric mean, which sits below the tail: realized residuals on `income_diverse` ran `1.5x` its
  prediction at the median and `3.4x` at the 90th percentile, leaving coverage at `0.546`. A scale
  corrects the level of a residual distribution and not its shape. Learned quantiles under pinball
  loss correct both, which is why the final model predicts the band directly.
- The capacity retrain improved `income_diverse` sustainable WAPE from `0.164` to `0.127` and moved
  coverage by two points. That gap is the evidence that coverage is governed by the spread of the
  residual distribution rather than by average error, and it is why the model work alone could never
  have closed it.
- The hurdle rule carried zero-truth coverage from `0.86` to `0.9983`. A log-space band around a
  positive point estimate excluded zero categorically, however unsure the gate was.
- Interval score over mean truth separates the suites cleanly: `1.056` for `income_diverse` against
  `0.243` and `0.163`. A sharpness measure was needed to make one-sided coverage safe, and it turns
  out to rank the suites by the same deficiency coverage does.
- Dropping the fold refits removes three model fits per calibration run and pays for the extra
  populations.
- `conformal-intervals-0.8.0` promotes, with two of three bands publishing. The low band covers
  `0.7026` against a floor of `0.7490` and is withheld, so roughly 9% of months publish no interval.
  This ADR intends every band to publish, and that intent is not yet met.
- The guarantee does not extend outside the calibration distribution. On the held-out stress suites
  coverage falls to `0.491` on `noisy` and `0.125` on `high_volatility`. Those suites are excluded
  from calibration on purpose, and the measurement is what a stress suite exists to produce, but the
  published `80%` is a claim about conditions resembling the three calibration suites and nothing
  wider.
- `income_diverse` clears its floor while carrying by far the worst sharpness, an interval score of
  `0.844` against mean truth where the other suites sit near `0.2`. The one-sided gate permits this
  deliberately; the interval score is what keeps it visible.
- The `0.7` and `0.8` calibration artifacts and their reports are superseded. Their measured numbers
  described a model that was never shipped, scored on a value that was never published.
- `training/out_of_fold.py` loses its role in calibration. Fold assignment stays available for any
  future use that genuinely needs in-population honesty.
- The evaluation cost rises: four populations instead of two, and no fold refitting to amortize.
  Removing the per-fold model fits offsets most of it.
- ADR 0005's per-band and per-suite segmentation, its customer-clustered error bars, and its
  capacity-artifact hash binding are all retained. Only the gate's symmetry and the calibration
  protocol change.
