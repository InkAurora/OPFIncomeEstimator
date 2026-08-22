# Model cards

One card per promoted artifact. Each states what the artifact does, the data it was fitted on, how
it was measured, and where it is known to fail. An artifact without a card is not promoted.

All measurements come from synthetic populations. They describe behavior against a simulator, not
accuracy on real clients, and no card here supports a production claim.

---

## `recurring-streams-0.2.0` — monthly realized income

**Task.** Reconstruct `realized_income_month` from observed credits. Deterministic rules only, no
trained model.

**Inputs.** Estimator input `1.0` or later: accounts, transactions, coverage, loan and investment
links.

**Method.** Precedence-ordered rules classify each credit with a reason code, description-based
clustering detects income streams, and stable streams fill month gaps only where measured account
coverage is incomplete. Complete-coverage zero months stay zero.

**Measured.** Held-out incomplete-observation MAE improves `97.36%` over the frozen `0.1` baseline
with no complete-data regression and no added false-income classification. Stress suites: realized
WAPE `0.0` on clean, normal, life-events, and high-volatility; `0.008` on partial consent;
`0.179` on the noisy suite.

**Known failure modes.**
- Non-income credits shaped like income are its weak point. The noisy suite, where an asset sale, a
  merchant refund, and an own transfer described as a PIX receipt all appear, is by far its worst
  realized error.
- Stream clustering falls back to normalized description because no adapter populates the optional
  counterparty fields of input `1.1`. Two payers sharing a description merge.
- Imputation requires measured incomplete coverage. A provider that under-reports without declaring
  it produces silent under-estimation.

**Intended use.** Default realized-income estimate, and the anchor every later component builds on.

---

## `capacity-gbdt-stumps-0.5.0` — sustainable monthly income

**Task.** Predict `sustainable_monthly_income` for one customer-month.

**Inputs.** Feature set `customer-month-features-1.1.0`, 98 point-in-time features. Requires
estimator input `1.2` for the capacity group; on `1.0` or `1.1` those six features report
`CONTRACT_DOMAIN_UNAVAILABLE` and the model routes on what remains.

**Labels.** Private contract `income-targets-1.0`, joined only after observed features are built.
Requires simulator contract `1.3` or later.

**Method.** Hurdle. A logistic gate decides whether sustainable income is zero; an anchored
regressor boosts `log1p(sustainable)` around `log1p(income_mean_3m_minor)`. Decision stumps over
binned features, with a per-stump direction for missing values.

**Data.** 240 customers across `income_diverse`, `life_events`, and `incomplete_observation`, split
70/15/15 by customer.

**Measured.** Held-out MAE `25,055` against `55,455` for the best deterministic baseline, WAPE
`0.0459`. Improves full coverage, partial consent, volatile income, short history, and every income
band. Zero-income customers predicted exactly.

**Known failure modes.**
- Loses to the trivial cash-flow baseline on perfectly stable salaried income, `15,444` against
  `12,418`. Estimator `0.6` routes around this deliberately.
- Degrades sharply on income conditions absent from training. On the held-out high-volatility suite
  sustainable WAPE is `0.443`, an order of magnitude worse than on its training conditions.
- Trained on three suites only. Income profiles outside them are out of distribution and nothing
  currently detects that at inference time.

**Intended use.** Sustainable-income component inside estimator `0.6` routing. Not a standalone
product output.

---

## `conformal-intervals-0.7.0` — sustainable income interval

**Task.** Turn a sustainable-income point estimate into an `80%` interval.

**Method.** Split conformal on the log residual. Offsets are the empirical `0.1` and `0.9` quantiles
of out-of-fold residuals, collected from models refitted per fold that never saw the row.

**Measured.** Held-out coverage `0.8365` against nominal `0.80`, standard error `0.0226` on 312
rows. Confidence is monotonic with relative error: WAPE `0.024`, `0.069`, `0.221` across the high,
medium, and low confidence bands.

**Known failure modes.**
- Coverage is not uniform across confidence bands: `1.00`, `0.817`, `0.412` from high to low. A
  single global offset cannot serve every band, and low-confidence intervals under-cover badly.
  Conditional conformal calibration is the documented next step.
- Coverage collapses on unseen conditions. On the held-out high-volatility suite it falls to
  `0.375`, so the stated `80%` does not hold outside the calibration distribution.
- Annual quantiles are not produced. Deriving them from monthly quantiles needs a dependence
  structure across months that nobody has measured.
- Must be refitted whenever the capacity model changes. The artifact records the capacity model
  version it was fitted against so a mismatch is visible.

**Intended use.** Interval around the routed sustainable estimate, inside the calibration
distribution only.

---

## Not promoted

`transaction-gbdt-stumps-0.3.0` tied the rule baseline on held-out F1 at `0.99552372` with zero
critical false positives. Promotion requires strict improvement, so it stays available for explicit
experiments and is never the default.
