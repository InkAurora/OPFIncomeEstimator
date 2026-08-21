# Finances Simulator Contract Schema 1.5

Contract `1.5`, emitted by simulator engine `0.6.0`, adds deterministic incomplete observations to
schema `1.4`. Hidden economics and private truth retain schema `1.4` semantics. This remains a
project-owned, Open Finance-inspired contract rather than an official wire format.

## Version and determinism

- Every output record carries `schema_version: "1.5"`.
- The manifest carries `contract_schema_version: "1.5"` and `simulator_version: "0.6.0"`.
- The manifest records the full `config_sha256` and separate `world_config_sha256`.
- Economics use the frozen V4 world configuration and engine fingerprint.
- Observation policy uses a separate V5 configuration fingerprint and deterministic namespace.
- Changing only observation degradation leaves customer state, events, ledger entries, balances,
  product histories, and private ground truth unchanged.

## Configuration

Schema `1.5` contains every schema `1.4` field and adds `observation_degradation`.

### Consent coverage

`consent.default_coverage_percent`, every institution rule, and every account rule accept standard
levels `100`, `70`, or `40`. Institution rules override the default; account rules override their
institution. References must be unique and resolve to configured institutions or accounts.

Static account, card, loan, and investment records remain visible for nonzero standard consent.
Every dated stream is deterministically ranked within its account or product and retains the
nearest whole-record count for its coverage level:

```text
retained_count = round_half_up(eligible_count * coverage_percent / 100)
```

Account rules govern deposit balances and transactions. Institution rules govern card limits,
card transactions, invoices, invoice items, loan payments and balances, and investment transactions
and balances.

### Institution descriptions

Each `institution_descriptions` rule supplies a `description_prefix` and `reversal_prefix`.
Prefixes apply only to estimator-visible descriptions. Hidden financial-event and private truth
descriptions remain unchanged. Deposit transactions, card transactions and invoice items, and
investment transactions receive their provider prefix.

### Record degradation

The following basis-point rates apply after consent selection to deposit transactions:

| Field | Effect |
| --- | --- |
| `missing_record_basis_points` | Removes selected originals |
| `late_record_basis_points` | Moves `observed_at` after `posted_at` |
| `duplicate_record_basis_points` | Adds one linked copy of selected originals |
| `reversal_record_basis_points` | Adds one linked, opposite-direction record |

`maximum_late_days` and `maximum_reversal_delay_days` bound deterministic delays to `1..365` days.
Each rate is `0..10000`. Selection uses exact integer counts per account and stable SHA-256 ranks;
no degradation draw consumes or changes an economic simulation stream.

## Transaction lineage

Schema `1.5` deposit transactions retain all schema `1.4` fields and add:

| Field | Meaning |
| --- | --- |
| `observed_at` | Provider arrival date; never precedes `posted_at` |
| `duplicate_of_transaction_id` | Original observed record ID for a duplicate, else null |
| `reversal_of_transaction_id` | Original observed record ID for a reversal, else null |

Original records keep their V4 transaction IDs. Artifact IDs are unique and deterministic.
Duplicates preserve account, amount, direction, currency, description, posting date, and reported
balance. Reversals preserve account and amount, invert direction, restore the pre-original reported
balance, and carry the institution reversal description. No economic type, income-source identity,
life-event label, or anomaly label is exposed.

## Coverage dataset and manifest

`observed/observation_coverage.jsonl` contains one row per deposit account:

- configured coverage percent;
- eligible and consented original counts;
- observed original and missing counts;
- late, duplicate, and reversal counts;
- effective coverage in basis points.

Effective coverage excludes duplicates and reversals:

```text
effective_coverage_basis_points
  = round_half_up(observed_original_count * 10000 / eligible_count)
```

The manifest repeats aggregate eligible, observed-original, and effective-coverage values under
`observation_quality`. This makes coverage measurable without exposing private economic labels.

## Output tree

Schema `1.5` retains schema `1.4` private datasets and its 14 financial observation datasets, then
adds:

```text
observed/observation_coverage.jsonl
```

## Invariants

- V5 configurations that differ only in observation settings produce identical hidden economics
  and private truth.
- Consented, missing, and observed-original counts reconcile for every account.
- Effective coverage uses unique original records only.
- Duplicate and reversal links resolve to an emitted original record.
- Duplicate payloads match their source; reversal amount and account match while direction flips.
- Every arrival date is on or after its financial posting date.
- All schema `1.4` ledger, product, truth, reconciliation, determinism, and leakage invariants remain
  mandatory.

## Bounded limitations

- Missing, late, duplicate, and reversal injection currently targets deposit transactions; consent
  coverage targets every dated product stream.
- Coverage selects deterministic records across the run window rather than modeling consent start
  and expiry timestamps.
- Product metadata remains visible because standard coverage has no zero-percent level.
- Provider descriptions use configurable prefixes, not official institution payload formats.
- Authentication, tokens, transport failures, pagination, and official consent lifecycle remain
  adapter concerns.
