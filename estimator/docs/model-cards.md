# Model cards

One card per artifact the estimator reads or is a candidate to read. Each states what the artifact
does, the data it was fitted on, how it was measured, and where it is known to fail. Every card
opens with its promotion status; a card is not itself a promotion.

All measurements come from synthetic populations. They describe behavior against a simulator, not
accuracy on real clients, and no card here supports a production claim.

Every metric quoted under `0.12.0` on `incomplete_observation` or `partial_consent` measured the
coverage oracle and is withdrawn. See [ADR 0010](../../docs/adr/0010-coverage-oracle-removed.md).

---

## `recurring-streams-0.3.0` — monthly realized income

**Task.** Reconstruct `realized_income_month` from observed credits. Deterministic rules only, no
trained model.

**Inputs.** Estimator input `1.0` or later: accounts, transactions, coverage, loan and investment
links. On contract `1.3`, `consent_scopes` replaces `coverage`.

**Method.** Precedence-ordered rules classify each credit with a reason code, description-based
clustering detects income streams, and a stable stream fills a due month only where that month also
falls inside a receiver-known fetch gap of one of its accounts — a month the receiver fetched and
found empty is a non-payment month, not a gap. Complete-coverage zero months stay zero, as before.

**What changed from `0.2.0`.** Imputation used to fire on measured account coverage below a
threshold, a ratio only the simulator's withheld-record counts could supply. It now fires only on a
receiver-known gap read from `consent_scopes` (`fetched_from`, `fetched_through`,
`pagination_complete`). Under the simulator's `build_estimator_input_v1_3` adapter, which declares
full-window scopes because the simulator fetches everything it emits, `incomplete_observation` has no
receiver-known gaps and this component matches the unscaled baseline exactly — correctly, since the
simulator withholds records without telling the receiver. See
[ADR 0010](../../docs/adr/0010-coverage-oracle-removed.md).

**Measured.** On `stress-0.13.0-report.json`, `capacity-estimator-0.7.0` +
`quantile-calibration-0.13.0`: realized WAPE `0.0` on `clean`, `normal`, `life_events`, and
`high_volatility`; `0.1083` on `partial_consent` and `0.0167` on `noisy`. Zero false-income months
on every suite.

**Known failure modes.**
- The noisy suite remains its only nonzero realized error under contract `1.6`. What is left is
  timing, not classification: a reversal's corrected re-post that has not arrived by the request
  cutoff carries income the estimator cannot yet see, and the reversed original it repairs is
  correctly excluded.
- Non-income credits shaped like income remain a structural weak point. The noisy suite contains an
  asset sale, a merchant refund, and an own transfer described as a PIX receipt.
- Stream clustering falls back to normalized description because no adapter populates the optional
  counterparty fields of input `1.1`. Two payers sharing a description merge.
- Imputation requires a receiver-known fetch gap. A provider that under-fetches without recording it
  in its own request log produces silent under-estimation; this is no longer a coverage-ratio defect,
  it is the honest limit of what a receiver can know.

**Intended use.** Default realized-income estimate, and the anchor every later component builds on.

---

## `recurring-streams-0.2.0` — monthly realized income, superseded

**Status: superseded by `recurring-streams-0.3.0`.** It imputed by dividing by
`eligible_record_count`/`observed_original_record_count`, counts only the simulator could produce
because it had generated and withheld the records. See
[ADR 0010](../../docs/adr/0010-coverage-oracle-removed.md).

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

## `capacity-gbdt-stumps-0.7.0` — sustainable monthly income

**Task.** Predict `sustainable_monthly_income` for one customer-month.

**Inputs.** Feature set `customer-month-features-1.3.0`, 103 point-in-time features. Requires
estimator input `1.2` for the capacity group; on `1.0` or `1.1` those six features report
`CONTRACT_DOMAIN_UNAVAILABLE` and the model routes on what remains.

**Labels.** Private contract `income-targets-1.0`, joined only after observed features are built.
Requires simulator contract `1.3` or later.

