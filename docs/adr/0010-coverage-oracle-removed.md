# ADR 0010: The coverage oracle is removed from every estimator input path

- Status: Accepted
- Date: 2026-09-09
- Supersedes: the routing rule of
  [ADR 0009](0009-routing-narrowed-and-recalibrated.md), `deterministic-routing-0.7.0`, the
  calibration fitted around it, `conditional-selector-intervals-0.12.0`, and the capacity model
  they were measured against, `capacity-gbdt-stumps-0.6.0`
- Input contract: `1.3` (new); `1.0`–`1.2` still parsed, their `coverage` records no longer read
- Feature set: `customer-month-features-1.3.0`
- Routing contract: `deterministic-routing-0.8.0`
- Capacity contract: `capacity-gbdt-stumps-0.7.0`
- Calibration contract: `conditional-selector-intervals-0.13.0`
- Estimator milestone: `0.13`
- Review that found it: [9 September 2026](../project-review-2026-09-09-and-plan.md), finding 1

## Context

Every estimator input contract through `1.2` carried one `coverage` record per account with
`eligible_record_count` and `observed_original_record_count`. The simulator computed both from the
records it had generated and then withheld (`projector_v5.py`, lines 335–359). The cash-flow
reconstruction divided observed income by their ratio (`models/cashflow.py`, line 64), and the
recurring reconstruction used the same ratio to decide whether a stream was allowed to fill a gap.
The feature `effective_consent_coverage_basis_points` was the same ratio, eligible-record weighted,
and ADR 0009 routed to the cash-flow component exactly where that feature was below `10_000`.

No receiver of Open Finance data has these numbers. A provider does not report how many records a
customer's unconsented institutions hold, and a receiver cannot count records it never fetched. On
the `incomplete_observation` suite the division was exact: hidden income was recovered by dividing
by the exact hidden fraction. The `12179.10` MAE that promoted routing `0.7.0` was the oracle being
read twice, once by the feature that fired the rule and once by the component the rule selected.
Fresh seeds confirmed it because fresh seeds carry the same field.

The 8 September review had listed this as finding 7, "retrospective coverage fields", and left it
open as the hardest of nine. The 9 September review showed it was also the most consequential.

## Decision

**Nothing the estimator reads may encode information a receiver could not have written at the
cutoff.** Concretely:

1. **Input contract `1.3`** forbids `coverage` (typed as an always-empty tuple, so a payload carrying
   the oracle fails validation with a message rather than being silently ignored) and requires one
   `consent_scopes` record per account: `fetched_from`, `fetched_through`, `pagination_complete`.
   All three come from the receiver's own request log. Contracts `1.0`–`1.2` still validate; their
   `coverage` records are not read anywhere, so they behave as if they declared no receiver-known
   gaps.

2. **A month is a gap only if the receiver knows it did not fetch it** — its consent scope does not
   cover the month end to end, or pagination did not complete. `consent_scope.py` is the one module
   that answers this, and `slice_request` clips the scope to each reference month's cutoff so a
   fetch that ran later cannot be read earlier.

3. **Observed income is never scaled upward.** `reconstruct_monthly_income` reports the sum of
   classified credits. A known gap widens the interval (`2_500` basis points against `500`); it does
   not multiply the estimate. `reconstruct_recurring_income` fills a stream's expected amount only
   into months that its cadence calls due *and* that are receiver-known gaps for one of its
   accounts. A month the receiver fetched and found empty is a non-payment month.

4. **Features.** `effective_consent_coverage_basis_points` and
   `minimum_account_coverage_basis_points` are removed. `fetched_window_coverage_basis_points`
   replaces them: the share of account-months through the cutoff the receiver fully fetched, and
   `CONTRACT_DOMAIN_UNAVAILABLE` for inputs older than `1.3`. `data_completeness_score_basis_points`
   uses it when present. The feature set is `1.3.0` with a new fingerprint; the `0.12.0` bundle
   refuses to load against this code, as it should.

5. **Training and evaluation keep the simulator's coverage as a private label.** `CapacityRow`
   carries `declared_coverage_basis_points`, joined from `observation_coverage` after inference like
   the income targets, so the `consent_coverage` segment in every report still reads the
   generator's truth. It is never in `features`. Scenarios that project no coverage record withheld
   nothing and are labelled complete.

