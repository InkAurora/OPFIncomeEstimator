# Model cards

One card per artifact the estimator reads or is a candidate to read. Each states what the artifact
does, the data it was fitted on, how it was measured, and where it is known to fail. Every card
opens with its promotion status; a card is not itself a promotion.

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

## `capacity-gbdt-stumps-0.6.0` — sustainable monthly income

**Task.** Predict `sustainable_monthly_income` for one customer-month.

**Inputs.** Feature set `customer-month-features-1.2.0`, 104 point-in-time features. Requires
estimator input `1.2` for the capacity group; on `1.0` or `1.1` those six features report
`CONTRACT_DOMAIN_UNAVAILABLE` and the model routes on what remains.

**Labels.** Private contract `income-targets-1.0`, joined only after observed features are built.
Requires simulator contract `1.3` or later.

**Method.** Hurdle. A logistic gate decides whether sustainable income is zero; an anchored
regressor boosts `log1p(sustainable)` around `log1p(income_mean_3m_minor)`. Decision stumps over
binned features, with a per-stump direction for missing values.

**Data.** 720 customers across `income_diverse`, `life_events`, and `incomplete_observation`, split
70/15/15 by customer. Customer-disjoint from every population used to fit or gate the interval.

**Measured.** Held-out MAE `25,217` against `74,469` for the best deterministic baseline, WAPE
`0.0499` on 109 test customers. Refitted against simulator contract `1.6` and feature set
`customer-month-features-1.2.0`.

**Version.** This model shipped for one milestone as `capacity-gbdt-stumps-0.5.0`, written over that
artifact's file while carrying its name, so a model trained on 510 customers under feature set
`1.2.0` was indistinguishable from one trained on 174 under `1.1.0`. ADR 0007 gives it `0.6.0` and
restores `0.5.0` to its own bytes. Every consumer reads `0.6.0`.

The `1.2.0` features exist because the model could see how many income sources a customer had and
how regular they looked, but never what they were worth per month. `detect_income_streams` reported
the median of *paying* months as the monthly amount, so a quarterly source of `9,000` read as
`9,000` rather than `3,000`, and `source_features` did not expose even that. Six features were added:
frequency-normalized source capacity, its largest component, cadence confidence, observation count,
source age, and a no-source flag. The retrained model splits on four of them in the regressor and
three in the gate, where cadence confidence is the fifth most-used feature.

**Known failure modes.**
- Still loses narrowly to the trivial cash-flow baseline on perfectly stable salaried income,
  `11,413` against `11,140`. Estimator `0.6` routes around this deliberately.
- Degrades sharply on income conditions absent from training. On the held-out high-volatility suite
  sustainable WAPE is `0.410`, an order of magnitude worse than on its training conditions.
- Trained on three suites only. Income profiles outside them are out of distribution and nothing
  currently detects that at inference time.

**Intended use.** Sustainable-income component inside estimator `0.6` routing. Not a standalone
product output.

---

## `adaptive-intervals-0.9.0` — sustainable income interval

**Status: NOT_PROMOTED.** Every band publishes and every band passes its coverage floor and both
tail gates. Three failures block promotion: the `income_diverse` upper tail, and the sharpness
comparison on `income_diverse` and `incomplete_observation`.

**Task.** Turn the routed sustainable-income estimate into a `p10`/`p90` pair.

**Method.** Conformalized quantile regression with bandwise asymmetric correction. Two boosted stump
ensembles predict this row's lower and upper log-residual quantiles under pinball loss. Each
confidence band then corrects each tail separately, at that tail's own finite-sample `0.90` quantile
fitted on untouched customers. See
[ADR 0007](../../docs/adr/0007-complete-adaptive-interval-promotion.md).

`0.8` corrected both tails of all three bands by one pooled constant. High and medium held 92% of the
conformity mass and both over-covered, so that constant came out negative, `-0.0077`, and narrowed
the low band that was already under its floor. The low band's own lower tail needed `+0.1236` and its
upper tail `+0.0079`; no single symmetric number expresses that.

Residuals are taken around the estimate `combine_month` publishes, routing included, not around the
capacity model's own prediction. Four customer-disjoint populations train the point model, train the
quantile model, correct it, and gate it; the report asserts zero shared customers.

**Measured.** On 720 final-test customers never used by any earlier stage, coverage `0.9039` against
nominal `0.80` on **8640 of 8640 rows**. By band: high `0.9174`, medium `0.9103`, low `0.7987`, each
against a floor of `0.7500`. By suite: `income_diverse` `0.7670`, `incomplete_observation` `0.9618`,
`life_events` `0.9830`. Zero-truth coverage `0.9983`. Tail miss rates overall: lower `0.0363`, upper
`0.0597` against `0.10` each.

**Gate.** Coverage is one-sided on under-coverage, per suite and per band. Each tail is additionally
gated on its own miss rate against `0.10`, because a joint `80%` figure is satisfied by a lower tail
missing `0.02` and an upper missing `0.18`. Sharpness is mandatory and unconditional: the Winkler
score is compared per suite against the fixed-band conformal model on the same rows, as a one-sided
non-inferiority test on the paired per-row difference against a margin declared in advance, `2%` of
that suite's baseline score. Every band publishes unconditionally; the measurement decides only
whether the artifact promotes, never its shape. Error bars are measured by resampling customers
rather than months.

