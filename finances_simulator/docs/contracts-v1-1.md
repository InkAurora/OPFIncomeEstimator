# Finances Simulator Contract Schema 1.1

This document specifies configuration and output contract `1.1`, emitted by simulator engine
`0.2.0`. It extends the simulator with multiple deposit accounts, own-account transfers, credit
cards, limits, statements, full invoice payments, and installment purchases.

Contract `1.1` is project-owned and Open Finance-inspired. It is not an official Open Finance wire
schema. Contract [`1.0`](contracts-v1.md) remains frozen and supported by its legacy engine path.

## Common rules

- Dates use ISO `YYYY-MM-DD`; month keys use `YYYY-MM`.
- Currency is one three-letter uppercase code shared by all products in a run.
- Fields ending in `_minor` contain integer currency minor units. Amounts are non-negative or
  positive as specified; deposit balances may be negative.
- Basis points use `10_000` as 100%.
- Configuration rejects unknown fields. Non-empty strings trim surrounding whitespace.
- Machine references and rule IDs match `[A-Za-z0-9][A-Za-z0-9_.-]*`; delimiters such as `:` are
  rejected so composite deterministic-ID keys remain unambiguous.
- Every output record carries `schema_version: "1.1"`.
- Optional output fields serialize as JSON `null`.
- Private economic classifications, event IDs, rule metadata, and transfer groups never enter
  estimator-visible records.
- JSONL uses compact UTF-8 JSON, lexicographically sorted keys, and one trailing newline per record.

## Scenario configuration

Contract `1.1` has exactly these top-level fields:

| Field | Type | Rule |
| --- | --- | --- |
| `schema_version` | string | Must equal `"1.1"`. |
| `scenario` | object | Timeline settings. |
| `customer` | object | Currency and primary-account reference. |
| `institutions` | array | At least two institutions. |
| `accounts` | array | At least two deposit accounts. |
| `salary` | object | Routed monthly salary rule. |
| `fixed_expenses` | array | Exactly five routed monthly expense rules. |
| `variable_expenses` | object | Routed random monthly expense rule. |
| `own_transfers` | array | At least one monthly own-account transfer rule. |
| `credit_cards` | array | At least one card and payment policy. |
| `card_purchase_rules` | array | Between 1 and 256 deterministic purchase schedules. |

### `scenario`

| Field | Type | Rule |
| --- | --- | --- |
| `name` | string | Non-empty. |
| `start_date` | date | First day of a month. |
| `default_months` | integer | Positive; effective CLI duration remains `1..1200`. |

### `customer`

| Field | Type | Rule |
| --- | --- | --- |
| `currency` | string | Exactly three uppercase ASCII letters. |
| `primary_account_ref` | string | Must reference a configured `CHECKING` account. |

Account and card currencies inherit `customer.currency`; schema `1.1` has no multi-currency mode.

### `institutions[]`

| Field | Type | Rule |
| --- | --- | --- |
| `institution_ref` | string | Non-empty configuration reference; unique. |
| `institution_id` | string | Non-empty observed institution identifier; unique. |
| `institution_name` | string | Non-empty observed name. |

Every account and card institution reference must resolve.

### `accounts[]`

| Field | Type | Rule |
| --- | --- | --- |
| `account_ref` | string | Non-empty configuration reference; unique. |
| `institution_ref` | string | Must reference `institutions[]`. |
| `account_label` | string | Non-empty observed label. |
| `account_type` | enum | `CHECKING` or `SAVINGS`. |
| `opening_balance_minor` | integer | Greater than or equal to zero. |

Every account is active and opens on `scenario.start_date`. Later balances may be negative; no
available-funds or overdraft-limit check is applied.

### `salary`

| Field | Type | Rule |
| --- | --- | --- |
| `amount_minor` | integer | Positive. |
| `day_of_month` | integer | `1..31`. |
| `payer` | string | Non-empty private source entity. |
| `description` | string | Non-empty observed transaction description. |
| `destination_account_ref` | string | Must reference an account. |

Salary creates one `INCOME` event and one account credit per simulated month.

### `fixed_expenses[]`