**Method.** Hurdle. A logistic gate decides whether sustainable income is zero; an anchored
regressor boosts `log1p(sustainable)` around `log1p(income_mean_3m_minor)`. Decision stumps over
binned features, with a per-stump direction for missing values. Unchanged from `0.6.0`.

**Data.** 720 customers across `income_diverse`, `life_events`, and `incomplete_observation`, split
70/15/15 by customer. Customer-disjoint from every population used to fit or gate the interval.

**What changed from `0.6.0`.** [ADR 0010](../../docs/adr/0010-coverage-oracle-removed.md) removed
`effective_consent_coverage_basis_points` and `minimum_account_coverage_basis_points` from the
feature set and added `fetched_window_coverage_basis_points`. Retrained on feature set `1.3.0`,
same seeds and split.

**Measured.** Held-out MAE `25,374.23` against `84,089.40` for the best deterministic baseline, WAPE
`0.0502`. Against `0.6.0` (MAE `25,217.44`, WAPE `0.0499`) the model's own error barely moves — the
two removed features were not what it was fitting on. `PROMOTED` by the unchanged gate against
`historical_median_12m`. By the simulator's private coverage label, `partial_high` rows move from
`13,469.21` to `11,647.99` and `complete` rows from `32,582.00` to `33,978.74`.

**Known failure modes.**
- Still loses narrowly to the trivial cash-flow baseline on perfectly stable salaried income.
  Routing `0.8.0` routes around this deliberately.
- Degrades sharply on income conditions absent from training. On the held-out high-volatility suite
  sustainable WAPE is `0.410`, an order of magnitude worse than on its training conditions.
- Trained on three suites only. Income profiles outside them are out of distribution and nothing
  currently detects that at inference time.

**Intended use.** Sustainable-income component inside routing `0.8.0`. Not a standalone product
output.

---

## `capacity-gbdt-stumps-0.6.0` — sustainable monthly income, superseded

**Status: superseded by `capacity-gbdt-stumps-0.7.0`.** Trained on feature set `1.2.0`, which
carried `effective_consent_coverage_basis_points` and `minimum_account_coverage_basis_points`; the
model itself did not lean on either, but the two components measured downstream of it did. See
[ADR 0010](../../docs/adr/0010-coverage-oracle-removed.md).

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

## `deterministic-routing-0.8.0` — routing, promoted

**Status: PROMOTED.** Selects the capacity model wherever it is available. See
[ADR 0010](../../docs/adr/0010-coverage-oracle-removed.md).

**Task.** Choose, per customer-month, whether `sustainable_monthly_income` comes from the capacity
model or from last month's realized-income reconstruction.

**Method.** The `0.7.0` stable-income rule was re-pointed at `fetched_window_coverage_basis_points`
(receiver-known fetch coverage) instead of the withdrawn `effective_consent_coverage_basis_points`,
and re-measured on the same benchmark. It fired on zero of 312 rows: the simulator's
`build_estimator_input_v1_3` adapter declares full-window scopes, so no row is receiver-known
partial. The benchmark's own gate reported `NOT_PROMOTED` for the re-pointed rule ("improves no
segment"), which is the result that removes it. No rule now routes away from the capacity model.

**Measured.** Held-out, 312 rows: routed MAE `13,973.25`, identical to the capacity model alone.
Against the withdrawn `0.7.0` oracle rule, which scored `12,179.10` by dividing by the exact hidden
fraction on `partial_high` rows (`1,720.56` against `9,612.08` for the model alone): that number was
the oracle being read twice, once by the feature that fired the rule and once by the component the
rule selected, and no receiver of Open Finance data has it.

The benchmark's "improves a segment" gate now applies only when some rule actually routed away from
the model on the population, because with no such rule the condition is unsatisfiable by
construction.

**Known failure modes.**
- Reason codes still report coverage and volatility so a reviewer sees the evidence the earlier
  rules acted on, even though no rule currently acts on it.
