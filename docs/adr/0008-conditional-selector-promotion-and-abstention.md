# ADR 0008: Conditional selector promotion and out-of-calibration abstention

- Status: Accepted
- Date: 2026-08-25
- Supersedes: the calibration candidate of
  [ADR 0007](0007-complete-adaptive-interval-promotion.md), whose `adaptive-intervals-0.9.0` did not
  promote
- Calibration contract: `conditional-selector-intervals-0.11.0`
- Capacity contract: `capacity-gbdt-stumps-0.6.0`
- Estimator milestone: `0.11`

## Context

ADR 0007 left `adaptive-intervals-0.9.0` with three recorded failures: the `income_diverse` upper
tail at `0.1372` against a `0.1250` ceiling, and sharpness against the fixed-band model on
`income_diverse` and `incomplete_observation`. Before any of that could be acted on, three protocol
defects had to be repaired, because two of them made the recorded evidence unreliable.

**The capacity binding was recorded and never checked.** A calibration artifact carries the
`model_version` and SHA-256 of the capacity artifact it was fitted against; nothing compared them at
load. Three live call sites were mis-bound, including the stress report's default. Two distinct
drifts had occurred: `capacity-estimator-0.5.0.json` was rewritten in place under an unchanged
`model_version`, which only a digest can catch, and `conformal-intervals-0.8.0` names the pre-rename
bytes of what is now `capacity-estimator-0.6.0.json`. The `0.491` and `0.125` out-of-distribution
figures quoted in several places came from a run made under that violation and are attributable to
no coherent model pair.

**A measured zero was read as a missing value.** The tail gate resolved its error bar with
`metrics[...] or fallback`, so a clustered standard error of exactly `0.0` — what a tail no customer
ever misses actually has — was replaced by a row-level binomial and published under the name
`clustered_standard_error`. That is the `0.00559` standing beside `life_events`' `0.0` lower-tail
miss rate in the `0.9` report.

**Sharpness was not a test.** It compared two independently reported means and failed anything above
a ratio of `1.0`, which calls `1.001` a regression and `0.999` an improvement on a difference whose
sampling noise nobody had measured.

## Decision

### Sharpness is a paired non-inferiority test

Both models score the same rows, so the difference is taken row by row and the shared variance that
dominates a Winkler score — how hard each customer-month happens to be — cancels. The error bar
comes from resampling customers. A candidate passes when the upper end of that difference sits below
a margin fixed in advance: `2%` of that suite's own baseline score, since suite scores differ by
roughly `4x`. A difference with no error bar is refused rather than waved through.

The pairing is not cosmetic. On `life_events` the paired standard error is `65` minor units on a
difference of `-31,406`; unpaired, that comparison was noise.

The gate stays unconditional. `baseline_tails_hold` records whether the fixed-band model holds its
own tail claims on each suite, because a baseline that under-covers wins on Winkler score by
declining to buy width it owes. On `income_diverse` it does not, and the comparison is contested for
exactly that reason. It is recorded as a diagnostic and is not an exemption: excluding under-covering
baselines would remove the pressure the interval score exists to apply.

### Width is allocated by a conditional cell selector

The diagnostic that decided the model came from breaking the paired score difference down by
covariates already in the feature table. Both failures were one defect: the learned band beat the
fixed-band model across the middle of its own width distribution and lost everything in its widest
quartile, spending `728,476` there against `97,830` while covering `0.943` against a nominal `0.80`.
The failing `p90` was almost entirely the narrowest quartile's, missing `0.308` at a width the
fixed-band model matched. The quartile upper-tail miss rates were `0.308`, `0.142`, `0.085`, `0.014`;
their mean is `0.1371` against a measured `0.1372`.

A monotone transform of that width was tried first and cannot work. Its fitted lower slope came out
at exactly `1.0` — a pure rescale, no compression — which is the fit reporting that raw-width
ordering carried no signal about which rows needed more width. The regimes overlap in raw width: at
the same learned width an `income_diverse` row needs a wider interval and a `life_events` row a
narrower one.

The promoted model conditions on something that can separate them. Rows fall into quartiles of one
conditioner crossed with the confidence band; each cell chooses the learned or the fixed band and
carries its own two tail corrections. Five of six cells chose fixed.

The `low` band is not selected over. It holds both tails at `0.1091` and `0.0922` against `0.10`,
and the run verifies its intervals are byte-identical with the selector stripped from the artifact.

### The conditioner is pre-registered

`observed_domain_count` was ranked first of `64` candidates inside the uncertainty-training
population, on a criterion evaluated on customer splits of that population alone, and frozen in
`conditioner-preregistration.json` before the selector was built.