| Field | Type | Rule |
| --- | --- | --- |
| `rule_id` | string | Non-empty; unique within `fixed_expenses`. |
| `category` | string | Non-empty private category. |
| `amount_minor` | integer | Positive. |
| `day_of_month` | integer | `1..31`. |
| `payee` | string | Non-empty private destination entity. |
| `description` | string | Non-empty observed description. |
| `source_account_ref` | string | Must reference an account. |

Each rule creates one `EXPENSE` event and one account debit per simulated month.

### `variable_expenses`

| Field | Type | Rule |
| --- | --- | --- |
| `count_min` | integer | `0..500`. |
| `count_max` | integer | Between `count_min` and 500. |
| `amount_min_minor` | integer | Positive. |
| `amount_max_minor` | integer | Greater than or equal to `amount_min_minor`. |
| `day_min` | integer | `1..28`. |
| `day_max` | integer | Between `day_min` and 28. |
| `merchants` | array | At least one `{entity, description}` object containing non-empty strings. |
| `source_account_ref` | string | Must reference an account. |

Per month, the engine samples count, merchant, day, and integer amount uniformly over inclusive
bounds. Schema `1.1` uses the deterministic `deposit-variable-expenses-v1` RNG stream derived from
the run seed. Card purchase schedules do not consume this stream.

### `own_transfers[]`

| Field | Type | Rule |
| --- | --- | --- |
| `rule_id` | string | Non-empty; unique within `own_transfers`. |
| `source_account_ref` | string | Must reference an account. |
| `destination_account_ref` | string | Must reference a different account. |
| `amount_minor` | integer | Positive. |
| `day_of_month` | integer | `1..31`. |
| `outgoing_description` | string | Non-empty source-account description. |
| `incoming_description` | string | Non-empty destination-account description. |

Each rule runs once per simulated month. Insufficient source funds do not block it.

### `credit_cards[]`

| Field | Type | Rule |
| --- | --- | --- |
| `card_ref` | string | Non-empty configuration reference; unique. |
| `institution_ref` | string | Must reference `institutions[]`. |
| `card_label` | string | Non-empty observed label. |
| `credit_limit_minor` | integer | Positive fixed total limit. |
| `statement_close_day` | integer | `1..31`. |
| `payment_due_day` | integer | `1..31`. |
| `payment_account_ref` | string | Must reference a deposit account. |
| `payment_description` | string | Non-empty observed account-transaction description. |
| `payment_policy` | enum | Must equal `FULL_AUTOPAY`. |
| `utilization_policy` | object | Authorization ceiling below. |

`utilization_policy` contains exactly:

| Field | Type | Rule |
| --- | --- | --- |
| `maximum_basis_points` | integer | `1..10000`. |
| `on_exceed` | enum | Must equal `DECLINE`. |

Cards start active on `scenario.start_date`, with zero outstanding balance and no prior invoice.

### `card_purchase_rules[]`

| Field | Type | Rule |
| --- | --- | --- |
| `rule_id` | string | Non-empty; unique within `card_purchase_rules`. |
| `card_ref` | string | Must reference a card. |
| `merchant` | string | Non-empty private destination entity. |
| `description` | string | Non-empty observed card description. |
| `amount_minor` | integer | Positive and at least `installment_count`. |
| `day_of_month` | integer | `1..31`. |
| `start_month_index` | integer | Zero-based index, greater than or equal to zero. |
| `interval_months` | integer | Positive spacing between attempts. |
| `occurrences` | integer | `1..1200` attempted schedule occurrences. |
| `installment_count` | integer | `1..120`. `1` means a single-payment purchase. |

Attempt `i` uses month index `start_month_index + i * interval_months`. Attempts outside the
effective simulation duration are ignored. Same-day attempts are authorized in ascending
`(date, rule_id, occurrence_index)` order.

Across all rules, configuration permits at most 10,000 attempted purchases and 250,000 scheduled
installment items (`occurrences * installment_count`). These bounds prevent configuration values
from amplifying into unbounded work.

## Calendar, transfers, and posting

Configured days beyond a calendar month's end clamp to its final day. Dates do not move for
weekends or holidays.

Deposit-account effects and output rows use deterministic
`(posted_at, posting_priority, event_id, entry_id)` order. Priorities are income, own transfer,
card payment, then deposit expense. Each account reconciles independently:

```text
closing_balance = opening_balance + credits - debits
```

One `OWN_TRANSFER` event produces exactly two same-date ledger entries:

- debit from its source account;
- credit to its destination account;
- equal positive amounts; and
- one shared private `transfer_group_id`.

The pair changes no aggregate deposit balance. The event is neither income nor expense. Observed
transactions expose the two account postings and descriptions, but not `economic_type` or
`transfer_group_id`.

## Card authorization and utilization

Maximum authorized used limit is:

```text
floor(credit_limit_minor * maximum_basis_points / 10000)
```

An accepted purchase immediately increases used limit by its full amount, including installments
assigned to future statements. An attempt is declined when `used_before + amount_minor` exceeds the
maximum. Declined attempts create no event, transaction, truth record, invoice item, or decline
record.

Paid installment amounts release used limit on their invoice due dates:

```text
used_limit = accepted purchase totals to date - installment amounts due to date
available_limit = total_limit - used_limit
```

Month-end `credit_limits` snapshots must satisfy
`total_limit_minor = used_limit_minor + available_limit_minor`.

## Statements, installments, and payments

A purchase on or before its clamped statement close date enters that statement. A later purchase
enters the next month's statement. Subsequent installments enter consecutive statement months.

Integer installment split is exact. For amount `A` and count `N`:

```text
base, remainder = divmod(A, N)
```

The first `remainder` installments receive `base + 1`; remaining installments receive `base`.
Installment amounts always sum to the purchase total.

Invoice due date is the first clamped `payment_due_day` strictly after statement close. No invoice
is emitted for an empty statement. Only statements closing on or before simulation end are emitted;
future scheduled installments remain hidden until their statement enters a longer run.

If due date is within the simulation window, `FULL_AUTOPAY` creates:

- a `PAID` invoice whose paid amount equals amount due;
- `paid_at` equal to due date;
- one `CARD_PAYMENT` event; and
- one debit from `payment_account_ref` for full invoice amount.

The account may become negative. `CARD_PAYMENT` is cash settlement, not new expense or income. An
invoice whose due date is after simulation end remains `CLOSED`, with zero payment and null payment
fields.

A card purchase is one `EXPENSE` economic event for its full amount on purchase date. Invoice items
allocate that liability across statements; neither installment allocation nor invoice payment is a
second expense.

## Output directory

Schema `1.1` emits:

```text
<output>/
|-- run_manifest.json
|-- observed/
|   |-- accounts.jsonl
|   |-- balances.jsonl
|   |-- transactions.jsonl
|   |-- credit_cards.jsonl
|   |-- credit_limits.jsonl
|   |-- credit_card_transactions.jsonl
|   |-- credit_card_invoices.jsonl
|   `-- credit_card_invoice_items.jsonl
`-- private/
    |-- customer_ground_truth.jsonl
    |-- customer_month_ground_truth.jsonl
    |-- transaction_ground_truth.jsonl
    `-- credit_card_transaction_ground_truth.jsonl