- Removing the rule removes its failure mode too: the previous rule's cost on the `noisy` stress
  suite (see `evaluation/baselines/README.md`) does not recur, because nothing routes away from the
  capacity model there either.

**Intended use.** Default routing inside the ensemble estimator and every bundle.

---

## `conditional-selector-intervals-0.13.0` — sustainable income interval, promoted

**Status: PROMOTED.** Bound to `capacity-gbdt-stumps-0.7.0` and `deterministic-routing-0.8.0`. See
[ADR 0010](../../docs/adr/0010-coverage-oracle-removed.md).

**Why a refit.** Residuals are taken around the estimate `combine_month` publishes, routing
included. Both halves of what the residuals were taken around moved — the capacity model to `0.7.0`
and routing to `0.8.0` — so the calibration is refitted rather than re-pointed, drawn from lockbox
seed floor `1_010_000`. Floors `610_000`, `710_000`, `810_000`, and `910_000` are already spent by
earlier releases. The method is unchanged from `0.12.0`: same conditional cell selector, same
pre-registered conditioner, same gates.

**Measured.** Conditioner re-selected inside the uncertainty population: `observed_domain_count`,
cuts `[2, 3, 5]`, worst-seed tail miss `0.1343` (next best `transaction_count_1m` `0.1574`).
Validation, seeds `410_000`+/`510_000`+, 240/suite: coverage `0.8760` against nominal `0.80`, floor
`0.75`, on 8635/8640 rows; tails lower `0.0573` upper `0.0667` against `0.10`; bands high `0.8824`,
medium `0.8796`, low `0.8151`. By suite: `income_diverse` `0.8092` (width `162766.85`, WAPE
`0.1245`), `incomplete_observation` `0.8186` (width `51490.40`, WAPE `0.0225`), `life_events`
`1.0000` (width `10594.17`, WAPE `0.0062`). Zero-truth coverage `0.9983` on 600 rows. Support
envelope fences 9 features, 5/8640 rows out of support. Sharpness non-inferiority passed on all
three suites. `PROMOTED`.

Lockbox, read once, seeds `1_010_000`+, 240/suite, 8640 rows: coverage `0.8756` on 8639/8640; tails
lower `0.0581` upper `0.0663`; bands high `0.8723` low `0.8418` medium `0.8863`. By suite:
`income_diverse` `0.8014`, `incomplete_observation` `0.8253`, `life_events` `1.0000`. Sharpness
passed on all three. `RELEASE_CONFIRMED`. Report:
`training/artifacts/lockbox-conditional-selector-intervals-0.13.0-report.json`.

**Known failure modes.** Out-of-distribution coverage collapses on `noisy` and `high_volatility`
(`0.09` / `0.19` against `0.80`); in-distribution over-covers (`0.876` against `0.80`), the same
symptom as `0.12.0`. The conditioner is still `observed_domain_count`, which barely varies in
distribution — the regime-conditioned selector is plan order 4.

**Intended use.** The runtime default, bound to `capacity-gbdt-stumps-0.7.0` by version and digest.
The rollback is no intervals.

---

## `conditional-selector-intervals-0.12.0` — sustainable income interval, superseded

**Status: superseded by `conditional-selector-intervals-0.13.0`.** Fitted around
`capacity-gbdt-stumps-0.6.0` and `deterministic-routing-0.7.0`, the routing rule ADR 0010 found was
reading the coverage oracle twice; every figure below inherits that. See
[ADR 0010](../../docs/adr/0010-coverage-oracle-removed.md).

**Why a refit.** Residuals are taken around the estimate `combine_month` publishes, routing
included. `deterministic-routing-0.7.0` narrowed routing to stable income under declared partial
coverage, which moves the residual distribution whatever the model bytes do, so the calibration was
refitted rather than re-pointed. The method is unchanged from `0.11.0`: same conditional cell
selector, same pre-registered conditioner, same gates.

