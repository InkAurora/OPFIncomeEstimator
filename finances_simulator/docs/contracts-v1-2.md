# Finances Simulator Contract Schema 1.2

This document specifies configuration and output contract `1.2`, emitted by simulator engine
`0.3.0`. It retains schema `1.1` deposit-account and credit-card behavior, then adds deterministic
constant-principal loans, fixed-income investments, and monthly private balance sheets.

Contract `1.2` is project-owned and Open Finance-inspired. It is not an official Open Finance wire
schema. Contracts [`1.1`](contracts-v1-1.md) and [`1.0`](contracts-v1.md) remain frozen and supported
by their legacy engine paths.

## Common rules

- Dates use ISO `YYYY-MM-DD`; month keys use `YYYY-MM`.
- One three-letter uppercase currency applies to every product in a run.
- Fields ending in `_minor` use integer currency minor units. Positive and non-negative constraints
  appear below. Deposit balances, total assets, and net worth may be negative.
- Basis points use `10_000` as 100%.
- Rate calculations use integer half-up rounding. Binary floating point is never used.
- Configuration rejects unknown fields. Non-empty strings trim surrounding whitespace.
- Machine references and rule IDs match `[A-Za-z0-9][A-Za-z0-9_.-]*`.
- Every output record carries `schema_version: "1.2"`.
- Optional output fields serialize as JSON `null`.
- Private event IDs, economic classifications, source and destination entities, rule metadata,
  income-source IDs, causal links, and transfer groups never enter estimator-visible records.
- JSONL uses compact UTF-8 JSON, lexicographically sorted keys, and one trailing newline per record.

## Scenario configuration

Schema `1.2` contains exactly these top-level fields:

| Field | Type | Rule |
| --- | --- | --- |
| `schema_version` | string | Must equal `"1.2"`. |
| `scenario` | object | Timeline settings. |
| `customer` | object | Currency and primary-account reference. |
| `institutions` | array | Between 2 and 32; references must be unique. |
| `accounts` | array | Between 2 and 32 deposit accounts. |
| `salary` | object | Routed monthly salary rule. |
| `fixed_expenses` | array | Exactly five routed monthly expense rules. |
| `variable_expenses` | object | Routed random monthly expense rule. |
| `own_transfers` | array | Between 1 and 32 monthly own-account transfer rules. |
| `credit_cards` | array | Between 1 and 32 cards and payment policies. |
| `card_purchase_rules` | array | Between 1 and 256 deterministic purchase schedules. |
| `loans` | array | Between 1 and 32 deterministic loan contracts. |
| `investments` | array | Between 1 and 32 fixed-income investments. |
| `investment_contribution_rules` | array | Between 1 and 256 contribution schedules. |
| `investment_redemption_rules` | array | Between 1 and 256 redemption schedules. |

Schema `1.2` inherits all schema `1.1` validation. Across all loans, `term_months` may schedule at
most 10,000 installments. Across contribution and redemption rules, configured `occurrences` may
schedule at most 10,000 attempts.

### `scenario`

| Field | Type | Rule |
| --- | --- | --- |
| `name` | string | Non-empty scenario name. |
| `start_date` | date | First day of a month. |
| `default_months` | integer | Positive; effective CLI duration must be `1..1200`. |

### `customer`

| Field | Type | Rule |
| --- | --- | --- |
| `currency` | string | Exactly three uppercase ASCII letters. |
| `primary_account_ref` | string | Must reference a configured `CHECKING` account. |

### `institutions[]`

| Field | Type | Rule |
| --- | --- | --- |
| `institution_ref` | string | Unique configuration reference. |
| `institution_id` | string | Unique non-empty observed institution identifier. |
| `institution_name` | string | Non-empty observed name. |

Every account, card, loan, and investment institution reference must resolve.

### `accounts[]`

