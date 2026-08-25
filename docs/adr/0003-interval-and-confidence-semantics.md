# ADR 0003: Interval and confidence semantics

- Status: Accepted; the global-offset decision is superseded by
  [ADR 0005](0005-conditional-conformal-calibration.md)
- Date: 2026-08-22
- Extends: [ADR 0001](0001-income-target-definitions.md), [ADR 0002](0002-income-target-construction.md)
- Calibration contract: `conformal-intervals-0.7.0`
- Estimator milestone: `0.7`

## Context

Estimator `0.6` publishes a sustainable-income point estimate and a confidence score but no
interval, because output contract `1.1` refuses a quantile that was never calibrated. Milestone
`0.7` must supply intervals whose stated coverage survives measurement, and must show that
confidence tracks accuracy rather than merely looking plausible.

Three questions had no answer before this decision: which residuals may calibrate an interval, what
an interval means around a predicted zero, and whether annual quantiles can be derived from monthly
ones.

## Decision

### Calibration data

Intervals are calibrated on out-of-fold residuals only. The `0.5` artifact cannot supply them: its
residuals on `train` are in-sample, and `validation` was consumed twice already, once to select the
tree count and once to select the gate threshold. Calibrating on either would report intervals
tighter than they are, and coverage is the one number `0.7` exists to state honestly.

`training/out_of_fold.py` refits the hurdle once per fold and predicts only the fold it held out.
Folds are assigned by customer, never by row, because two months of one customer share almost every
feature. Each fold takes its own validation set from the next fold, so the held-out fold is
untouched by every choice its model makes about itself.

### Method

Split-conformal on the log residual. Empirical quantiles of the out-of-fold residual distribution
become fixed log offsets; prediction widens the point estimate by those offsets and back-transforms.
The method is distribution-free, so nothing assumes the residuals are normal, and they are not.

Quantile regression was rejected for this milestone: it needs a pinball-loss trainer and three
fitted models, where conformal reuses the promoted point model unchanged and adds two numbers.

### Intervals around a predicted zero

A predicted zero is a decision by the gate, not a small positive number, so it does not get a
symmetric band. A band below zero would imply negative income and a band above it would invent
precision the model never claimed.

When the gate is confident, below `zero_gate_certain_basis_points`, the interval is `[0, 0]`. That
is a falsifiable claim and the evaluation does falsify it when wrong. When the gate is unsure, the
lower bound stays zero and the upper bound comes from the positive branch, because the honest
statement is "probably nothing, but if something, about this much".

### Annual quantiles

`annual_income_p10/p50/p90` stay absent. Deriving them from monthly quantiles requires assuming a
dependence structure across months, and monthly incomes of one customer are strongly dependent, not
independent. Summing independent quantiles would understate the annual interval by a factor nobody
has measured. They remain `None` until a milestone models the dependence directly.

### Confidence monotonicity

Confidence is measured against relative error, not absolute error. Absolute error scales with
income, so a band containing richer customers shows larger errors regardless of how well the
estimator understands them; the first monotonicity check failed for exactly that reason and was
inverted by income scale alone. WAPE per confidence band is the measured quantity.

The band boundaries read the score estimator `0.6` actually publishes rather than a proxy for it.
An earlier version banded on data completeness alone and measured something the product never
shows.

## Consequences

- On the held-out partition, empirical coverage is `0.8365` against a nominal `0.80`, inside the
  documented `0.05` tolerance, with a coverage standard error of `0.0226` on 312 rows.
- Confidence is monotonic with accuracy: WAPE `0.024` for high, `0.069` for medium, `0.221` for low.
- Coverage is not uniform across confidence bands: `1.00` for high, `0.817` for medium, `0.412` for
  low. A single global offset cannot serve every band, and low-confidence intervals under-cover
  badly. Conditional conformal calibration, with offsets fitted per band, is the natural next step
  and is deliberately not attempted here.
- Calibration must be refitted whenever the capacity model changes. The artifact records the
  capacity model version it was fitted against so a mismatch is visible rather than silent.
- Coverage measured on a few hundred rows carries a standard error near two percentage points. The
  gate tolerance is set wider than that spread on purpose; tightening it requires a larger
  evaluation population, not a stricter threshold.
