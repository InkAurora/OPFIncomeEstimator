# Finances Simulator Contract Schema 1.3

This document specifies configuration and output contract `1.3`, emitted by simulator engine
`0.4.0`. It retains schema `1.2` account, card, loan, investment, and balance-sheet behavior, then
adds deterministic income diversity and a configurable customer population factory.

Contract `1.3` is project-owned and Open Finance-inspired. It is not an official Open Finance wire
schema. Contracts [`1.2`](contracts-v1-2.md), [`1.1`](contracts-v1-1.md), and
[`1.0`](contracts-v1.md) remain frozen and supported by their legacy engine paths.

## Common rules

- Dates use ISO `YYYY-MM-DD`; month keys use `YYYY-MM`.
- One three-letter uppercase currency applies to every product and income source in a run.
- Fields ending in `_minor` use integer currency minor units. Binary floating point is never used.
- Basis points use `10_000` as 100%.
- Non-negative ratios use integer half-up rounding.
- Configuration rejects unknown fields. Non-empty strings trim surrounding whitespace.
- Machine references match `[A-Za-z0-9][A-Za-z0-9_.-]*`.
- Every output record carries `schema_version: "1.3"`.
- JSONL uses compact UTF-8 JSON, lexicographically sorted keys, and one trailing newline per record.
- Income profile, income kind, source identity, source parameters, behavior, wealth, economic type,
  and causal metadata are private. They never enter estimator-visible records.

## Scenario configuration

Schema `1.3` contains exactly these top-level fields:

| Field | Type | Rule |
| --- | --- | --- |
| `schema_version` | string | Must equal `"1.3"`. |
| `scenario` | object | Same timeline settings as schema `1.2`. |
| `customer` | object | Currency and primary checking-account reference. |
| `customer_factory` | object | Conditional income distribution plus independent behavior and wealth distributions. |
| `institutions` | array | `1..32`; references and observed IDs must be unique. |
| `accounts` | array | `1..32`; one must be the configured primary `CHECKING` account. |
| `fixed_expenses` | array | `0..64`; omitted value defaults to an empty array. |
| `variable_expenses` | object | Required routed random monthly expense rule. |
| `own_transfers` | array | `0..32`; omitted value defaults to an empty array. |
| `credit_cards` | array | `0..32`; omitted value defaults to an empty array. |
| `card_purchase_rules` | array | `0..256`; omitted value defaults to an empty array. |
| `loans` | array | `0..32`; omitted value defaults to an empty array. |
| `investments` | array | `0..32`; omitted value defaults to an empty array. |
| `investment_contribution_rules` | array | `0..256`; omitted value defaults to an empty array. |
| `investment_redemption_rules` | array | `0..256`; omitted value defaults to an empty array. |

Schema `1.3` has no top-level `salary` field. Every true-income source comes from the selected
factory source bundle.

Financial-product and expense objects retain the exact schema `1.2` fields and semantics. All
account, institution, card, loan, investment, and rule references must resolve. Rule and product
references must be unique within the same scopes documented for schema `1.2`. Work caps remain:

- at most 10,000 configured card-purchase attempts;
- at most 250,000 card installment items;
- at most 10,000 loan installments; and
- at most 10,000 investment contribution and redemption attempts.

Unlike schema `1.2`, these product arrays may be empty. Empty product datasets are still emitted.
`scenario.start_date` must be the first day of a month, and the effective run length remains
`1..1200` months. Configured days beyond month end clamp to the final calendar day. Weekends and
holidays cause no shift.

## Customer factory

`customer_factory` contains three weighted axes:

| Field | Cardinality | Meaning |
| --- | --- | --- |
| `income_profiles` | `1..7` | Income-profile marginal and source-bundle distribution conditional on the chosen profile. |
| `behavior_profiles` | `1..3` | Independent spending and saving dimension. |
| `wealth_profiles` | `1..3` | Independent opening deposit and investment wealth dimension. |

Each array may contain a subset of supported enum values, but a value may occur at most once. Each
axis uses positive `weight_basis_points`, and weights in that axis must sum exactly to `10_000`.
Behavior and wealth draws are neither conditioned on the income profile nor on each other. This
permits controlled unusual combinations such as a high-wealth unemployed customer or a low-wealth
investor.