| Field | Type | Rule |
| --- | --- | --- |
| `account_ref` | string | Unique configuration reference. |
| `institution_ref` | string | Must reference `institutions[]`. |
| `account_label` | string | Non-empty observed label. |
| `account_type` | enum | `CHECKING` or `SAVINGS`. |
| `opening_balance_minor` | integer | Greater than or equal to zero. |

All accounts open on `scenario.start_date`. Later balances may be negative; no available-funds or
overdraft-limit check applies.

### `salary`

| Field | Type | Rule |
| --- | --- | --- |
| `amount_minor` | integer | Positive. |
| `day_of_month` | integer | `1..31`. |
| `payer` | string | Non-empty private source entity. |
| `description` | string | Non-empty observed transaction description. |
| `destination_account_ref` | string | Must reference an account. |

Salary creates one `INCOME` event and one deposit credit per simulated month.

### `fixed_expenses[]`

| Field | Type | Rule |
| --- | --- | --- |
| `rule_id` | string | Unique within `fixed_expenses`. |
| `category` | string | Non-empty private category. |
| `amount_minor` | integer | Positive. |
| `day_of_month` | integer | `1..31`. |
| `payee` | string | Non-empty private destination entity. |
| `description` | string | Non-empty observed description. |
| `source_account_ref` | string | Must reference an account. |

Each rule creates one `EXPENSE` event and one deposit debit per simulated month.

### `variable_expenses`

| Field | Type | Rule |
| --- | --- | --- |
| `count_min` | integer | `0..500`. |
| `count_max` | integer | Between `count_min` and 500. |
| `amount_min_minor` | integer | Positive. |
| `amount_max_minor` | integer | At least `amount_min_minor`. |
| `day_min` | integer | `1..28`. |
| `day_max` | integer | Between `day_min` and 28. |
| `merchants` | array | At least one merchant object. |
| `source_account_ref` | string | Must reference an account. |

Each merchant has exactly `entity` and `description`, both non-empty strings. Per month, count,
merchant, day, and integer amount are sampled uniformly over inclusive configured bounds using the
isolated `deposit-variable-expenses-v1` stream. Loan and investment activity consumes no RNG draws.

### `own_transfers[]`

| Field | Type | Rule |
| --- | --- | --- |
| `rule_id` | string | Unique within `own_transfers`. |
| `source_account_ref` | string | Must reference an account. |
| `destination_account_ref` | string | Must reference a different account. |
| `amount_minor` | integer | Positive. |
| `day_of_month` | integer | `1..31`. |
| `outgoing_description` | string | Non-empty source-account description. |
| `incoming_description` | string | Non-empty destination-account description. |

Each rule runs once per month. One `OWN_TRANSFER` event produces equal source debit and destination
credit entries linked by one private transfer group. It changes neither aggregate deposits nor true
income.

### `credit_cards[]`

| Field | Type | Rule |
| --- | --- | --- |
| `card_ref` | string | Unique card reference. |
| `institution_ref` | string | Must reference `institutions[]`. |
| `card_label` | string | Non-empty observed label. |
| `credit_limit_minor` | integer | Positive fixed total limit. |
| `statement_close_day` | integer | `1..31`. |
| `payment_due_day` | integer | `1..31`. |
| `payment_account_ref` | string | Must reference a deposit account. |
| `payment_description` | string | Non-empty observed deposit-transaction description. |
| `payment_policy` | enum | Must equal `FULL_AUTOPAY`. |
| `utilization_policy` | object | Authorization ceiling. |

`utilization_policy` contains `maximum_basis_points` (`1..10000`) and `on_exceed` (`DECLINE`).
Cards open on `scenario.start_date` with zero debt.

### `card_purchase_rules[]`

| Field | Type | Rule |
| --- | --- | --- |
| `rule_id` | string | Unique within `card_purchase_rules`. |
| `card_ref` | string | Must reference a card. |
| `merchant` | string | Non-empty private merchant. |
| `description` | string | Non-empty observed description. |
| `amount_minor` | integer | Positive and at least `installment_count`. |
| `day_of_month` | integer | `1..31`. |
| `start_month_index` | integer | Zero-based and non-negative. |
| `interval_months` | integer | Positive. |
| `occurrences` | integer | `1..1200`. |
| `installment_count` | integer | `1..120`. |

