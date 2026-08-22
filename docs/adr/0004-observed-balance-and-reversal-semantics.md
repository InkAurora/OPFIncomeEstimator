# ADR 0004: Observed balance and reversal semantics

- Status: Accepted
- Date: 2026-08-22
- Supersedes: nothing
- Extends: [ADR 0001](0001-income-target-definitions.md)
- Observation contract: `1.6`
- Estimator milestone: `0.9` prerequisite

## Context

The estimator `0.8` stress report records realized WAPE `0.17934555` on the `noisy` suite against
`0.0` on every other suite except partial consent. Attribution over 20 customers and 240
customer-months at seed `650000` assigns **all** of that error to a single reason code:

| Reason code | Missed income | Records |
| --- | --- | --- |
| `REVERSED_ORIGINAL` | `25,591,233` of `142,692,326` truth income | 43 |
| every other reason code | `0` | 0 |

Counting the reversed originals instead of excluding them drives realized WAPE to exactly `0.0`.

The cause is a semantic gap, not a classifier defect. Under observation contract `1.5` the
observation projector injects a reversal record and stops there. On that run: 429 reversal records,
0 corrections, and all 429 originals still present in the feed. Private truth keeps the money and
the observed feed does not, so the two disagree permanently and by construction.

`EconomicType.REVERSAL` is declared in the domain enum but no generator emits it. Every reversal in
the data is therefore an observation artifact describing a provider's bookkeeping, never an economic
event that cancels income.

Two questions had no recorded answer, and the inconsistency survived to `0.8` because neither was
asked:

1. What does `balance_after_minor` on an observed record mean?
2. What repairs an artifact reversal?

## Decision

### Observed balances are carried provider reports

`balance_after_minor` on an observed record is the balance the provider reported alongside that
record. It is **not** a running total derivable by folding observed amounts, and no contract
guarantees that folding observed amounts reproduces any balance.

That guarantee is unavailable in principle, not merely unimplemented. Four independent degradation
levers each break a folded balance on their own, and three of them are active in shipped scenarios:

| Lever | `noisy_observation` | `incomplete_observation` | Effect on a folded balance |
| --- | --- | --- | --- |
| `missing_record_basis_points` | `0` | `1000` | removes amounts |
| `duplicate_record_basis_points` | `2000` | `1000` | adds amounts |
| `reversal_record_basis_points` | `1500` | `500` | subtracts amounts |
| consent coverage | full | partial | removes amounts |

Any reconciliation statement written against a folded observed balance is therefore a statement
about degradation rates, not about correctness. Reconciliation is defined per record instead, below.

### The reversal record stays an observation artifact

`EconomicType.REVERSAL` remains reserved and unemitted. Reversal selection stays inside the
observation projector, downstream of ground truth and drawn from the observation namespace.

This rejects making the reversal an economic event that cancels the original and lowers
`true_income_minor`. That change would move selection upstream into the simulation engine, which
breaks the contract `1.5` invariant that V5 configurations differing only in observation settings
produce identical hidden economics and private truth, and it would invalidate every private dataset
rather than only the observed ones. The reversal describes what a provider did to its own ledger.
The customer's income did not change.

### An artifact reversal is corrected by a re-post

Contract `1.6` adds `repost_of_transaction_id`. Every injected reversal is followed by a corrected
re-post under a new deterministic ID, restoring the original amount and direction, so the observed
feed reconverges to truth. This is what a bank does after posting an erroneous reversal.

Two details are load-bearing:

- The re-post description carries the institution `description_prefix` and **drops** the
  `reversal_prefix`. The estimator's income rules read descriptions, so a re-post that still reads
  as a reversal would be excluded by keyword and the correction would never land.
- `duplicate_of_transaction_id`, `reversal_of_transaction_id`, and `repost_of_transaction_id` are
  mutually exclusive. The existing two-way validator becomes a three-way one.

Effective coverage keeps counting original records only. `repost_record_count` joins the coverage
dataset as its own field so a re-post can never inflate `observed_original_record_count`.

### Why the estimator is not changed instead

Dropping the `is_reversed_original` exclusion produces the same measured income result at a fraction
of the cost, and it was rejected.

It would make the estimator correct only for a feed in which every reversal is an artifact. In a
real Open Finance feed a reversed credit frequently means money that never arrived, and counting it
would inflate income in exactly the situation the exclusion exists to prevent. The defect is in the
data, so the data is what gets repaired. The estimator's rule and its `REVERSED_ORIGINAL` reason
code are unchanged by this decision and become correct rather than merely conservative.

### Reconciliation invariant

Stated per record, and mandatory from contract `1.6`:

- **R1** — an observed record with no lineage field set carries the `balance_after_minor` of the
  truth entry with the same ID.
- **R2** — a reversal record carries the balance the original replaced: the truth entry's
  `balance_after_minor` less its amount for a `CREDIT` original, plus its amount for a `DEBIT` one.
- **R3** — a duplicate record's amount, direction, account, currency, posting date, and balance match
  its source.
- **R4** — a re-post carries the amount, direction, account, currency, and `balance_after_minor` of
  the original it corrects, and arrives on or after its reversal.
- **R5** — for an account at full consent with `missing_record_basis_points` of `0`, folding the
  signed amounts of every observed record except duplicates reproduces the truth closing balance.
  Each reversal and re-post pair contributes zero net movement.

R5 is the feed-level statement the repository never had. It holds only because the re-post exists;
under contract `1.5` it is false for every account carrying a reversal.

## Consequences

- Measured after the refit: noisy realized WAPE falls from `0.17934555` to `0.01674763`, and
  partial consent from `0.00833333` to `0.0`. Sustainable WAPE falls from `0.09156228` to
  `0.0355653` on noisy and from `0.01791606` to `0.00898131` on partial consent. No suite produces
  a false income month.
- The residual noisy error is timing, not classification. Of 31 income re-posts in the measured
  population, 27 are counted and 4 arrive after the request cutoff. A correction that has not
  arrived is late data; under contract `1.5` the same amount was lost permanently.
- Quantile calibration `0.7` fails its coverage gate after the refit, at `0.8568` against nominal
  `0.80` with a declared tolerance of `0.05`. The cause is the segment split ADR 0003 already
  documented: a single global offset now sits over a strongly bimodal residual distribution, since
  the partial-consent segment became much more accurate while the complete-coverage segment did
  not. Conditional conformal calibration is required before `0.7` can be promoted again.
- Contract `1.6` changes observed records for `noisy_observation.yaml` and
  `incomplete_observation.yaml`. Their committed reference runs and byte-stability guarantees move.
  `high_volatility.yaml` sets `reversal_record_basis_points` to `0` and is unaffected.
- Realized income feeds `income_1m_minor`, so capacity features shift and the full refit chain is
  mandatory: retrain `0.5`, regenerate out-of-fold residuals, recalibrate `0.7`, update the pinned
  SHA-256 assertions, re-run the stress report and the `0.1`/`0.2` benchmark, then correct the model
  cards. ADR 0003 forbids calibrating `0.7` on `0.5`'s own residuals, so regenerating the out-of-fold
  pass is a required step between retrain and recalibrate rather than an optimization.
- Skipping any part of the chain leaves a calibration artifact bound to a capacity version that no
  longer exists, fails the frozen-artifact tests, and leaves the model cards misstating measured
  performance.
- `noisy_observation.yaml` carries the repository's highest degradation rates and had no scenario
  suite of its own, only a reproducibility assertion. It gains one, so the next semantic gap in the
  most degraded feed is caught by a test rather than by an attribution study.