An earlier scan ranked the same features against final test and picked the same winner. That route
is disqualifying regardless of the answer it gives: selecting a model on the population that then
measures it means the measurement is not a test. The pre-registration exists so the provenance can
be checked rather than trusted, and the lockbox bears it out — validation and lockbox agree to
within `0.005` on every `income_diverse` figure, which is where a selection effect would have shown.

### Final test is validation; the release lockbox is read once

Seeds `510_000`–`530_000` have been read across several method-selection rounds. They are validation
permanently, recorded as `populations.final_test_role`. A release lockbox is drawn from untouched
seeds and read exactly once.

Seeds `610_000`+ were generated once at eight customers per suite to check the lockbox evaluator ran
at all. Nothing was decided from it and every suite was below the gating threshold, but a population
that has been generated is no longer untouched, and "it barely counts" is the reasoning a lockbox
exists to refuse. That floor is recorded as spent and the release read used `710_000`+.

### Outside the calibrated conditions, refuse

The artifact carries the range each fenced feature took across the calibration population. A row
outside it receives no interval and `quantile_unavailable_reason = OUT_OF_CALIBRATED_SUPPORT`,
distinct from `UNCALIBRATED_INTERVAL`: that one says the band has no fitted correction, this one says
the correction exists and does not apply here.

Nine features are fenced — the eight the residual quantile ensembles split on most, plus the
conditioner. Fencing everything the stumps touch refuses ordinary rows over features that move the
interval by nothing; fencing nothing publishes an `80%` label on conditions nobody measured.

The refusal is made where the interval is produced, so the runtime and the evaluation harness refuse
the same rows. Complete promotion accordingly means every **supported** row publishes, and the
refusal share is gated against a `1%` ceiling so the envelope cannot fence the calibration
distribution itself.

## Consequences

`conditional-selector-intervals-0.11.0` promotes. On validation, `8,635` of `8,640` rows publish and
`5` are refused; coverage `0.9050` against nominal `0.80`; every band and suite inside its floor and
both tails; every suite passing sharpness by `7x`, `24x` and `19x` its margin. On the release
lockbox, `RELEASE_CONFIRMED` with an empty failure list.

`income_diverse` gains coverage, `0.7670` to `0.8025`, while its width falls from `286,952` to
`168,704` and its upper tail from `0.1372` to `0.0965`. `incomplete_observation` narrows `72%` and
gains coverage. Both diagnostic targets are met at once, which they could be because they were never
in tension: the model was misallocating width, not short of it.

The safe rollback is no intervals. `conformal-intervals-0.8.0` is not one — its binding no longer
resolves and the runtime refuses to load it.

## Known limits

- The lockbox was read before abstention was added. The promoted artifact differs from the one it
  measured by the added `support_envelope` and the schema bump alone, with every offset, adjustment,
  cell policy and tree identical, and nothing in the offset path reads the envelope. For any
  in-support row the published bounds are the bounds that were measured. Whether any lockbox row now
  falls outside the envelope is untested, and could only remove intervals, never change one.
- `observed_domain_count` is integer-valued, so its quartile cuts collapse to three occupied buckets
  rather than four. No cell fell back, but the pre-registration guard should have caught that.
- The conformal unit remains the customer-month, not the customer. This is empirical
  customer-disjoint calibration with customer-clustered error bars, **not** a finite-sample
  guarantee.
- Out-of-distribution coverage was unmeasured for this artifact when the decision was taken. It has
  since been measured and is bad; see the addendum below. The envelope makes the scope of the claim
  honest by refusing outside it; it does not repair behaviour beyond it, and it turns out not to
  refuse nearly often enough to make the scope honest in practice.
- Annual quantiles are not produced.

## Out of scope

Customer-level conformal theory, repair of the `high_volatility` regime, and any third calibration
model. Interval work stops here.

## Addendum: measured out-of-distribution behaviour

The stress suites were run against the promoted pair after this ADR was accepted, and recorded in
`estimator/evaluation/baselines/stress-0.11.0-report.json`. On the two held-out income conditions
the interval under-covers badly while publishing almost everything:

| suite | intervals published | coverage | mean confidence |
|---|---|---|---|
| noisy | 224/240 | 0.348 | 0.744 |
| high_volatility | 234/240 | 0.158 | 0.546 |

Every withheld row is withheld for `OUT_OF_CALIBRATED_SUPPORT`, and for no other reason. The
envelope therefore behaves exactly as specified and is still far too permissive: nine independent
per-feature range checks admit rows drawn from a population the calibration never saw, because no
single feature leaves its range. Confidence does not fall to compensate, so the `noisy` suite
publishes `0.744` mean confidence over intervals that hold `0.348` of the time.

This does not reverse the promotion, which was gated on in-distribution evidence and holds there.
It does retire the claim that abstention alone makes the `80%` scope honest. Multivariate support
detection and an out-of-distribution component in the confidence score are prerequisites for any
further interval promotion.