Across all card rules, at most 10,000 attempts and 250,000 installment items may be configured.
Attempts outside the effective run are ignored.

### `loans[]`

| Field | Type | Rule |
| --- | --- | --- |
| `loan_ref` | string | Unique loan reference. |
| `institution_ref` | string | Must reference `institutions[]`. |
| `loan_label` | string | Non-empty observed label. |
| `loan_type` | enum | Must equal `PERSONAL`. |
| `principal_minor` | integer | Positive and at least `term_months`. |
| `annual_interest_basis_points` | integer | `1..10000`. |
| `term_months` | integer | `1..480`. |
| `amortization_system` | enum | Must equal `CONSTANT_PRINCIPAL`. |
| `disbursement_account_ref` | string | Must reference an account. |
| `disbursement_month_index` | integer | Zero-based `0..1199`. |
| `disbursement_day_of_month` | integer | `1..31`. |
| `payment_account_ref` | string | Must reference an account. |
| `payment_day_of_month` | integer | `1..31`. |
| `disbursement_description` | string | Non-empty observed deposit-credit description. |
| `payment_description` | string | Non-empty observed payment-debit description. |
| `payment_policy` | enum | Must equal `FULL_AUTOPAY`. |

Loan origination is the configured day in `start_date + disbursement_month_index` calendar months.
The date clamps to month end. A loan originating after simulation end remains outside world state
and creates no loan, event, ledger entry, payment, balance, truth, or observed record for that run.
An in-window origination creates one `LOAN_DISBURSEMENT` event and a principal-sized deposit credit.
It is a liability-producing cash inflow, not income.

For principal `P` and term `N`:

```text
base, remainder = divmod(P, N)
principal_i = base + 1  when i <= remainder
principal_i = base      otherwise
```

Installments use one-based `i`; early components absorb indivisible minor units. Each installment's
interest uses opening principal and nominal annual basis points:

```text
interest_i = round_half_up(opening_principal_i * annual_interest_basis_points / 120000)
payment_i = principal_i + interest_i
remaining_i = opening_principal_i - principal_i
```

For non-negative integers, half-up is equivalent to
`floor((numerator + denominator / 2) / denominator)`. First payment is scheduled in the calendar
month after origination; subsequent payments use consecutive months. `payment_day_of_month` clamps
to month end. Every due date inside the simulation window is paid in full on its due date, creating
one `LOAN_PAYMENT` event and one deposit debit. Future schedule entries remain hidden from outputs.
Insufficient deposit funds do not block payment.

Only principal is loan liability. Interest paid is recorded separately in monthly private truth;
principal payment is neither income nor expense.

### `investments[]`

| Field | Type | Rule |
| --- | --- | --- |
| `investment_ref` | string | Unique investment reference. |
| `institution_ref` | string | Must reference `institutions[]`. |
| `investment_label` | string | Non-empty observed label. |
| `investment_type` | enum | Must equal `FIXED_INCOME`. |
| `opening_balance_minor` | integer | Greater than or equal to zero. |
| `monthly_return_basis_points` | integer | `0..10000`. |
| `return_description` | string | Non-empty observed return description. |

Every investment opens on `scenario.start_date`. Opening investment value is an asset already held
before first simulated event; it does not create a deposit posting.

### `investment_contribution_rules[]` and `investment_redemption_rules[]`

Both arrays use the same fields:

| Field | Type | Rule |
| --- | --- | --- |
| `rule_id` | string | Unique across both investment-flow arrays. |
| `investment_ref` | string | Must reference `investments[]`. |
| `account_ref` | string | Must reference a deposit account. |
| `amount_minor` | integer | Positive. |
| `day_of_month` | integer | `1..31`. |
| `start_month_index` | integer | Zero-based `0..1199`. |
| `interval_months` | integer | `1..1200`. |
| `occurrences` | integer | `1..1200`. |
| `description` | string | Non-empty observed description. |

