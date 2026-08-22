# Finances Simulator Contract Schema 1.6

Contract `1.6`, emitted by simulator engine `0.7.0`, corrects the reversal semantics of schema
`1.5`. Hidden economics and private truth retain schema `1.4` semantics and are byte-identical to
what schema `1.5` produced for the same world configuration. This remains a project-owned, Open
Finance-inspired contract rather than an official wire format.

## Why this version exists

Schema `1.5` injected an artifact reversal and stopped there. The reversed amount left the observed
ledger permanently while private truth kept it, so no estimator could be simultaneously correct
about income and consistent with the observed feed. Measured on the `noisy_observation` suite at
estimator `0.8`, that gap was the entire realized income error: WAPE `0.17934555`, all of it
attributable to reversed originals.

Schema `1.6` follows every artifact reversal with a corrected re-post, which is what a bank does
after posting an erroneous reversal. See
[ADR 0004](../../docs/adr/0004-observed-balance-and-reversal-semantics.md).

## Version and determinism

- Every output record carries `schema_version: "1.6"`.
- The manifest carries `contract_schema_version: "1.6"` and `simulator_version: "0.7.0"`.
- The manifest records the full `config_sha256` and separate `world_config_sha256`.
- Economics use the frozen V4 world configuration and engine fingerprint.
- Observation policy uses a separate V6 configuration fingerprint and deterministic namespace.
- Changing only observation degradation leaves customer state, events, ledger entries, balances,
  product histories, and private ground truth unchanged.

## Configuration

Schema `1.6` contains every schema `1.5` field and adds none. The correction is mandatory rather
than configurable: a reversal without its correction is the defect this version removes, so a rate
that could disable the correction would leave that defect reachable.

## Transaction lineage

Schema `1.6` deposit transactions retain all schema `1.5` fields and add:

| Field | Meaning |
| --- | --- |
| `repost_of_transaction_id` | Original observed record ID for a corrected re-post, else null |

`duplicate_of_transaction_id`, `reversal_of_transaction_id`, and `repost_of_transaction_id` are
mutually exclusive. Original records keep their V4 transaction IDs; artifact IDs are unique and
deterministic.

### Reversals and their corrections

Every injected reversal is followed by exactly one re-post:

| Record | Amount | Direction | Reported balance | Description |
| --- | --- | --- | --- | --- |
| original | `A` | as posted | truth balance after the entry | provider prefix |
| reversal | `A` | inverted | balance the original replaced | provider prefix, reversal prefix |
| re-post | `A` | as posted | truth balance after the entry | provider prefix |

The re-post carries the original's description and **drops** the reversal prefix. Estimator income
rules read descriptions, so a correction that still read as a reversal would be excluded by keyword
and would never land.

A re-post arrives on or after its reversal, bounded by `maximum_reversal_delay_days`. A correction
that has not arrived by a request cutoff is late data, not lost data, which is the difference this
version makes.

## Coverage dataset and manifest

`observed/observation_coverage.jsonl` retains every schema `1.5` field and adds
`repost_record_count`. Effective coverage still counts unique original records only:

```text
effective_coverage_basis_points
  = round_half_up(observed_original_count * 10000 / eligible_count)
```

Re-posts are reported separately so a correction can never inflate `observed_original_record_count`.
Every account satisfies `repost_record_count == reversal_record_count`.

## Output tree

Schema `1.6` retains the schema `1.5` private and observed datasets unchanged.

## Invariants

Schema `1.6` keeps every schema `1.5` invariant and adds the observed-versus-truth reconciliation
rules, which no earlier contract stated:

- **R1** — an observed record with no lineage field carries the `balance_after_minor` of the truth
  entry with the same ID.
- **R2** — a reversal record carries the balance the original replaced.
- **R3** — a duplicate record's amount, direction, account, currency, posting date, and balance match
  its source.
- **R4** — a re-post carries the amount, direction, account, currency, and reported balance of the
  original it corrects, and arrives on or after its reversal.
- **R5** — for an account at full consent with `missing_record_basis_points` of `0`, folding the
  signed amounts of every observed record except duplicates reproduces the truth balance movement.
  Each reversal and re-post pair contributes zero net movement.

R5 holds only because the correction exists. Under schema `1.5` it is false for every account
carrying a reversal.

An observed `balance_after_minor` is a carried provider report, not a running total derived by
folding observed amounts. No contract guarantees that folding reproduces a balance, and none can:
missing records, duplicates, reversals, and partial consent each break a folded balance
independently. R5 is stated for the one configuration where the other three levers are inactive.

## Bounded limitations

- Missing, late, duplicate, reversal, and re-post injection currently targets deposit transactions;
  consent coverage targets every dated product stream.
- Coverage selects deterministic records across the run window rather than modeling consent start
  and expiry timestamps.
- Product metadata remains visible because standard coverage has no zero-percent level.
- Provider descriptions use configurable prefixes, not official institution payload formats.
- Every reversal is an observation artifact. `EconomicType.REVERSAL` remains reserved and unemitted,
  so this contract does not model a reversal that genuinely cancels an economic event.
- Authentication, tokens, transport failures, pagination, and official consent lifecycle remain
  adapter concerns.