### `income_profiles[]`

| Field | Type | Rule |
| --- | --- | --- |
| `income_profile` | enum | `SALARIED`, `SELF_EMPLOYED`, `BUSINESS_OWNER`, `RETIRED`, `INVESTOR`, `MIXED`, or `UNEMPLOYED`. |
| `weight_basis_points` | integer | `1..10000`; profile-axis weights sum to `10000`. |
| `source_bundles` | array | `1..16` weighted bundles conditional on this profile. |

Each source bundle contains:

| Field | Type | Rule |
| --- | --- | --- |
| `source_bundle_ref` | string | Unique across the entire `customer_factory`. |
| `weight_basis_points` | integer | `1..10000`; bundle weights within one profile sum to `10000`. |
| `sources` | array | `0..8` source templates; `source_ref` is unique within the bundle. |

Bundle contents must match their archetype:

| Income profile | Required bundle content |
| --- | --- |
| `SALARIED` | At least one `SALARY` source. |
| `SELF_EMPLOYED` | At least one `SELF_EMPLOYMENT` source. |
| `BUSINESS_OWNER` | At least one `BUSINESS_PROFIT` source. |
| `RETIRED` | At least one `PENSION` source. |
| `INVESTOR` | At least one `INVESTMENT_DISTRIBUTION` source. |
| `MIXED` | At least two distinct income kinds. |
| `UNEMPLOYED` | No sources. |

Additional source kinds may appear beside a required kind. `INVESTMENT_DISTRIBUTION` means an
external cash distribution that is true income. It is distinct from an investment redemption and
from the schema `1.2` fixed-income product's internal `INVESTMENT_RETURN`; neither of those is true
income.

### `sources[]`

| Field | Type | Rule |
| --- | --- | --- |
| `source_ref` | string | Unique within its selected bundle. |
| `income_kind` | enum | `SALARY`, `SELF_EMPLOYMENT`, `BUSINESS_PROFIT`, `PENSION`, `INVESTMENT_DISTRIBUTION`, or `OTHER`. |
| `payer` | string | Non-empty private source entity. |
| `description` | string | Non-empty estimator-visible deposit description. |
| `destination_account_ref` | string | Must reference a configured deposit account. |
| `amount_distribution` | object | Uniform stepped distribution sampled once for this customer and source. |
| `day_of_month` | integer | `1..31`; clamps to calendar month end. |
| `frequency` | enum | `MONTHLY`, `EVERY_TWO_MONTHS`, `QUARTERLY`, `SEMIANNUALLY`, or `ANNUALLY`. |
| `start_month_index` | integer | Zero-based `0..1199`. |
| `occurrences` | integer | `1..1200` scheduled attempts, not guaranteed successful payments. |
| `payment_probability_basis_points` | integer | `0..10000`; chance that each scheduled attempt pays. |
| `volatility_basis_points` | integer | `0..10000`; symmetric per-attempt amount shock. |
| `seasonality_basis_points` | array | Exactly 12 integers in `0..20000`, January through December. |

`amount_distribution` contains:

| Field | Type | Rule |
| --- | --- | --- |
| `minimum_minor` | integer | `1..1000000000000`. |
| `maximum_minor` | integer | Between `minimum_minor` and `1000000000000`. |
| `step_minor` | integer | `1..1000000000000`; must divide `maximum_minor - minimum_minor`. |

The source's `base_amount_minor` is sampled uniformly from the inclusive grid:

```text
minimum_minor,
minimum_minor + step_minor,
...,
maximum_minor
```

It is sampled once when the factory member is created, not once per payment.

### `behavior_profiles[]`

| Field | Type | Rule |
| --- | --- | --- |
| `behavior_profile` | enum | `LOW_SPENDING`, `BALANCED`, or `HIGH_SPENDING`. |
| `weight_basis_points` | integer | `1..10000`; behavior-axis weights sum to `10000`. |
| `spending_multiplier_basis_points` | integer | `0..100000`. |
| `saving_multiplier_basis_points` | integer | `0..100000`. |

Names are configured labels; multipliers define behavior. The simulator does not infer behavior
from income profile or wealth.

### `wealth_profiles[]`

