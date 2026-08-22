# ADR 0002: Income target construction

- Status: Accepted
- Date: 2026-08-21
- Supersedes: nothing
- Extends: [ADR 0001](0001-income-target-definitions.md)
- Target contract: `income-targets-1.0`
- Estimator milestone: `0.5` prerequisite

## Context

ADR 0001 named five private targets but left their construction open. It explicitly deferred the
treatment of bonuses, source start and end dates, job changes, zero-income months, partial months,
and histories shorter than twelve months, and required that no supervised model be trained before
those rules are fixed.

Only `realized_income_month` exists today, as `true_income_minor` on the private customer-month
record. Estimator `0.5` trains on `log1p(sustainable_monthly_income)`, which has no label at all.

Two placements were considered.

**Reconstruct targets in the estimator training zone.** The plan places label construction in the
training pipeline, and the private truth already publishes source parameters, life-event
transitions, and per-month active source identifiers. But the expectation math depends on hidden
state the truth layer does not publish, in particular the scenario-level seasonality multipliers
applied inside the engine. Reconstructing it would duplicate the generative process in a second
repository, where it could silently drift from the engine that produced the data.

**Project targets in the simulator.** Turning hidden state into private truth is exactly what the
projector layer exists for. The estimator training zone then consumes labels rather than
re-deriving them, and the trust boundary is unchanged: the labels remain private truth, joined only
inside the isolated dataset-building step.

## Decision

### Placement

Income targets are projected by the simulator from the hidden simulation run, in
`finances_simulator.ground_truth.income_targets`, and consumed by the estimator training zone. The
projection is additive: it introduces its own contract version, does not alter any record of
observation contracts `1.0` through `1.5`, and does not change any existing dataset file.

The scenario income-seasonality multipliers become part of the hidden `SimulationRun`, which is
where the rest of the world state already lives. Runs produced by engines without scenario
seasonality report an empty tuple, read as a neutral multiplier.

### Expectation model

The engine emits one income attempt per source occurrence. For source `s` and occurrence `k`, the
attempt falls in `start_month_index + k * interval_months`, pays with probability
`payment_probability_basis_points / 10000`, and draws a volatility shock uniformly from
`[-volatility_basis_points, +volatility_basis_points]`, whose expectation is zero. The realized
amount is

```text
base * source_seasonality[m] * scenario_seasonality[m] * (10000 + shock) / 10^12
```

so the expected amount of one attempt is exactly

```text
base_effective * source_seasonality[m] * scenario_seasonality[m] * payment_probability / 10^12
```

Targets sum these exact integer ratios and round half up once, at the end. Volatility therefore
never biases a target, and the expectation is derived from the engine's own parameters rather than
estimated from realized samples.

### Target definitions

All amounts are integer minor currency units. `M` is the reference month and its cutoff is the last
day of `M`.

- **`realized_income_month`** is unchanged: the sum of `INCOME` economic events posted in `M`. It is
  copied from the existing customer-month truth so the two can never disagree.
- **`expected_income_month`** sums the expected amount of every attempt scheduled in `M` from
  sources active at the attempt date, plus every bonus life event effective in `M`.
- **`sustainable_monthly_income`** sums, over sources that are recurring and active at the cutoff,
  the expected amount those sources will produce across the twelve months after `M`, divided by
  twelve. A per-source annual expectation divided by twelve is used rather than the median of the
  twelve monthly values, because a median would report zero for any source that does not pay every
  month and would erase quarterly, semiannual, and annual income entirely.
- **`realized_income_trailing_12m`** sums `realized_income_month` over the twelve complete months
  ending at `M`, and is unavailable when fewer than twelve complete months precede the cutoff.
- **`expected_income_next_12m`** sums the expected amount of every attempt scheduled in the twelve
  months after `M`, under the state effective at the cutoff. Like sustainable income it applies no
  post-cutoff life event.

### Deferred rules now fixed

- **Bonuses.** A `BONUS` life event emits a one-off `INCOME` event with a configured amount. It
  counts toward `realized_income_month`, and toward `expected_income_month` for the month it is
  effective, because its date and amount are then known with certainty. It is excluded from
  `sustainable_monthly_income` as an extraordinary inflow, and from `expected_income_next_12m`,
  which applies no post-cutoff life event of any kind. Inheritance is a `GIFT` and is income under
  no target.
- **Source start and end dates.** A source contributes only to attempts within its schedule: from
  `start_month_index`, every `interval_months`, for `occurrences` attempts. Beyond its final
  scheduled attempt a source contributes zero and is not treated as active.
- **Job changes.** Effective-dated life-event state governs both activity and amount. The state used
  for an attempt is the one produced by the last transition effective on or before that attempt's
  date, matching the engine. Deactivation, raises, and promotions therefore take effect from the
  transition date forward, with no retroactive restatement of earlier months.
- **Forward projection uses state known at the cutoff.** `sustainable_monthly_income` and
  `expected_income_next_12m` project each source's own schedule forward under the state effective at
  the cutoff. Life events effective after the cutoff are deliberately not applied: these targets
  describe capacity as of the reference date, not a forecast privileged with future knowledge. A
  job loss next month therefore does not reduce this month's sustainable income, and the resulting
  error is a real property of the estimation problem rather than a labelling artifact.
- **Zero-income months.** A realized zero is a valid observation and is retained. It never forces
  the expected or sustainable targets to zero: a source may be active with no attempt scheduled in
  that month, or an attempt may simply have failed its probability draw. Conversely a customer with
  no active source has sustainable income of exactly zero, which is a value, not a gap.
- **Partial months.** The first and last calendar months of a window may be partial. Targets are
  computed over whole calendar months regardless, and each row carries
  `is_partial_month` so consumers can weight or drop them. Realized income for a partial month is
  the realized income of the observed part, and no scaling is applied.
- **Fewer than twelve months of history.** `realized_income_trailing_12m` is unavailable rather
  than annualized, as ADR 0001 requires. `expected_income_next_12m` and
  `sustainable_monthly_income` remain available because they are forward projections from source
  state and do not depend on the length of history.
- **Recurring versus one-off sources.** A source is recurring at the cutoff when at least two of
  its scheduled attempts remain after the cutoff and at least one of them falls within the next
  twelve months. The first condition excludes a source about to make its final payment; the second
  excludes a source too infrequent to represent capacity in the coming year. An annual source with
  several attempts left is therefore recurring and contributes its yearly expectation divided by
  twelve. A source with a single remaining attempt contributes to expected targets but not to
  sustainable income.

## Consequences

- Estimator `0.5` has a defined, deterministic training label, and evaluation can separate realized
  from sustainable error.
- The scenario contract is untouched. Observation contracts `1.0` through `1.5` keep byte-stable
  reference outputs, and no committed reference run changes.
- Targets require the private income-source records introduced by schema `1.3`. Older runs raise
  rather than emit partial targets.
- The expectation math lives beside the engine that defines it. A change to income realization must
  update both, and a shared test asserts that projected expectations agree with realized income
  over a large population.
- `sustainable_monthly_income` deliberately ignores post-cutoff life events. Evaluation of estimator
  `0.5` near life events must therefore be reported separately, as the plan already requires.