Attempt `i` uses month index `start_month_index + i * interval_months`; attempts outside the
effective run are ignored. Same-date contributions execute before redemptions. Within each type,
rules execute by `(rule_id, occurrence_index)`.

A contribution debits its account and increases investment balance by the same amount. A redemption
decreases investment balance and credits its account. A redemption exceeding current investment
balance is declined silently: no event, movement, deposit entry, or decline record is emitted.
Neither flow is income or expense.

After every month's dated flows, return is credited on calendar month end:

```text
return_minor = round_half_up(balance_after_flows * monthly_return_basis_points / 10000)
closing_investment_balance = balance_after_flows + return_minor
```

A positive return creates an `INVESTMENT_RETURN` event and investment transaction but no deposit
entry. A rounded zero creates neither event nor transaction. Every investment still receives one
month-end balance snapshot.

## Posting, cards, and accounting

Configured days beyond month end clamp to its last date. Weekends and holidays cause no shift.
Deposit effects use deterministic `(posted_at, posting_priority, event_id, entry_id)` order. Same-day
priorities are:

1. salary income;
2. loan disbursement;
3. own transfer;
4. investment contribution;
5. investment redemption;
6. card payment;
7. loan payment; and
8. deposit expense.

Each deposit account reconciles independently:

```text
closing_balance = opening_balance + credits - debits
```

Schema `1.1` card authorization, statement, installment, and payment semantics remain unchanged:

- maximum used limit is `floor(credit_limit_minor * maximum_basis_points / 10000)`;
- a purchase reserves its full amount immediately and is declined when it would exceed that value;
- purchase date on or before statement close enters that statement;
- purchase installments split exactly with remainder assigned to earliest installments;
- invoice due date is the first configured due date strictly after statement close;
- full invoice payment releases due installment amounts and creates one deposit debit; and
- purchase is the expense; invoice payment is only settlement.

## Output directory and manifest

Schema `1.2` emits:

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
|   |-- credit_card_invoice_items.jsonl
|   |-- loans.jsonl
|   |-- loan_payments.jsonl
|   |-- loan_balances.jsonl
|   |-- investments.jsonl
|   |-- investment_transactions.jsonl
|   `-- investment_balances.jsonl
`-- private/
    |-- customer_ground_truth.jsonl
    |-- customer_month_ground_truth.jsonl
    |-- transaction_ground_truth.jsonl
    |-- credit_card_transaction_ground_truth.jsonl
    |-- loan_payment_ground_truth.jsonl
    |-- investment_transaction_ground_truth.jsonl
    `-- balance_sheet_ground_truth.jsonl
```

Generation accepts a nonexistent path or existing empty directory, refuses a non-empty target,
stages the complete tree in a temporary sibling, and publishes with one rename.

`run_manifest.json` contains:

| Field | Type or value |
| --- | --- |
| `config_sha256` | SHA-256 of compact, key-sorted canonical validated configuration JSON. |
| `contract_schema_version` | `"1.2"`. |
| `datasets` | `observed` and `private` metadata maps matching the tree above. |
| `months` | Effective simulated month count. |
| `rng_algorithm` | `"sha256-counter-v1"`. |
| `run_id` | Deterministic ID derived from configuration hash, seed, months, and engine version. |
| `scenario_name` | Configured scenario name. |
| `seed` | CLI seed. |
| `simulation_window` | Exact `start_date` and `end_date`. |
| `simulator_version` | `"0.3.0"`. |

Each dataset metadata object contains `path`, `record_count`, `schema_version`, and SHA-256 of exact
JSONL bytes.

## Observed datasets