| Field | Type | Rule |
| --- | --- | --- |
| `wealth_band` | enum | `LOW`, `MIDDLE`, or `HIGH`. |
| `weight_basis_points` | integer | `1..10000`; wealth-axis weights sum to `10000`. |
| `deposit_balance_multiplier_basis_points` | integer | `0..100000`. |
| `investment_balance_multiplier_basis_points` | integer | `0..100000`. |

Names are configured labels; multipliers define opening financial wealth. Wealth remains
independent of income profile and behavior.

## Deterministic sampling and addressability

The CLI generates factory member index `0`. The public `CustomerFactory` also supports addressable
members with indexes `0..999999`. `sample(count)` is an in-memory helper capped at 100,000 members;
it is not a batch financial-history writer.

For each member, weighted choices use this exact rule:

1. sort choices by semantic key: enum value for profile axes, or reference for bundles;
2. draw an integer ticket uniformly from `0..9999`;
3. traverse cumulative weights and select the first item whose cumulative upper bound is strictly
   greater than the ticket.

Sources in a selected bundle are materialized in `source_ref` order. Each base amount is selected
uniformly from its configured stepped grid.

Factory randomness uses independent SHA-256 counter streams derived from seed, customer index, and
dimension label. Income profile, conditional bundle, behavior, wealth, and each source's base amount
use separate streams. Each scheduled source occurrence then uses separate payment and volatility
streams keyed by customer index, bundle reference, source reference, and occurrence index.

Consequences:

- sampling a member directly yields the same member as sampling a prefix containing that index;
- adding or reordering an unrelated source does not consume another source's draws;
- reordering weighted configuration entries does not change the selected semantic values; and
- changing income-profile configuration cannot consume behavior or wealth draws.

The factory's statistical population approaches configured marginals over many addressable indexes.
It does not force exact quotas in a finite sample because exact quotas would make a member depend on
batch size or partitioning.

## Behavior and wealth scaling

For non-negative integers, all scaling uses one half-up operation:

```text
scaled_minor = round_half_up(configured_minor * multiplier_basis_points / 10000)
```

The selected multipliers apply as follows:

| Multiplier | Applied to |
| --- | --- |
| `spending_multiplier_basis_points` | Fixed-expense amounts, variable-expense amount bounds, and card-purchase amounts. |
| `saving_multiplier_basis_points` | Own-account transfer amounts and investment-contribution amounts. |
| `deposit_balance_multiplier_basis_points` | Every deposit account's opening balance. |
| `investment_balance_multiplier_basis_points` | Every investment's opening balance. |

A fixed expense, own transfer, card purchase, or contribution whose scaled amount rounds to zero is
omitted. A positive scaled card purchase is clamped to at least its installment count so exact
minor-unit installment splitting remains valid. When spending multiplier is zero, variable-expense
counts become zero. Otherwise, each scaled variable-expense bound is clamped to at least one minor
unit.

Scaling does not change income base amounts, expense occurrence counts except for the zero-spending
case, credit limits, loan principals or payments, investment redemptions, or investment return
rates. It changes hidden financial state, not the canonical configuration hash. The original
validated configuration and seed remain the run identity inputs.

## Income schedule and amount realization

Frequency maps to an interval in calendar months:

| Frequency | Interval |
| --- | --- |
| `MONTHLY` | 1 month |
| `EVERY_TWO_MONTHS` | 2 months |
| `QUARTERLY` | 3 months |
| `SEMIANNUALLY` | 6 months |
| `ANNUALLY` | 12 months |

For zero-based occurrence `i`:

```text
month_index_i = start_month_index + i * interval_months
```

Attempts outside the effective run are ignored. For an in-window attempt, a uniform integer ticket
from `0..9999` succeeds exactly when:

```text
ticket < payment_probability_basis_points
```

On success, the simulator samples an integer volatility shock uniformly and inclusively:

```text
shock_basis_points in [-volatility_basis_points, +volatility_basis_points]
volatility_factor_basis_points = 10000 + shock_basis_points
```

The calendar month selects one of the 12 seasonality values. Realized payment uses one combined
half-up rounding step with no intermediate rounding:

```text
realized_amount_minor = round_half_up(
    base_amount_minor
    * seasonality_basis_points[calendar_month - 1]
    * volatility_factor_basis_points
    / 100000000
)
```