**What changed in the artifact.** Schema `1.6` records `ensemble_version`, the routing rule the fit
was taken around, and the runtime refuses a calibration that names a different one — or none, which
every artifact before `1.6` does. A calibration fitted around one routing rule used to load silently
under another.

**Measured.** Validation, `720` customers no earlier stage used: `8,636` of `8,640` rows publish and
`4` are refused as out of support. Coverage `0.9035` against nominal `0.80`. By band: high `0.9235`,
medium `0.9036`, low `0.7912`, each against a floor of `0.7500`. By suite: `income_diverse` `0.8085`,
`incomplete_observation` `0.9020`, `life_events` `1.0000`. Zero-truth `0.9983`. Overall tail miss
rates `0.0515` and `0.0449` against `0.10`.

Release lockbox, seeds `810_000`+, generated for the first time by that run and read once:
`RELEASE_CONFIRMED`, coverage `0.9122` on `8,636` of `8,640` rows, empty failure list. One reading,
not two: the envelope is written into the artifact before the lockbox is read, so the reading
describes the released bytes exactly.

**Known failure modes.**
- **Out-of-distribution coverage is bad, and this release makes one case worse.** On
  `evaluation/baselines/stress-0.12.0-report.json`, `noisy` covers `0.0888` against `0.3482` for the
  previous pair, and `high_volatility` `0.1483` against `0.1581`, both against a nominal `0.80`. The
  `noisy` cost is attributable: routing alone takes it to `0.1786`, refitting around that routing
  takes it the rest of the way, and all of the point-estimate damage is routing's. Under partial
  coverage the rule routes to last month's reconstruction, which on a feed carrying duplicates,
  reversals and late arrivals is the least reliable number available. It was kept because it was
  selected and confirmed on the distribution the model is for; narrowing it against these suites
  would be selecting on the data meant to test it.
- **The support envelope cannot fix `high_volatility`, and tightening it was rejected on evidence.**
  Only `4` of `240` of those rows fall outside any fenced range, and their joint distance from the
  calibration centre is lower than the `normal` suite's. Fencing `income_cv_12m` does not separate
  them either: the calibration population spans it from `0` to `1.888`, and `2.3%` of
  `high_volatility` rows fall outside that against `3.6%` of `normal` ones. What fails is conditional
  coverage, not support. Conditioning the interval on volatility is open work.
- Mean confidence does not fall to compensate, so it cannot be read as a warning.

## `conditional-selector-intervals-0.11.0` — sustainable income interval, superseded

**Status: superseded by `conditional-selector-intervals-0.12.0`.** It passed every gate it was
judged against; it was fitted around `deterministic-routing-0.6.0`, which no longer exists, and the
runtime now refuses it for exactly that reason. See
[ADR 0008](../../docs/adr/0008-conditional-selector-promotion-and-abstention.md).

**Task.** Turn the routed sustainable-income estimate into a `p10`/`p90` pair, or refuse.

**Method.** Conformalized quantile regression with a conditional cell selector. Rows fall into
quartiles of `observed_domain_count`, cut at `2`/`3`/`5`, crossed with the confidence band. Each cell
chooses the learned residual band or the fixed per-band offsets and carries its own two tail
corrections. The branch is decided out-of-fold inside the uncertainty-training population, comparing
the branches only after both have been corrected to hold their tails. Corrections are fitted on
calibration customers. Five of six cells chose fixed; the learned band earned its place in `q2/high`.

The `low` band is not selected over. It holds both tails at `0.1091` and `0.0922` against `0.10`, and
the run checks its intervals are byte-identical with the selector stripped from the artifact.

The conditioner was pre-registered: ranked first of `64` candidates inside the uncertainty-training
population and frozen in `conditioner-preregistration.json` before the selector was built, with no
final-test population loaded. An earlier scan against final test picked the same winner and was
discarded, because selecting a model on the population that measures it means the measurement is not
a test.

