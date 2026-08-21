# Phase-7 Batch and Estimator Contract 1.0

Batch schema `1.0`, implemented by simulator orchestrator `0.7.0`, composes versioned single-customer
runs into deterministic populations. Current examples use frozen engine `0.6.0` and observation
contract `1.5`; Phase 7 does not revise their record fields or economics.

## Identity and determinism

Given canonical scenario configuration, first seed, population size, effective month count, and
orchestrator version, `batch_id` is stable. Member index `n` uses `first_seed + n`. Parallel workers
return members in index order. Worker count and completion order never enter random streams,
identifiers, sorting, partitions, manifests, estimates, or reports.
Population size is bounded from 1 through 100,000 members.

Every member is re-parsed through its exact Pydantic record model before output. All records must use
the selected member contract, belong to the expected customer, and preserve the observed/private
field boundary.

## Parquet layout

```text
<output>/
|-- population_manifest.json
|-- observed/<dataset>/customer_bucket=<nn>/part-00000.parquet
|-- private/<dataset>/customer_bucket=<nn>/part-00000.parquet
`-- evaluation/
    |-- estimates/customer_bucket=<nn>/part-00000.parquet
    `-- report.json
```

`customer_bucket` is the unsigned first eight bytes of SHA-256 over UTF-8 `customer_id`, modulo the
configured partition count. Each populated source record gains four envelope columns:

- `batch_id`;
- `run_id`;
- `seed`;
- `customer_bucket`.

Primitive repeated fields remain Parquet lists. Open maps and nested objects use compact canonical
JSON strings to keep one stable Arrow type across partitions. Empty logical datasets have a zero
count and no physical part file. Every part uses Zstandard compression and carries embedded
`batch_schema_version`, `contract_schema_version` where applicable, dataset, and trust metadata.

`population_manifest.json` records partition policy, all seeds and run IDs, logical row counts,
Arrow-schema SHA-256 digests, physical part counts and SHA-256 digests, source engine/contract,
estimator versions, and evaluation paths. Output is staged beside its destination and published by
one rename; non-empty destinations are never overwritten.

## Estimator input 1.0

The input is an immutable allow list, not a pass-through of simulator bundles:

```text
schema_version = "1.0"
source_contract_schema_version
run_id, customer_id, currency
window_start, window_end, months
accounts[]: customer_id, account_id, institution_id, currency
transactions[]: id, customer/account, posted/observed dates, direction, amount,
                currency, description, duplicate/reversal lineage
loans[]: customer_id, loan_id, disbursement_transaction_id
investment_transactions[]: customer_id, id, type, related_account_transaction_id
coverage[]: customer/account, configured percent, eligible/observed counts,
            effective coverage basis points
```

Ground-truth classifications, true income, income profiles/sources, life events, anomalies, and
latent state are absent. Older observation contracts normalize missing arrival/lineage fields and
use complete-coverage defaults.

An estimator implements `estimate(request)` or is directly callable. Output contract `1.0` contains
estimator version, input identity/currency, and one ordered record for every simulation month. Each
monthly record contains estimated income in minor units, inclusive lower/upper confidence bounds,
and unique contributing transaction IDs. Boundary validation rejects mismatched identity, currency,
months, malformed intervals, duplicate evidence, and evidence absent from input observations.

## Evaluation report 1.0

Private truth is joined only after estimator execution. `evaluation/report.json` contains:

- overall mean and median absolute error in minor units;
- error grouped by private income type and fixed true-income ranges;
- error grouped by effective consent coverage;
- error inside and outside a one-month neighborhood around life events;
- selected-credit misclassification counts for `OWN_TRANSFER`, `LOAN_DISBURSEMENT`, and
  `INVESTMENT_REDEMPTION`;
- empirical confidence-interval coverage.

`evaluation/estimates` is non-private and retains estimator evidence. The report contains aggregate
truth comparisons, not row-level private truth. The bundled `baseline-1.0.0` estimator is an
integration fixture, not a production model or accuracy claim.