```

Generation accepts a nonexistent path or existing empty directory and refuses a non-empty target.
It stages the complete tree in a temporary sibling directory, then publishes it with one rename.

## Manifest

`run_manifest.json` retains the schema `1.0` manifest fields:

| Field | Type or value |
| --- | --- |
| `config_sha256` | SHA-256 of compact, key-sorted canonical validated configuration JSON. |
| `contract_schema_version` | `"1.1"`. |
| `datasets` | `observed` and `private` metadata maps matching the tree above. |
| `months` | Effective simulated month count. |
| `rng_algorithm` | `"sha256-counter-v1"`. |
| `run_id` | Deterministic ID derived from configuration hash, seed, months, and engine version. |
| `scenario_name` | Configured scenario name. |
| `seed` | CLI seed. |
| `simulation_window` | Exact `start_date` and `end_date`. |
| `simulator_version` | `"0.2.0"`. |

Each dataset metadata object contains `path`, `record_count`, `schema_version`, and SHA-256 of exact
JSONL bytes.

## Observed datasets

These records form the estimator-visible boundary. They omit source/destination entities, hidden
event IDs, `economic_type`, income-source IDs, causal links, rule metadata, and transfer groups.

### `observed/accounts.jsonl`

One record per account:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `customer_id` | string |
| `account_id` | string |
| `institution_id` | string |
| `institution_name` | string |
| `account_label` | string |
| `account_type` | `CHECKING` or `SAVINGS` |
| `currency` | string |
| `opened_on` | date string |
| `status` | `ACTIVE` |

### `observed/balances.jsonl`

One month-end record per account per simulated month:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `balance_id` | string |
| `customer_id` | string |
| `account_id` | string |
| `reference_date` | month-end date string |
| `balance_minor` | integer; may be negative |
| `currency` | string |
| `balance_type` | `CLOSING` |

### `observed/transactions.jsonl`

One record per deposit-account ledger entry:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `transaction_id` | ledger-entry ID |
| `customer_id` | string |
| `account_id` | string |
| `posted_at` | date string |
| `direction` | `CREDIT` or `DEBIT` |
| `amount_minor` | positive integer |
| `currency` | string |
| `description` | string |
| `balance_after_minor` | integer; may be negative |

Salary, deposit expenses, both transfer legs, and invoice-payment debits appear here. Card purchases
do not affect this ledger directly.

### `observed/credit_cards.jsonl`

One record per card:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `customer_id` | string |
| `card_id` | string |
| `institution_id` | string |
| `institution_name` | string |
| `card_label` | string |
| `currency` | string |
| `opened_on` | date string |
| `status` | `ACTIVE` |

Payment-account routing, utilization policy, and statement policy are not exposed on this record.

### `observed/credit_limits.jsonl`

One month-end record per card per simulated month:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `credit_limit_id` | string |
| `customer_id` | string |
| `card_id` | string |
| `reference_date` | month-end date string |
| `total_limit_minor` | positive integer |
| `used_limit_minor` | non-negative integer |
| `available_limit_minor` | non-negative integer |
| `currency` | string |

### `observed/credit_card_transactions.jsonl`

One record per accepted card purchase, containing full purchase amount rather than one row per
installment:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `card_transaction_id` | string |
| `customer_id` | string |
| `card_id` | string |
| `occurred_at` | purchase date string |
| `amount_minor` | positive full purchase amount |
| `currency` | string |
| `description` | string |
| `installment_count` | positive integer |
| `status` | `POSTED` |

### `observed/credit_card_invoices.jsonl`

One record per nonempty statement closed during the run:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `invoice_id` | string |
| `customer_id` | string |
| `card_id` | string |
| `statement_close_date` | date string |
| `due_date` | first configured due date after close |
| `amount_due_minor` | positive sum of invoice items |
| `paid_amount_minor` | amount due for `PAID`; zero for `CLOSED` |
| `currency` | string |
| `status` | `PAID` or `CLOSED` |
| `paid_at` | due-date string for `PAID`; otherwise `null` |
| `payment_transaction_id` | linked deposit transaction ID for `PAID`; otherwise `null` |

### `observed/credit_card_invoice_items.jsonl`

One record per installment assigned to an emitted invoice:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `invoice_item_id` | string |
| `customer_id` | string |
| `card_id` | string |
| `invoice_id` | linked invoice ID |
| `card_transaction_id` | linked purchase ID |
| `installment_number` | one-based positive integer |
| `installment_count` | purchase installment count |
| `amount_minor` | positive installment amount |
| `currency` | string |
| `description` | purchase description |

For each invoice, `amount_due_minor` equals the sum of its item amounts.

## Private datasets

Private records support reconciliation and estimator evaluation. They must not become estimator
features.

### `private/customer_ground_truth.jsonl`

One customer record:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `customer_id` | string |
| `scenario_name` | string |
| `employment_status` | `SALARIED` |
| `currency` | string |
| `true_monthly_salary_minor` | positive integer |
| `income_source_id` | string |
| `primary_account_id` | string |
| `opening_balance_minor` | primary-account opening balance |
| `account_ids` | ordered array of all account IDs, primary first |
| `card_ids` | ordered array of card IDs |
| `total_opening_deposit_balance_minor` | sum of all account opening balances |

### `private/customer_month_ground_truth.jsonl`

One chronological record per simulated month:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `customer_id` | string |
| `month` | `YYYY-MM` |
| `currency` | string |
| `true_income_minor` | sum of `INCOME` events |
| `true_expenses_minor` | sum of `EXPENSE` events, including full card purchases |
| `income_event_count` | count of `INCOME` events |
| `expense_event_count` | count of `EXPENSE` events |
| `opening_balance_minor` | primary-account opening balance for month |
| `closing_balance_minor` | primary-account closing balance for month |
| `total_deposit_opening_balance_minor` | sum across accounts at month start |
| `total_deposit_closing_balance_minor` | sum across accounts at month end |
| `total_card_outstanding_opening_minor` | aggregate used card limit at month start |
| `total_card_outstanding_closing_minor` | aggregate used card limit at month end |

Own transfers and `CARD_PAYMENT` events contribute zero to both true-income and true-expense sums.

### `private/transaction_ground_truth.jsonl`

One record per deposit ledger entry:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `event_id` | hidden event ID |
| `entry_id` | ledger-entry ID |
| `customer_id` | string |
| `account_id` | affected account ID |
| `occurred_at` | posting date string |
| `economic_type` | `INCOME`, `EXPENSE`, `OWN_TRANSFER`, or `CARD_PAYMENT` |
| `direction` | `CREDIT` or `DEBIT` |
| `amount_minor` | positive integer |
| `currency` | string |
| `source_entity` | hidden event source |
| `destination_entity` | hidden event destination |
| `income_source_id` | string or `null` |
| `caused_by_event_id` | string or `null` |
| `transfer_group_id` | shared ID on own-transfer legs; otherwise `null` |
| `description` | entry-specific description |
| `balance_after_minor` | affected account balance after posting |
| `metadata` | hidden string/integer metadata object |

One own-transfer event therefore appears in two records with one shared event and transfer-group ID.
Card purchases have no deposit entry and appear in the separate card truth dataset.

### `private/credit_card_transaction_ground_truth.jsonl`

One record per accepted card purchase:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.1"` |
| `event_id` | hidden purchase event ID |
| `card_transaction_id` | observed purchase ID |
| `customer_id` | string |
| `card_id` | string |
| `occurred_at` | purchase date string |
| `economic_type` | `EXPENSE` |
| `amount_minor` | positive full purchase amount |
| `currency` | string |
| `source_entity` | card ID |
| `destination_entity` | merchant configured on purchase rule |
| `description` | purchase description |
| `installment_count` | positive integer |
| `outstanding_after_minor` | used limit on this card immediately after purchase |
| `metadata` | `expense_kind`, `purchase_id`, `rule_id`, and `installment_count` |

