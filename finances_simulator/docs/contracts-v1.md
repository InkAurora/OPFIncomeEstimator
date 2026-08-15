# Finances Simulator Contract Schema 1.0

This document describes configuration schema `1.0` and every file emitted by simulator version
`0.1.0`. These are project-owned contracts inspired by financial categories available through Open
Finance Brasil. They are not official Open Finance wire schemas.

## Common rules

- Dates use ISO `YYYY-MM-DD`; month keys use `YYYY-MM`.
- Currency is a three-letter uppercase code. The validator checks format, not membership in a
  currency registry.
- Monetary fields ending in `_minor` are integers in the currency's minor unit. Amounts are positive
  unless a field says otherwise. Balance fields may be negative.
- Configuration objects reject unknown fields. Non-empty text fields trim surrounding whitespace.
- Output records carry `schema_version: "1.0"` and are immutable within the generator.
- Enum values are serialized as the uppercase strings shown below.
- Optional fields are serialized as JSON `null` when absent.

## Scenario configuration

The YAML document has exactly these top-level fields:

| Field | Type | Rule |
| --- | --- | --- |
| `schema_version` | string | Must equal `"1.0"`. |
| `scenario` | object | Timeline settings below. |
| `customer` | object | Customer and account settings below. |
| `salary` | object | Monthly salary rule below. |
| `fixed_expenses` | array | Exactly five fixed-expense rules. |
| `variable_expenses` | object | Variable-expense rule below. |

### `scenario`

| Field | Type | Rule |
| --- | --- | --- |
| `name` | string | Non-empty after trimming. |
| `start_date` | date | Must be the first day of a month. |
| `default_months` | integer | Greater than zero. Effective generation length must also be at most 1,200. |

### `customer`

| Field | Type | Rule |
| --- | --- | --- |
| `currency` | string | Exactly three uppercase ASCII letters. |
| `opening_balance_minor` | integer | Greater than or equal to zero. |
| `institution_id` | string | Non-empty after trimming. |
| `institution_name` | string | Non-empty after trimming. |
| `account_label` | string | Non-empty after trimming. |

V0 creates one active checking account from these settings.

### `salary`

| Field | Type | Rule |
| --- | --- | --- |
| `amount_minor` | integer | Greater than zero. Credited once per simulated month. |
| `day_of_month` | integer | Inclusive range 1 through 31. |
| `payer` | string | Non-empty after trimming. |
| `description` | string | Non-empty after trimming. Exposed in observed transactions. |

The configured amount becomes both the hidden true monthly salary and each monthly salary credit.
V0 applies no tax, deduction, gross/net, inflation, or proration calculation.

### `fixed_expenses[]`

| Field | Type | Rule |
| --- | --- | --- |
| `rule_id` | string | Non-empty after trimming; unique across all five rules. |
| `category` | string | Non-empty after trimming; private metadata only. |
| `amount_minor` | integer | Greater than zero; debited once per simulated month. |
| `day_of_month` | integer | Inclusive range 1 through 31. |
| `payee` | string | Non-empty after trimming. |
| `description` | string | Non-empty after trimming. Exposed in observed transactions. |

### `variable_expenses`

| Field | Type | Rule |
| --- | --- | --- |
| `count_min` | integer | Greater than or equal to zero. |
| `count_max` | integer | Greater than or equal to `count_min`. |
| `amount_min_minor` | integer | Greater than zero. |
| `amount_max_minor` | integer | Greater than or equal to `amount_min_minor`. |
| `day_min` | integer | Inclusive range 1 through 28. |
| `day_max` | integer | Between `day_min` and 28, inclusive. |
| `merchants` | array | At least one merchant object. |

Each merchant has exactly two non-empty strings: `entity` and `description`.

For every simulated month, V0 uses its versioned `sha256-counter-v1` generator to sample:

1. transaction count uniformly from `[count_min, count_max]`;
2. one merchant uniformly from the merchant list for each transaction;
3. transaction day uniformly from `[day_min, day_max]`; and
4. integer amount uniformly from `[amount_min_minor, amount_max_minor]`.