These records form the estimator-visible boundary. Product-native classifications such as loan
terms and investment transaction type are observable. Hidden economic classification, source and
destination entities, rule IDs, occurrence indexes, and event IDs remain private.

### `observed/accounts.jsonl`

One record per deposit account:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
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

One record per account and simulated month:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
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
| `schema_version` | `"1.2"` |
| `transaction_id` | ledger-entry ID |
| `customer_id` | string |
| `account_id` | string |
| `posted_at` | date string |
| `direction` | `CREDIT` or `DEBIT` |
| `amount_minor` | positive integer |
| `currency` | string |
| `description` | string |
| `balance_after_minor` | integer; may be negative |

Salary, deposit expenses, own-transfer legs, card payments, loan disbursements and payments, and
accepted investment contribution/redemption account effects appear here. Card purchases and
investment returns do not post to this ledger.

### `observed/credit_cards.jsonl`

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `customer_id` | string |
| `card_id` | string |
| `institution_id` | string |
| `institution_name` | string |
| `card_label` | string |
| `currency` | string |
| `opened_on` | date string |
| `status` | `ACTIVE` |

### `observed/credit_limits.jsonl`

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `credit_limit_id` | string |
| `customer_id` | string |
| `card_id` | string |
| `reference_date` | month-end date string |
| `total_limit_minor` | positive integer |
| `used_limit_minor` | non-negative integer |
| `available_limit_minor` | non-negative integer |
| `currency` | string |

`total_limit_minor = used_limit_minor + available_limit_minor`.

### `observed/credit_card_transactions.jsonl`

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
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

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `invoice_id` | string |
| `customer_id` | string |
| `card_id` | string |
| `statement_close_date` | date string |
| `due_date` | first configured due date after close |
| `amount_due_minor` | positive item sum |
| `paid_amount_minor` | amount due for `PAID`; zero for `CLOSED` |
| `currency` | string |
| `status` | `PAID` or `CLOSED` |
| `paid_at` | due-date string for `PAID`; otherwise `null` |
| `payment_transaction_id` | linked deposit transaction for `PAID`; otherwise `null` |

### `observed/credit_card_invoice_items.jsonl`

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `invoice_item_id` | string |
| `customer_id` | string |
| `card_id` | string |
| `invoice_id` | linked invoice ID |
| `card_transaction_id` | linked purchase ID |
| `installment_number` | one-based positive integer |
| `installment_count` | positive integer |
| `amount_minor` | positive installment amount |
| `currency` | string |
| `description` | purchase description |

### `observed/loans.jsonl`

One record per configured originated loan:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `loan_id` | string |
| `customer_id` | string |
| `institution_id` | string |
| `institution_name` | string |
| `loan_label` | string |
| `loan_type` | `PERSONAL` |
| `currency` | string |
| `originated_at` | date string |
| `original_principal_minor` | positive integer, at least `term_months` |
| `annual_interest_basis_points` | `1..10000` |
| `term_months` | `1..480` |
| `amortization_system` | `CONSTANT_PRINCIPAL` |
| `status` | `ACTIVE` or `PAID_OFF` at run end |
| `disbursement_transaction_id` | linked deposit-credit transaction ID |

### `observed/loan_payments.jsonl`

One record per fully paid installment due inside the run:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `loan_payment_id` | string |
| `customer_id` | string |
| `loan_id` | linked loan ID |
| `installment_number` | one-based positive integer |
| `installment_count` | contractual term count |
| `due_date` | date string |
| `principal_amount_minor` | positive integer |
| `interest_amount_minor` | non-negative integer |
| `total_amount_minor` | principal plus interest |
| `paid_amount_minor` | equals total amount |
| `remaining_principal_after_minor` | non-negative integer |
| `currency` | string |
| `status` | `PAID` |
| `paid_at` | equals due date |
| `payment_transaction_id` | linked deposit-debit transaction ID |

### `observed/loan_balances.jsonl`