## Ordering and determinism

Dataset ordering is deterministic:

- accounts and cards by generated ID;
- balances and limits by month-end date, then product ID;
- deposit transactions by posting date, causal priority, event ID, then entry ID;
- observed card transactions by purchase date, then hidden event ID;
- invoices by close date, then card ID;
- invoice items by close date, card ID, then item ID;
- monthly truth chronologically.

Private card-transaction truth retains authorization order
`(purchase date, rule ID, occurrence index)` so `outstanding_after_minor` follows causal state.

Given identical validated configuration, seed, effective months, and version profile, financial
records, IDs, ordering, serialized JSONL, and hashes are reproducible across supported runtimes.

## Compatibility

- A `schema_version: "1.0"` configuration still selects simulator engine `0.1.0`, contract `1.0`,
  the original RNG behavior, and the original three observed plus three private datasets.
- A `schema_version: "1.1"` configuration selects engine `0.2.0`, contract `1.1`, and the expanded
  tree documented here.
- Loading or generating `1.0` does not inject `1.1` fields or empty card datasets. The committed
  seed-42 legacy golden output remains byte-for-byte stable.

## Current limitations

- One salaried customer and one currency per run.
- Active checking/savings accounts and active credit cards all open at simulation start.
- Fixed limits, zero opening card debt, full automatic invoice payment, and deterministic decline
  only.
- No interest, fees, revolving balance, minimum or partial payments, delinquency, failed autopay,
  refunds, chargebacks, reversals, rewards, cash advances, or foreign-currency card transactions.
- No loans, investments, variable income, employment changes, observation loss, duplicates, or
  provider-specific degradation.
- No taxes, inflation, available-funds rejection, overdraft policy, weekends, or holidays.