6. **Routing `0.8.0` selects the capacity model wherever it is available.** The `0.7.0` rule was
   first re-pointed at `fetched_window_coverage_basis_points` and re-measured; it never fired on the
   benchmark population and improved no segment, so it was removed rather than kept dormant. The
   reason codes still report coverage and volatility so a reviewer sees the evidence the earlier
   rules acted on. The benchmark's "improves a segment" gate now applies only when some rule
   actually routed away from the model on the population, because with no such rule the condition
   is unsatisfiable by construction.

7. **Recalibration.** Both halves of what the residuals were taken around moved, so
   `conditional-selector-intervals-0.13.0` is refitted, its conditioner re-selected, and its lockbox
   drawn from seed floor `1_010_000`. Floors `810_000` and `910_000` are spent: the first by the
   `0.12.0` release read, the second by the ADR 0009 confirmation.

## Measured

Capacity `0.7.0` against `0.6.0`, same seeds (`110_000`–`130_000`, 240 customers per suite), same
1,308 held-out rows:

| Component | `0.6.0` MAE | `0.7.0` MAE |
| --- | ---: | ---: |
| capacity model | `25217.44` (WAPE `0.0499`) | `25374.23` (WAPE `0.0502`) |
| `cash_flow_last_month` | `77593.12` | `93648.17` |
| `recurring_stream_mean_3m` | `74469.46` | `88078.02` |
| `historical_median_12m` | `84089.40` | `84089.40` |

By the simulator's private coverage label, the capacity model on `partial_high` rows moves from
`13469.21` to `11647.99` and on `complete` rows from `32582.00` to `33978.74`. Its own error barely
moves: the two removed features were not what it was fitting on. The two components that divided
by the oracle get worse by `21%` and `18%`, which is the correct direction; they were never as good
as they measured. `0.7.0` is `PROMOTED` by the unchanged gate against `historical_median_12m`.

Routing benchmark, seeds `310_000`–`330_000`, 80 customers per suite, 312 held-out rows:

| Rule | Capacity | Routed MAE | Capacity alone | `partial_high` routed / alone |
| --- | --- | ---: | ---: | ---: |
| `0.7.0` (oracle) | `0.6.0` | `12179.10` | `14910.78` | `1720.56` / `9612.08` |
| `0.7.0` re-pointed at fetch coverage | `0.7.0` | `13973.25` | `13973.25` | `8095.43` / `8095.43` |

Re-pointed, the rule fired on zero of 312 rows: the simulator declares full-window scopes, so no
row is receiver-known partial, and the `99` rows that had routed under the oracle now stay with the
model. `1720.56` on the rows where the oracle fired is what an exact division looks like. The
benchmark reported `NOT_PROMOTED` for the re-pointed rule ("improves no segment"), which is the
result that removes it.

## Consequences

- The `partial_consent` stress figure (`0.9375` coverage, `0.66%` sustainable WAPE in the 8
  September review) and every `incomplete_observation` number quoted before this ADR measured the
  oracle. They are withdrawn. The model card carries the corrected figures.
- The simulator's `observation_coverage` dataset is unchanged and still validated; it is an
  evaluation-zone artifact now, like the ground truth it always resembled.
- `finances_simulator.integration.build_estimator_input_v1_3` declares full-window scopes with
  `pagination_complete=True`, because the simulator fetches everything it emits. Under it the
  recurring estimator has no receiver-known gaps on `incomplete_observation` and matches the
  baseline exactly: the simulator withholds records without telling the receiver, which is
  precisely the situation where an honest estimator must not invent income. The benchmark gate
  that required the recurring estimator to beat the baseline on that suite is removed; the
  do-not-regress gate stays.
- Contract `1.3` carries exactly one scope per account. Session-level fetch gaps (two fetches with
  a month between them) are deferred to the provider adapter of plan order 5, where the payload
  shape is known.
- Order 2 of the plan (ablations sizing generator inversion; regime and time holdouts) is the next
  protection against the class of defect this ADR closes. This ADR closes one instance.