**Known failure modes.**
- `income_diverse` clears its coverage floor at `0.7670` while its upper tail misses `0.1372` against
  a ceiling of `0.1250`. The published `p90` holds about `86%` of the time on that suite.
- Sharpness fails on two suites. On `incomplete_observation` this is unambiguous: the fixed-band
  model covers `0.9844` against `0.9618` in 21% less width. On `income_diverse` the baseline that
  outscores it covers `0.5319`, so "no worse than the baseline" there is being asked of a model that
  reaches nominal coverage against one that does not.
- The conformal unit is the customer-month, not the customer. The 8,016 scores are roughly twelve
  correlated rows per customer, so this is empirical customer-disjoint calibration with
  customer-clustered error bars, **not** a finite-sample guarantee, and must not be described as one.
- Coverage does not extend outside the calibration distribution, and nothing detects that at
  inference time. The stated `80%` is a claim about conditions resembling the three calibration
  suites and nothing wider. **How far it falls outside them is unmeasured for this artifact.** The
  `0.491` noisy and `0.125` high-volatility figures quoted elsewhere were measured on
  `conformal-intervals-0.8.0`, in a run whose capacity binding cannot be reconstructed; see
  `evaluation/baselines/README.md`. They are the reason to expect trouble, not a measurement of
  `0.9`.
- The recorded failures were produced by the earlier sharpness form, an unpaired ratio of means with
  no error bar and no declared margin. The gate is now a paired non-inferiority test, but the
  numbers in the report predate it. They are large enough not to turn on the difference: the
  candidate spends `19%` more score than the baseline on `income_diverse` and `27%` on
  `incomplete_observation`, against a `2%` margin.
- Final-test seeds `510_000`–`530_000` have been inspected across several method-selection rounds.
  They are validation seeds permanently, recorded as `populations.final_test_role`, and a release
  lockbox is reserved from seed `610_000` upward to be drawn once every gate passes on validation.
- Annual quantiles are not produced. Deriving them from monthly quantiles needs a dependence
  structure across months that nobody has measured.
- Must be refitted whenever the capacity model changes. The artifact records the capacity artifact's
  SHA-256, because the version string alone did not detect the contract `1.6` refit.

**Intended use.** Candidate. The runtime default is unchanged until it promotes.

---

## `conformal-intervals-0.8.0` — superseded, limited promotion

**Status: LIMITED_PROMOTION.** Recorded `PROMOTED` under a gate that let a failing band withdraw
itself and still count. Two of three bands publish and roughly 9% of supported months receive no
interval, so it is a research result and a frozen comparison baseline, not a release. The artifact is
frozen byte-for-byte; only the promotion claim in its report was downgraded.

**Method.** Conformalized quantile regression with one pooled widening for both tails of every band.
See [ADR 0006](../../docs/adr/0006-uncertainty-protocol-and-gate-semantics.md).

**Measured.** Published coverage `0.9140` on 7870 of 8640 rows. High `0.9146`, medium `0.9132`, low
`0.7026` against a floor of `0.7490` and withheld. Per-suite figures in its report cover published
rows only, so they are not comparable with `0.9`'s whole-population figures.

**Known failure modes.**
- The low confidence band covers `0.7026` against a floor of `0.7490` and is withheld, so about 9% of
  supported months report `quantile_unavailable_reason = UNCALIBRATED_INTERVAL` and receive no
  interval at all.
- Both tails of every band share one pooled correction, `-0.0077`, so `p10` and `p90` are two halves
  of a joint `80%` claim rather than two quantiles that hold separately. The report has no field that
  would show one tail paying for the other.
- Coverage does not extend outside the calibration distribution, and nothing detects that at
  inference time: `0.491` on the held-out noisy suite and `0.125` on high-volatility.
- Calibration is over customer-months rather than customers, so this is empirical customer-disjoint
  calibration with customer-clustered error bars, not a finite-sample guarantee.
- Its `capacity_artifact_sha256` is dangling. It names bytes now stored as
  `capacity-estimator-0.6.0.json` under a corrected `model_version`; the pre-rename hash is recorded
  in that model's report.
- Annual quantiles are not produced.

**Retained for.** The ADR 0006 result itself, and nothing else. It is not the sharpness comparator:
that model is rebuilt in-run from the same calibration rows, bound to the same capacity bytes as the
candidate, because the comparison only means anything measured on the same final rows. It is not a
rollback target either — the runtime now checks the binding a calibration records, `f4f10e8d...`
matches no file here, and loading this artifact raises `CalibrationBindingError`. Until a matching
promoted calibration exists, the rollback is no intervals.

---

## Not promoted

`transaction-gbdt-stumps-0.3.0` tied the rule baseline on held-out F1 at `0.99552372` with zero
critical false positives. Promotion requires strict improvement, so it stays available for explicit
experiments and is never the default.