One record per loan month-end from origination month through run end:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `loan_balance_id` | string |
| `customer_id` | string |
| `loan_id` | linked loan ID |
| `reference_date` | month-end date string |
| `remaining_principal_minor` | non-negative integer |
| `currency` | string |
| `balance_type` | `CLOSING` |

### `observed/investments.jsonl`

One record per investment:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `investment_id` | string |
| `customer_id` | string |
| `institution_id` | string |
| `institution_name` | string |
| `investment_label` | string |
| `investment_type` | `FIXED_INCOME` |
| `currency` | string |
| `opened_on` | `scenario.start_date` |
| `status` | `ACTIVE` |

### `observed/investment_transactions.jsonl`

One record per accepted flow or positive monthly return:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `investment_transaction_id` | string |
| `customer_id` | string |
| `investment_id` | linked investment ID |
| `occurred_at` | date string |
| `transaction_type` | `CONTRIBUTION`, `REDEMPTION`, or `RETURN` |
| `amount_minor` | positive integer |
| `currency` | string |
| `description` | configured flow or return description |
| `balance_after_minor` | non-negative investment balance after movement |
| `related_account_transaction_id` | linked deposit transaction for external flow; `null` for return |

### `observed/investment_balances.jsonl`

One record per investment and simulated month:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `investment_balance_id` | string |
| `customer_id` | string |
| `investment_id` | linked investment ID |
| `reference_date` | month-end date string |
| `balance_minor` | non-negative integer |
| `currency` | string |
| `balance_type` | `CLOSING` |

## Private datasets

Private records support reconciliation and evaluation. They must never become estimator features.

### `private/customer_ground_truth.jsonl`

One customer record:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `customer_id` | string |
| `scenario_name` | string |
| `employment_status` | `SALARIED` |
| `currency` | string |
| `true_monthly_salary_minor` | positive integer |
| `income_source_id` | hidden salary-source ID |
| `primary_account_id` | primary deposit-account ID |
| `opening_balance_minor` | primary-account opening balance |
| `account_ids` | ordered array, primary first |
| `card_ids` | ordered array of card IDs |
| `total_opening_deposit_balance_minor` | sum of account opening balances |
| `loan_ids` | ordered array of loan IDs |
| `investment_ids` | ordered array of investment IDs |
| `total_opening_investment_balance_minor` | sum of investment opening balances |
| `total_opening_loan_principal_minor` | loan principal outstanding before run start |

### `private/customer_month_ground_truth.jsonl`

One chronological record per simulated month:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `customer_id` | string |
| `month` | `YYYY-MM` |
| `currency` | string |
| `true_income_minor` | sum of `INCOME` events only |
| `true_expenses_minor` | sum of `EXPENSE` events, including full card purchases |
| `income_event_count` | count of `INCOME` events |
| `expense_event_count` | count of `EXPENSE` events |
| `opening_balance_minor` | primary-account month opening balance |
| `closing_balance_minor` | primary-account month closing balance |
| `total_deposit_opening_balance_minor` | aggregate deposit balance at month opening |
| `total_deposit_closing_balance_minor` | aggregate deposit balance at month end |
| `total_card_outstanding_opening_minor` | aggregate used card limit at month opening |
| `total_card_outstanding_closing_minor` | aggregate used card limit at month end |
| `loan_interest_paid_minor` | interest component of loan payments made that month |
| `investment_return_minor` | positive investment returns credited that month |

Loan disbursement, loan payment, investment contribution, investment redemption, investment return,
own transfer, and card payment do not enter `true_income_minor` or `true_expenses_minor`.

### `private/transaction_ground_truth.jsonl`