A failed probability attempt or a successful attempt that rounds to zero creates no event, ledger
entry, observed record, or transaction truth. A positive realization creates one private `INCOME`
event and one deposit credit routed to the configured account. Its hidden `income_source_id` links
the event and private transaction truth to the realized source.

`customer_month_ground_truth.true_income_minor` is the sum of positive `INCOME` events in that
month. Multiple sources may contribute. Unemployed customers, probability misses, zero seasonality,
and maximum downward volatility can produce zero-income months. Own transfers, loans, investment
redemptions, investment returns, and all settlement activity remain excluded.

## Posting and accounting

Schema `1.2` card, loan, investment, and accounting semantics remain unchanged. Deposit effects use
deterministic `(posted_at, posting_priority, event_id, entry_id)` order. Diverse income credits use
the existing income priority before loan disbursements, transfers, investment flows, settlements,
and expenses on the same day.

Every deposit account, card invoice, loan schedule, investment balance, and monthly private balance
sheet must reconcile. Behavioral and wealth scaling occurs before product simulation. Adding income
credits requires one final deposit-ledger posting pass, so observed balances include every selected
source realization.

## Output directory and manifest

Schema `1.3` emits:

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
    |-- income_source_ground_truth.jsonl
    |-- transaction_ground_truth.jsonl
    |-- credit_card_transaction_ground_truth.jsonl
    |-- loan_payment_ground_truth.jsonl
    |-- investment_transaction_ground_truth.jsonl
    `-- balance_sheet_ground_truth.jsonl
```

Observed and common private record fields are identical to schema `1.2`, except every record carries
`schema_version: "1.3"`. The V3 writer emits every listed file, including empty product or income
source datasets.

`run_manifest.json` retains the schema `1.2` structure with these version values:

| Field | Value |
| --- | --- |
| `contract_schema_version` | `"1.3"` |
| `simulator_version` | `"0.4.0"` |
| `rng_algorithm` | `"sha256-counter-v1"` |

`config_sha256` hashes compact, key-sorted canonical validated configuration JSON. `run_id` and the
V3 deterministic identifier namespace derive from that hash, seed, effective months where
applicable, and engine version. Dataset metadata retains exact path, record count, schema version,
and byte SHA-256.

## Observed datasets and trust boundary

All 14 observed datasets retain the exact schema `1.2` fields:

- `accounts`, `balances`, and `transactions`;
- `credit_cards`, `credit_limits`, `credit_card_transactions`, `credit_card_invoices`, and
  `credit_card_invoice_items`;
- `loans`, `loan_payments`, and `loan_balances`; and
- `investments`, `investment_transactions`, and `investment_balances`.

Income realizations appear only as ordinary deposit credits with date, direction, amount, currency,
description, and resulting account balance. Observed records do not include `income_profile`,
`income_kind`, `income_source_id`, source or bundle reference, behavioral profile, wealth band,
multipliers, probability, volatility, seasonality, factory weights, economic type, event ID, or
private source/destination entities.

Descriptions are configured observable strings. They may resemble provider transaction text, but
they are not truth labels. Multiple income and non-income credits coexist in the same observed
ledger; the observation contract adds no field that yields true income through a perfect formula.

## Private datasets

Private records support causal audit, reconciliation, distribution testing, and estimator
evaluation. They must never become estimator features.

### `private/customer_ground_truth.jsonl`

One record:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.3"` |
| `customer_id` | string |
| `scenario_name` | string |
| `currency` | string |
| `income_profile` | selected `IncomeProfile` |
| `source_bundle_ref` | selected conditional bundle reference |
| `behavior_profile` | selected `BehaviorProfile` |
| `wealth_band` | selected `WealthBand` |
| `spending_multiplier_basis_points` | selected `0..100000` value |
| `saving_multiplier_basis_points` | selected `0..100000` value |
| `deposit_balance_multiplier_basis_points` | selected `0..100000` value |
| `investment_balance_multiplier_basis_points` | selected `0..100000` value |
| `income_source_ids` | zero to eight materialized source IDs |
| `primary_account_id` | primary deposit account ID |
| `opening_balance_minor` | scaled primary-account opening balance |
| `account_ids` | ordered deposit account IDs, primary first |
| `card_ids` | ordered card IDs |
| `loan_ids` | ordered in-window originated loan IDs |
| `investment_ids` | ordered investment IDs |
| `total_opening_deposit_balance_minor` | sum of scaled account opening balances |
| `total_opening_investment_balance_minor` | sum of scaled investment opening balances |
| `total_opening_loan_principal_minor` | loan principal outstanding before run start |