All bounds are inclusive. Draws share one isolated generator and therefore one deterministic
sequence. Unbiased rejection sampling maps SHA-256 counter blocks into each requested integer range;
the sequence does not depend on Python's standard-library random implementation.

## Calendar and posting semantics

The simulation starts on `scenario.start_date` and ends on the last calendar day of its final month.
Salary and every fixed expense occur once per month. A configured day that does not exist in a month
is clamped to that month's last day. For example, day 31 becomes February 28 or 29. No date is shifted
for weekends or Brazilian holidays.

Events are posted in ascending `(occurred_at, event_id)` order. `INCOME` produces a `CREDIT` and adds
to the account balance. `EXPENSE` produces a `DEBIT` and subtracts from it. V0 performs no available-
funds check, so `balance_after_minor`, monthly closing balances, and observed balances may be negative.

## Output directory

Generation accepts a nonexistent path or an existing empty directory. An existing non-empty
directory is rejected; files are never intentionally overwritten. The complete tree is first written
to a temporary sibling directory and then published with one directory rename. Failed writes clean up
their staging directory and leave no partial destination.

```text
<output>/
|-- run_manifest.json
|-- observed/
|   |-- accounts.jsonl
|   |-- balances.jsonl
|   `-- transactions.jsonl
`-- private/
    |-- customer_ground_truth.jsonl
    |-- customer_month_ground_truth.jsonl
    `-- transaction_ground_truth.jsonl
```

`private/` contains labels and causal truth used for evaluation. `observed/` is the only estimator-
visible boundary. Consumers must not join private records into estimator inputs, even though IDs make
evaluation joins possible.

For identical validated configuration, seed, effective month count, and simulator version, JSONL
record content and ordering are deterministic across supported Python runtimes. Records use compact
UTF-8 JSON with lexicographically sorted keys and a trailing newline. The manifest reports SHA-256
over exact JSONL bytes.

## Manifest contract

`run_manifest.json` is formatted JSON, not JSONL. It contains exactly:

| Field | Type | Meaning |
| --- | --- | --- |
| `config_sha256` | string | SHA-256 of compact, key-sorted canonical JSON for validated configuration. |
| `contract_schema_version` | string | `"1.0"`. |
| `datasets` | object | `observed` and `private` dataset metadata maps. |
| `months` | integer | Effective simulated month count. |
| `rng_algorithm` | string | Versioned deterministic generator; currently `"sha256-counter-v1"`. |
| `run_id` | string | Deterministic run ID derived from configuration hash, seed, and months. |
| `scenario_name` | string | Validated `scenario.name`. |
| `seed` | integer | CLI seed. |
| `simulation_window` | object | Exact `start_date` and `end_date` strings. |
| `simulator_version` | string | `"0.1.0"`. |

`datasets.observed` has keys `accounts`, `balances`, and `transactions`.
`datasets.private` has keys `customer_ground_truth`, `customer_month_ground_truth`, and
`transaction_ground_truth`. Every dataset metadata object contains exactly:

| Field | Type | Meaning |
| --- | --- | --- |
| `path` | string | POSIX-style path relative to the output directory. |
| `record_count` | integer | Number of JSONL records. |
| `schema_version` | string | `"1.0"`. |
| `sha256` | string | SHA-256 of exact file bytes. |

## Observed datasets

These records may be given to an estimator. They deliberately omit `economic_type`, income-source
labels, true salary, scenario labels, counterparties, causal links, and private event metadata.

### `observed/accounts.jsonl`

One record per run in V0:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.0"` |
| `customer_id` | string |
| `account_id` | string |
| `institution_id` | string |
| `institution_name` | string |
| `account_label` | string |
| `account_type` | `"CHECKING"` |
| `currency` | string |
| `opened_on` | `YYYY-MM-DD` string |
| `status` | `"ACTIVE"` |

### `observed/balances.jsonl`