One record per deposit ledger entry:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `event_id` | hidden financial-event ID |
| `entry_id` | deposit ledger-entry ID |
| `customer_id` | string |
| `account_id` | affected account ID |
| `occurred_at` | posting date string |
| `economic_type` | private `EconomicType` value |
| `direction` | `CREDIT` or `DEBIT` |
| `amount_minor` | positive integer |
| `currency` | string |
| `source_entity` | hidden event source |
| `destination_entity` | hidden event destination |
| `income_source_id` | hidden source ID or `null` |
| `caused_by_event_id` | hidden causal event ID or `null` |
| `transfer_group_id` | own-transfer group ID or `null` |
| `description` | entry-specific description |
| `balance_after_minor` | affected account balance after posting |
| `metadata` | hidden string/integer metadata object |

Schema `1.2` deposit truth may contain `INCOME`, `EXPENSE`, `OWN_TRANSFER`, `CARD_PAYMENT`,
`LOAN_DISBURSEMENT`, `LOAN_PAYMENT`, `INVESTMENT_CONTRIBUTION`, and `INVESTMENT_REDEMPTION`.

### `private/credit_card_transaction_ground_truth.jsonl`

One record per accepted card purchase:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `event_id` | hidden purchase-event ID |
| `card_transaction_id` | observed purchase ID |
| `customer_id` | string |
| `card_id` | linked card ID |
| `occurred_at` | purchase date string |
| `economic_type` | `EXPENSE` |
| `amount_minor` | positive full purchase amount |
| `currency` | string |
| `source_entity` | card ID |
| `destination_entity` | private merchant |
| `description` | purchase description |
| `installment_count` | positive integer |
| `outstanding_after_minor` | used card limit immediately after authorization |
| `metadata` | hidden purchase and rule metadata |

### `private/loan_payment_ground_truth.jsonl`

One record per paid loan installment:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `event_id` | hidden loan-payment event ID |
| `loan_payment_id` | observed payment ID |
| `customer_id` | string |
| `loan_id` | linked loan ID |
| `occurred_at` | due/payment date string |
| `economic_type` | `LOAN_PAYMENT` |
| `installment_number` | one-based positive integer |
| `installment_count` | contractual term count |
| `opening_principal_minor` | principal before payment |
| `principal_amount_minor` | positive principal component |
| `interest_amount_minor` | non-negative interest component |
| `total_amount_minor` | principal plus interest |
| `remaining_principal_after_minor` | principal after payment |
| `currency` | string |
| `source_entity` | payment account ID |
| `destination_entity` | loan ID |
| `description` | configured payment description |
| `metadata` | hidden loan, installment, principal, and interest metadata |

`opening_principal_minor = principal_amount_minor + remaining_principal_after_minor` and
`total_amount_minor = principal_amount_minor + interest_amount_minor`.

### `private/investment_transaction_ground_truth.jsonl`

One record per accepted investment flow or positive return:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `event_id` | hidden investment-event ID |
| `investment_transaction_id` | observed movement ID |
| `customer_id` | string |
| `investment_id` | linked investment ID |
| `occurred_at` | movement date string |
| `economic_type` | matching investment economic type |
| `transaction_type` | `CONTRIBUTION`, `REDEMPTION`, or `RETURN` |
| `amount_minor` | positive integer |
| `currency` | string |
| `source_entity` | hidden source entity |
| `destination_entity` | hidden destination entity |
| `description` | configured flow or return description |
| `balance_after_minor` | non-negative balance after movement |
| `rule_id` | schedule rule ID for external flow; `null` for return |
| `occurrence_index` | zero-based schedule occurrence for external flow; `null` for return |
| `metadata` | hidden movement metadata |

Economic types map exactly: contribution to `INVESTMENT_CONTRIBUTION`, redemption to
`INVESTMENT_REDEMPTION`, and return to `INVESTMENT_RETURN`.

### `private/balance_sheet_ground_truth.jsonl`