Schema `1.3` removes schema `1.2`'s hard-coded `employment_status`,
`true_monthly_salary_minor`, and singular `income_source_id` fields.

### `private/income_source_ground_truth.jsonl`

One record per source in the selected bundle; unemployed customers produce an empty file:

| Field | Type or value |
| --- | --- |
| `schema_version` | `"1.3"` |
| `income_source_id` | deterministic hidden source ID |
| `customer_id` | string |
| `source_ref` | selected template reference |
| `source_bundle_ref` | selected bundle reference |
| `income_kind` | private `IncomeKind` |
| `currency` | run currency |
| `payer` | private source entity |
| `description` | configured observed transaction description |
| `destination_account_id` | routed deposit account ID |
| `base_amount_minor` | factory-sampled positive amount |
| `day_of_month` | `1..31` |
| `frequency` | selected `IncomeFrequency` |
| `start_month_index` | `0..1199` |
| `occurrences` | `1..1200` scheduled attempts |
| `payment_probability_basis_points` | `0..10000` |
| `volatility_basis_points` | `0..10000` |
| `seasonality_basis_points` | 12 calendar-month values in `0..20000` |

### Other private datasets

These retain the exact schema `1.2` fields with schema version `1.3`:

- `customer_month_ground_truth`: monthly true income, expenses, counts, deposit/card openings and
  closings, loan interest, and investment return;
- `transaction_ground_truth`: every deposit entry with private economic type, source linkage,
  causal metadata, and running balance;
- `credit_card_transaction_ground_truth`: accepted card-purchase truth;
- `loan_payment_ground_truth`: paid installment component truth;
- `investment_transaction_ground_truth`: contribution, redemption, and return truth; and
- `balance_sheet_ground_truth`: monthly opening and closing assets, liabilities, and net worth.

For income deposit entries, `transaction_ground_truth.economic_type` is `INCOME` and
`income_source_id` references `income_source_ground_truth`. Monthly truth permits zero income and
multiple income events. All schema `1.2` accounting identities remain mandatory.

## Ordering and reproducibility

Schema `1.2` dataset ordering remains unchanged. Income sources are ordered by generated source ID;
income events and their ledger effects enter existing chronological and posting-priority ordering.

Given identical validated configuration, seed, effective months, member index, and version profile,
the factory member, hidden state, records, ordering, serialized JSONL, and hashes are reproducible.
Legacy V0, V1, and V2 paths retain their explicit version profiles and byte-stable reference outputs.

## Population boundary

Engine `0.4.0` generates one complete financial history per CLI run, using factory member index `0`.
`CustomerFactory.sample` supports distribution and addressability tests in memory, but it does not
write multiple histories, run parallel workers, partition datasets, or define cross-customer output
ordering. Batch population generation, deterministic parallelism, and partitioned output remain
Phase 7 work.

## Current limitations

- Income source parameters are sampled at customer creation and remain stationary for the run;
  Phase 5 owns raises, promotions, job loss, job changes, and other life events.
- Frequencies are fixed calendar-month intervals; there is no weekly, daily, or arbitrary-date
  schedule.
- Payment occurrence uses independent Bernoulli attempts. There is no arrears, catch-up payment,
  invoice, tax withholding, or source-specific settlement state.
- Volatility is bounded discrete uniform, not Gaussian, lognormal, or autocorrelated.
- Seasonality is a fixed 12-value calendar vector. There is no year-specific trend or inflation.
- Behavior scales configured amounts; it does not change merchants, dates, credit authorization
  policy, debt policy, or product ownership.
- Wealth scales opening deposits and investments only. Property, vehicles, pensions outside the
  configured cash source, and other nonfinancial or illiquid assets remain unmodeled.
- Observation coverage remains complete. Missing, delayed, duplicate, reversed, or provider-specific
  records remain Phase 6 work.