One month-end record per simulated month:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.0"` |
| `balance_id` | string |
| `customer_id` | string |
| `account_id` | string |
| `reference_date` | `YYYY-MM-DD` string |
| `balance_minor` | integer; may be negative |
| `currency` | string |
| `balance_type` | `"CLOSING"` |

### `observed/transactions.jsonl`

One record per ledger entry, ordered by posting date and hidden event ID:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.0"` |
| `transaction_id` | string; equals private ledger-entry ID |
| `customer_id` | string |
| `account_id` | string |
| `posted_at` | `YYYY-MM-DD` string |
| `direction` | `"CREDIT"` or `"DEBIT"` |
| `amount_minor` | positive integer |
| `currency` | string |
| `description` | string |
| `balance_after_minor` | integer; may be negative |

## Private ground-truth datasets

These records are for simulator validation and estimator evaluation only. They must not enter
estimator features.

### `private/customer_ground_truth.jsonl`

One record per run in V0:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.0"` |
| `customer_id` | string |
| `scenario_name` | string |
| `employment_status` | `"SALARIED"` |
| `currency` | string |
| `true_monthly_salary_minor` | positive integer |
| `income_source_id` | string |
| `primary_account_id` | string |
| `opening_balance_minor` | non-negative integer |

### `private/customer_month_ground_truth.jsonl`

One chronological record per simulated month:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.0"` |
| `customer_id` | string |
| `month` | `YYYY-MM` string |
| `currency` | string |
| `true_income_minor` | non-negative integer |
| `true_expenses_minor` | non-negative integer |
| `income_event_count` | non-negative integer |
| `expense_event_count` | non-negative integer |
| `opening_balance_minor` | integer; may be negative after first month |
| `closing_balance_minor` | integer; may be negative |

### `private/transaction_ground_truth.jsonl`

One record per economic event and matching ledger entry:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.0"` |
| `event_id` | string |
| `entry_id` | string |
| `customer_id` | string |
| `account_id` | string |
| `occurred_at` | `YYYY-MM-DD` string |
| `economic_type` | Private economic classification; V0 emits `"INCOME"` or `"EXPENSE"`. |
| `direction` | `"CREDIT"` or `"DEBIT"` |
| `amount_minor` | positive integer |
| `currency` | string |
| `source_entity` | string |
| `destination_entity` | string |
| `income_source_id` | string or `null` |
| `caused_by_event_id` | string or `null` |
| `transfer_group_id` | string or `null`; reserved for paired own-account transfers. |
| `description` | string |
| `balance_after_minor` | integer; may be negative |
| `metadata` | object whose values are strings or integers |

V0 salary metadata has `income_kind` and `schedule_month`. Fixed-expense metadata has
`expense_kind`, `expense_category`, and `rule_id`. Variable-expense metadata has `expense_kind` and
`transaction_index`.

## V0 limitations and out-of-scope behavior

- One customer, checking account, salaried employment, salary source, and currency per run.
- V0 emits only `INCOME` and `EXPENSE`. Schema 1.0 also reserves `OWN_TRANSFER`,
  `INVESTMENT_CONTRIBUTION`, `INVESTMENT_REDEMPTION`, `LOAN_DISBURSEMENT`, `LOAN_PAYMENT`, `REFUND`,
  `REVERSAL`, `GIFT`, `ASSET_SALE`, and `OTHER` for later phases.
- Exactly five fixed expenses plus uniformly sampled variable expenses.
- No variable income, commissions, multiple income sources, employment changes, income gaps, or
  seasonal income.
- No transfers, refunds, reversals, loans, cash deposits, cards, investments, interest, taxes,
  overdraft rules, or inflation.
- No observation loss, duplicate observations, inconsistent descriptions, consent filtering, or
  institution-specific transformations.
- No authentication, consent management, token storage, provider transport, official API payloads,
  or Open Finance certification.
- Synthetic output supports testing; it does not establish estimator accuracy or population
  representativeness.
