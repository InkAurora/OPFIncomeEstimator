# ADR 0009: Routing narrowed to partial coverage, and recalibrated around it

- Status: Accepted
- Date: 2026-09-08
- Supersedes: the routing rule of
  [ADR 0008](0008-conditional-selector-promotion-and-abstention.md), `deterministic-routing-0.6.0`,
  and the calibration fitted around it, `conditional-selector-intervals-0.11.0`
- Routing contract: `deterministic-routing-0.7.0`
- Calibration contract: `conditional-selector-intervals-0.12.0`
- Capacity contract: `capacity-gbdt-stumps-0.6.0`, unchanged
- Estimator milestone: `0.12`

## Context

A project review reproduced a defect that no test could see: the committed routing benchmark
described `capacity-gbdt-stumps-0.5.0`, digest `f58f13d5…`, while the bundle shipped `0.6.0`,
digest `4ee9c3a6…`. Re-run against the model it actually ships, the routed ensemble scored
`16064.13` MAE against `14910.78` for the capacity model by itself: a `7.73%` regression, reported
as `NOT_PROMOTED` by the benchmark's own gate, which then exited zero. CI never ran it.

The rule at fault routes to last month's reconstruction whenever income is stable. ADR 0008 records
that conditioning it on coverage as well was measured and rejected — "on the intersection the model
wins again". That measurement was taken against `0.5.0`. Against `0.6.0` it reverses, and the
reversal is the whole finding: evidence that outlives the pipeline it describes does not announce
that it has.

## Decision

**Routing fires only where income is stable and coverage is declared incomplete.** Where coverage is
complete the capacity model wins and routing away from it is what made `0.6.0` worse than its own
best component. Undeclared coverage does not route: that is the absence of evidence, not evidence of
a gap, and it is not what was measured.

Five candidate rules were ranked on the benchmark's own seeds, then confirmed on seeds
`910_000`–`930_000`, which no run had generated.

| Rule | Selection, 26 customers | Confirmation, 71 customers |
| --- | ---: | ---: |
| capacity model alone | `14910.78` | `19220.34` |
| stable income (`0.6.0`, superseded) | `16064.13` | `20126.14` |
| **stable income and declared partial coverage** | **`12179.10`** | **`16690.13`** |
| stable income and history under six months | `14061.47` | `18302.17` |
| either of the two above | `12346.09` | `16800.17` |

The ranking is identical on both populations. A variant routing to a three-month mean instead of
last month scored identically to four decimal places on both, because on stable income the two are
the same number; there is no evidence to prefer either, so the simpler one is kept.

**The calibration is refitted, not re-pointed.** Residuals are taken around the estimate
`combine_month` publishes, routing included, so a routing change moves the residual distribution
whatever the model bytes do.

**The artifact now records the routing it was fitted around, and the runtime refuses a mismatch.**
Artifact schema `1.6` adds `ensemble_version`. Until now a calibration fitted around one rule loaded
silently under another: the same failure as evidence outliving its pipeline, one level down. An
artifact written before `1.6` names no rule and is refused rather than trusted, because every one of
them was fitted around a rule this package no longer has.

**The benchmark is a gate.** It exits non-zero when the status is not `PROMOTED`, and release CI
runs it. `--no-gate` records a report for a rule still being worked on, without claiming it passed.

**One lockbox reading, of the bytes that ship.** `0.11.0` needed two, because its support envelope
was attached after the first reading, so the report that promoted it described bytes no deployment
ran. `0.12.0` is written with its envelope in place. Seeds `710_000`+ are spent; this release drew
`810_000`+, generated for the first time by that run and read once.

## Consequences

Measured on the stress suites, against the previous pair:

| Suite | In distribution | Before | After |
| --- | --- | ---: | ---: |
| `normal` | yes | `0.7583` | **`0.8042`** |
| `partial_consent` | yes | `0.9583` | `0.9375` |
| `life_events` | yes | `0.9750` | **`1.0000`** |
| `noisy` | no | `0.3482` | **`0.0888`** |
| `high_volatility` | no | `0.1581` | `0.1483` |

Nominal coverage is `0.80`. The in-distribution result is the best recorded: `normal` lands on
nominal rather than under it.

**The `noisy` suite is worse, and the cost is attributable.** Holding the calibration fixed and
changing only the routing moves it from `0.3482` to `0.1786`; refitting the calibration around the
new routing takes it to `0.0888`. All of the point-estimate damage is routing's: `noisy` sustainable
WAPE moves `0.0303` to `0.0811` under the routing change alone and does not move again. On a feed
carrying duplicates, reversals and late arrivals, last month's reconstruction is the least reliable
thing available, and it is exactly what this rule now routes to under partial coverage.

The rule is kept anyway. It was selected and confirmed on the distribution the model is for, twice,
with a consistent margin. Narrowing it against the stress suites would be selecting on the data
meant to test it, which this repository has already refused once, in ADR 0008.

**`high_volatility` is unchanged by any of this**, `0.1581` to `0.1483`, and was investigated
separately before being left alone. The failing rows are not out of support and cannot be made so:
only `4` of `240` fall outside any fenced feature range, and their joint distance from the
calibration centre is *lower* than the `normal` suite's, `0.63` median against `0.70`. Fencing
income volatility does not separate them either — the calibration population already spans
`income_cv_12m` from `0` to `1.888`, and `2.3%` of `high_volatility` rows fall outside that range
against `3.6%` of `normal` ones, so the fence would refuse more ordinary customers than volatile
ones.

The support envelope is therefore not the bottleneck, and tightening it was considered and rejected
on that evidence. What fails is conditional coverage: the correction is fitted per confidence band
crossed with `observed_domain_count`, and averages over a population where volatile and noisy rows
are a small minority. It is right on average and wrong in the tail. `income_cv_12m` was already
ranked as a conditioner candidate and placed 14th of 64 — but only against in-distribution data,
where it has nothing to prove. Conditioning the interval on volatility is the open work, and it is
not attempted here.

Output contract `1.2` limits the damage a bad interval can do rather than fixing it: no month
publishes a policy-qualified amount unless its evidence is assessed `SUPPORTED`. That does not yet
catch these suites, and `output_v1_2.py` says so.