**Measured.** Validation, `720` customers no earlier stage used: `8,635` of `8,640` rows publish and
`5` are refused as out of support. Coverage `0.9050` against nominal `0.80`. By band: high `0.9176`,
medium `0.9128`, low `0.7987`, each against a floor of `0.7500`. By suite: `income_diverse` `0.8025`,
`incomplete_observation` `0.9295`, `life_events` `0.9830`. Zero-truth `0.9983`. Overall tail miss
rates `0.0478` and `0.0471` against `0.10`.

Release lockbox, seeds `710_000`+, generated for the first time by that run and read once:
`RELEASE_CONFIRMED`, coverage `0.9116` on `8,640` of `8,640` rows, empty failure list.

**Gate.** Coverage one-sided on under-coverage, per suite and per band. Each tail gated on its own
miss rate against `0.10`. Sharpness is a one-sided non-inferiority test on the paired per-row
difference against the fixed-band model, judged against a margin declared in advance at `2%` of that
suite's baseline score; every suite passes by `7x`, `24x` and `19x`. Error bars from resampling
customers. Complete promotion means every supported row publishes.

**Known failure modes.**
- The lockbox was read before out-of-support abstention was added, then read once more against the
  exact released bytes to close that gap:
  `training/artifacts/lockbox-conditional-selector-intervals-0.11.0-release-report.json`,
  `RELEASE_CONFIRMED`. The envelope withheld `14` of `8,640` rows and altered no published bound;
  coverage on published rows moved `0.911574` to `0.911894`.
- **Out-of-distribution coverage is bad, and the envelope does not save it.** Measured on this
  artifact in `evaluation/baselines/stress-0.11.0-report.json`: the held-out `noisy` suite covers
  `0.348` on 224 of 240 published rows and `high_volatility` covers `0.158` on 234 of 240, both
  against a nominal `0.80`. Every withheld row is withheld for `OUT_OF_CALIBRATED_SUPPORT` and for
  no other reason, so the envelope behaves as specified and is simply far too permissive: nine
  independent per-feature range checks admit rows from a population the calibration never saw,
  because no single feature leaves its range. Mean confidence does not fall to compensate —
  `0.744` on `noisy`. This supersedes the `0.491`/`0.125` figures quoted historically, which were
  measured on a different artifact under a binding violation.
- `observed_domain_count` is integer-valued, so its quartile cuts collapse to three occupied buckets
  rather than four. No cell fell back, each carrying at least `720` scores, but the pre-registration
  guard should have caught the discreteness.
- The conformal unit is the customer-month, not the customer. Empirical customer-disjoint calibration
  with customer-clustered error bars, **not** a finite-sample guarantee.
- `income_diverse`'s sharpness comparison is contested: the fixed-band baseline it is measured
  against does not hold its own tails there. Recorded as `baseline_tails_hold`, not exempted.
- Annual quantiles are not produced.

**Intended use.** The runtime default, bound to `capacity-gbdt-stumps-0.6.0` by version and digest.
The rollback is no intervals.

---

## `adaptive-intervals-0.9.0` — sustainable income interval

**Status: NOT_PROMOTED, superseded by `conditional-selector-intervals-0.11.0`.** Every band
publishes and every band passes its coverage floor and both
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
- Width is allocated in the wrong direction within each suite. Broken down by the candidate's own
  predicted width, it beats the fixed-band model across the middle quartiles and loses everything in
  its widest, which covers `0.943` on `income_diverse` at `7.4x` the baseline's width. The failing
  `p90` is almost entirely the narrowest quartile's, missing `0.308` at a width the fixed-band model
  matches. The defect is the slope of the width model, not its level, so a global widening worsens
  the sharpness failure without addressing the tail one. See `width_allocation` in the report.
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
  inference time. The `0.491` noisy and `0.125` high-volatility figures recorded for this artifact
  come from a run whose capacity binding cannot be reconstructed and are void as evidence; see
  `evaluation/baselines/README.md`. They indicate the direction, not the magnitude.
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
