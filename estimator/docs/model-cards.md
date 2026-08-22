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

**Measured.** Held-out incomplete-observation MAE improves `99.25%` over the frozen `0.1` baseline
with no complete-data regression and no added false-income classification. Stress suites: realized
WAPE `0.0` on clean, normal, partial consent, life-events, and high-volatility; `0.0167` on the
noisy suite. No suite produces a false income month.

Measured against simulator contract `1.6`. Under contract `1.5` the noisy suite scored `0.179` and
partial consent `0.008`; that error was an artifact of reversals whose amount never returned to the
observed feed, not a property of these rules. See
[ADR 0004](../../docs/adr/0004-observed-balance-and-reversal-semantics.md).

**Known failure modes.**
- The noisy suite remains its only nonzero realized error. What is left is timing, not
  classification: a reversal's corrected re-post that has not arrived by the request cutoff carries
  income the estimator cannot yet see, and the reversed original it repairs is correctly excluded.
- Non-income credits shaped like income remain a structural weak point. The noisy suite contains an
  asset sale, a merchant refund, and an own transfer described as a PIX receipt.
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

**Measured.** Held-out MAE `24,200` against `53,892` for the best deterministic baseline, WAPE
`0.0443`. Improves full coverage, partial consent, volatile income, short history, and every income
band. Zero-income customers predicted exactly. Refitted against simulator contract `1.6`.

**Known failure modes.**
- Loses to the trivial cash-flow baseline on perfectly stable salaried income, `13,820` against
  `12,060`. Estimator `0.6` routes around this deliberately.
- Degrades sharply on income conditions absent from training. On the held-out high-volatility suite
  sustainable WAPE is `0.4645`, an order of magnitude worse than on its training conditions, and
  slightly worse than the `0.443` measured before the contract `1.6` refit.
- Trained on three suites only. Income profiles outside them are out of distribution and nothing
  currently detects that at inference time.

**Intended use.** Sustainable-income component inside estimator `0.6` routing. Not a standalone
product output.

---

## `conformal-intervals-0.7.0` — sustainable income interval

**Status: NOT PROMOTED.** The contract `1.6` refit fails the coverage gate this artifact exists to
satisfy. It is documented here because the artifact ships in the repository and the failure is the
finding, not an accident.

**Task.** Turn a sustainable-income point estimate into an `80%` interval.

**Method.** Split conformal on the log residual. Offsets are the empirical `0.1` and `0.9` quantiles
of out-of-fold residuals, collected from models refitted per fold that never saw the row.

**Measured.** Held-out coverage `0.8568` against nominal `0.80`, standard error `0.0185` on 468
rows. The declared tolerance is `0.05`, so `0.0568` from nominal fails it. Confidence stays
monotonic with relative error: WAPE `0.0277`, `0.0700`, `0.2491` across the high, medium, and low
bands.

**Why it fails.** A single global offset is fitted over a residual distribution that has become
strongly bimodal by consent segment. Repairing the reversal defect made the partial-consent segment
much more accurate, WAPE `0.0178` at coverage `1.00`, while the complete-coverage segment kept its
error, WAPE `0.0868` at coverage `0.777`. One offset over-covers the tight mode and under-covers the
wide one, and the pooled figure lands outside tolerance. Before contract `1.6` the same split
measured `0.98` against `0.76` and the pooled figure happened to land inside tolerance at `0.8365`.
The method did not degrade; a more accurate point estimate exposed a limitation
[ADR 0003](../../docs/adr/0003-interval-and-confidence-semantics.md) had already documented.

**Known failure modes.**
- Coverage is not uniform across confidence bands: `0.982`, `0.845`, `0.397` from high to low. A
  single global offset cannot serve every band, and low-confidence intervals under-cover badly.
  Conditional conformal calibration, with offsets fitted per band, is the documented next step and
  is now required rather than optional.
- Coverage collapses on unseen conditions. On the held-out high-volatility suite it falls to `0.30`,
  so the stated `80%` does not hold outside the calibration distribution.
- Annual quantiles are not produced. Deriving them from monthly quantiles needs a dependence
  structure across months that nobody has measured.
- Must be refitted whenever the capacity model changes. The artifact records the capacity model
  version it was fitted against so a mismatch is visible.

**Intended use.** None until it passes its gate. `tests/test_quantiles.py` fails while this artifact
is unpromoted, which is the intended behavior.

---

## Not promoted

`transaction-gbdt-stumps-0.3.0` tied the rule baseline on held-out F1 at `0.99552372` with zero
critical false positives. Promotion requires strict improvement, so it stays available for explicit
experiments and is never the default.