One chronological record per simulated month. Unsuffixed totals are closing/reference-date values:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.2"` |
| `balance_sheet_id` | deterministic ID |
| `customer_id` | string |
| `month` | `YYYY-MM` |
| `reference_date` | calendar month-end date |
| `currency` | string |
| `opening_total_deposit_balance_minor` | aggregate deposit opening balance |
| `opening_total_investment_balance_minor` | aggregate investment opening value |
| `opening_total_assets_minor` | opening deposits plus investments |
| `opening_total_card_outstanding_minor` | aggregate opening used card limit |
| `opening_total_loan_principal_minor` | aggregate opening loan principal |
| `opening_total_liabilities_minor` | opening card debt plus loan principal |
| `opening_net_worth_minor` | opening assets minus liabilities |
| `total_deposit_balance_minor` | aggregate closing deposit balance |
| `total_investment_balance_minor` | aggregate closing investment value |
| `total_assets_minor` | closing deposits plus investments |
| `total_card_outstanding_minor` | aggregate closing used card limit |
| `total_loan_principal_minor` | aggregate closing loan principal |
| `total_liabilities_minor` | closing card debt plus loan principal |
| `net_worth_minor` | closing assets minus liabilities |

Required identities:

```text
opening_total_assets = opening_deposits + opening_investments
opening_total_liabilities = opening_card_outstanding + opening_loan_principal
opening_net_worth = opening_total_assets - opening_total_liabilities

total_assets = closing_deposits + closing_investments
total_liabilities = closing_card_outstanding + closing_loan_principal
net_worth = total_assets - total_liabilities
```

Deposit balances enter assets algebraically, including negative balances. No separate overdraft
liability is created in schema `1.2`.

## Ordering and determinism

Dataset ordering is deterministic:

- accounts, cards, loans, and investments by generated product ID;
- deposit balances, card limits, loan balances, and investment balances by reference date, then
  product ID;
- deposit transactions by posting date, causal priority, event ID, then entry ID;
- observed card transactions by purchase date, then hidden event ID;
- private card truth by authorization order;
- invoices by close date, then card ID;
- invoice items by close date, card ID, then item ID;
- loan payments by due date, loan ID, then installment number;
- investment movements in causal simulation order: dated contributions, dated redemptions, then
  month-end returns;
- customer-month truth and balance sheets chronologically.

Identifiers use the schema `1.2` engine namespace derived from canonical configuration hash, seed,
and simulator version. Product IDs, event IDs, ledger IDs, payment IDs, movement IDs, snapshot IDs,
and balance-sheet IDs are deterministic. Loans and investments consume no RNG draws, so adding their
rules cannot perturb the isolated variable-expense stream.

Given identical validated configuration, seed, effective months, and version profile, hidden state,
records, ordering, serialized JSONL, and hashes are reproducible across supported runtimes.

## Compatibility

- `schema_version: "1.0"` selects engine `0.1.0`, schema `1.0`, its original RNG behavior, and its
  original three observed plus three private datasets.
- `schema_version: "1.1"` selects engine `0.2.0`, schema `1.1`, and its card-expanded tree.
- `schema_version: "1.2"` selects engine `0.3.0`, schema `1.2`, and the tree documented here.
- Older paths do not receive new fields or empty loan/investment datasets. Their committed seed-42
  outputs remain byte-for-byte stable.

## Current limitations

- One salaried customer and one currency per run.
- Active checking/savings accounts, cards, and investments open at simulation start.
- Cards retain fixed limits, deterministic decline, zero opening debt, and full automatic payment.
- Loans support only personal, constant-principal amortization, fixed nominal annual rate, one
  disbursement, and full automatic payment. Future-originating configured loans remain omitted until
  a run reaches their origination date.
- No loan fees, insurance, grace period, prepayment, refinancing, missed or partial payment,
  delinquency, default, penalty, or variable/indexed rate.
- Investments support one money-valued fixed-income balance, deterministic non-negative monthly
  returns, scheduled flows, and silent over-redemption decline.
- No units, prices, benchmarks, taxes, fees, negative returns, settlement lag, maturity, liquidity
  lock, dividends, coupons, or market calendar.
- No variable income, population generation, life events, observation degradation, taxes on other
  activity, overdraft policy, weekends, holidays, or inflation.
