# Finances Simulator Contract Schema 1.4

Contract `1.4`, emitted by simulator engine `0.5.0`, adds effective-dated life events, scenario
seasonality, and privately labeled anomalies to schema `1.3`. Account, card, loan, investment, and
factory rules remain compatible with schema `1.3`. This is a project-owned, Open Finance-inspired
contract, not an official Open Finance wire format.

## Version and determinism

- Every output record carries `schema_version: "1.4"`.
- The manifest carries `contract_schema_version: "1.4"` and `simulator_version: "0.5.0"`.
- Configuration, seed, effective month count, and engine version determine all IDs and values.
- V4 uses the existing `sha256-counter-v1` isolated deterministic streams.
- Contracts `1.3`, `1.2`, `1.1`, and `1.0` continue through their frozen engine profiles.

## Configuration

Schema `1.4` contains every schema `1.3` field and four additions:

| Field | Meaning |
| --- | --- |
| `initial_life_state` | Marital, dependent, property, vehicle, and optional job-title state |
| `seasonality` | Two 12-value calendar-month multiplier vectors |
| `life_events` | Up to 256 effective-dated transitions or exceptional cash events |
| `anomalies` | Up to 256 private anomaly specifications |

Unknown fields are rejected at every level. Event and anomaly references use the same bounded
reference syntax as product and rule IDs. References are unique across both collections. Dates may
not precede `scenario.start_date`; items beyond an effective run window remain unrealized and are
not emitted.

### Initial life state

`initial_life_state` has defaults and can be omitted:

| Field | Type/default |
| --- | --- |
| `marital_status` | `SINGLE`, `MARRIED`, or `DIVORCED`; default `SINGLE` |
| `dependent_count` | integer `0..100`; default `0` |
| `property_count` | integer `0..100`; default `0` |
| `vehicle_count` | integer `0..100`; default `0` |
| `job_title` | non-empty string or null; default null |

Initial employment status derives from the selected factory income profile. Initial employer is the
selected salary payer when one exists, then the first selected source payer, otherwise null.

### Seasonality

`income_multipliers_basis_points` and `expense_multipliers_basis_points` each contain exactly 12
values indexed January through December. Each value is `0..20000`; `10000` means 1.0. Omitted
vectors default to twelve `10000` values.

Recurring source income is realized with one integer half-up operation:

```text
round_half_up(
  effective_base_amount
  * source_seasonality
  * scenario_income_seasonality
  * (10000 + volatility_shock)
  / 10000^3
)
```

Scenario expense seasonality scales recurring fixed and variable deposit expenses and each
configured card-purchase occurrence. Explicit life-event and anomaly amounts are exact and are not
seasonally scaled.

### Life events

Every life event has `life_event_ref`, `event_type`, and `effective_date`. On a shared date,
configuration order determines transition order. State changes apply before recurring financial
activity on that date.

| Event type | Required event-specific fields | Effect |
| --- | --- | --- |
| `RAISE` | `income_source_ref` and exactly one new amount or multiplier | Changes source base amount |
| `PROMOTION` | source, `new_job_title`, optional new amount or multiplier | Changes title and optional base amount |
| `JOB_LOSS` | `income_source_ref` | Deactivates source; becomes unemployed when none remain active |
| `JOB_CHANGE` | source, new payer, description, base amount, optional title | Reactivates and replaces effective job terms |
| `MARRIAGE` | none | Sets marital status to married |
| `DIVORCE` | none | Sets marital status to divorced |
| `DEPENDENT_ADDED` | optional positive `count` | Increments dependent count |
| `DEPENDENT_REMOVED` | optional positive `count` | Decrements count; underflow is an error |
| `PROPERTY_PURCHASE` | source account, amount, counterparty, description | Increments property count and posts expense |
| `VEHICLE_PURCHASE` | source account, amount, counterparty, description | Increments vehicle count and posts expense |
| `BONUS` | income source, amount, description | Posts source-attributed true income |
| `INHERITANCE` | destination account, amount, source entity, description | Posts a `GIFT` credit, never income |
| `MEDICAL_EXPENSE` | source account, amount, payee, description | Posts an expense |
| `VACATION` | source account, amount, payee, description | Posts an expense |

An amount change uses `new_base_amount_minor` or `amount_multiplier_basis_points`. A raise requires
exactly one; a promotion permits neither but never permits both. Income source IDs remain stable
across all transitions, enabling before/after evaluation without identity changes.

### Anomalies

Anomaly labels are private. Their ordinary observed transactions retain their economic meaning:

| Anomaly type | Economic type | Ledger effect |
| --- | --- | --- |
| `LARGE_PIX_TRANSFER` | `OWN_TRANSFER` | Equal debit and credit across two owned accounts |
| `REFUND` | `REFUND` | One deposit-account credit |
| `ASSET_SALE` | `ASSET_SALE` | One deposit-account credit |
| `INVESTMENT_REDEMPTION` | `INVESTMENT_REDEMPTION` | Investment decrease and linked account credit |

An anomalous redemption is rejected at generation time if its investment lacks funds. Refunds,
asset sales, redemptions, and own transfers never count as true income.

## Output tree

Schema `1.4` retains all schema `1.3` observed datasets. It adds two private files:

```text
<output>/
|-- run_manifest.json
|-- observed/
|   `-- <same 14 datasets as schema 1.3, all tagged 1.4>
`-- private/
    |-- customer_ground_truth.jsonl
    |-- customer_month_ground_truth.jsonl
    |-- income_source_ground_truth.jsonl
    |-- life_event_ground_truth.jsonl
    |-- anomaly_ground_truth.jsonl
    `-- <same remaining private product and transaction datasets as schema 1.3>
```

Observed records contain no economic type, life-event reference, anomaly label, employment state,
or income-source identity. Descriptions and amounts remain realistic estimator inputs.

### Customer truth

`customer_ground_truth` retains schema `1.3` fields and adds complete `initial_life_state` and
`final_life_state` objects.

### Monthly truth

`customer_month_ground_truth` retains schema `1.3` fields and adds:

| Field | Meaning |
| --- | --- |
| `external_inflows_minor` | Sum of gift, refund, and asset-sale credits |
| `life_event_count` | Effective transitions in month |
| `anomaly_count` | Realized anomaly labels in month |
| `employment_status` | State at month end |
| `active_income_source_ids` | Active source IDs at month end |
| `marital_status` | State at month end |
| `dependent_count` | State at month end |
| `property_count` | State at month end |
| `vehicle_count` | State at month end |

The Phase-5 net-worth bridge is:

```text
net_worth_change
  = true_income
  + external_inflows
  - true_expenses
  - loan_interest_paid
  + investment_return
```

Own transfers and investment principal movements cancel across assets. Property and vehicle counts
are not valued balance-sheet assets, so their purchases remain expenses in this bounded model.

### Life-event truth

Each `life_event_ground_truth` record contains event ID/ref/type/date, customer ID, full customer
state before and after, every selected income-source state before and after, annualized base income
before and after, and an optional linked financial event ID. Source state contains ID, reference,
active flag, base amount, payer, and description.

Annualized base income sums `base_amount * 12 / frequency_interval_months` for active sources. It is
a transition-comparison label, not realized cash income and not estimator input.

### Anomaly truth

Each `anomaly_ground_truth` record contains anomaly ID/ref/type/date, customer ID, linked financial
event ID, and preserved `economic_type`. The referenced event and ledger transaction remain in the
normal private transaction and observed transaction datasets.

## Invariants

- Transition `state_before` equals the preceding `state_after`.
- Source-state sets retain the same materialized source IDs across transitions.
- Recurring income uses source state effective on its transaction date.
- Financial life events link exactly one configured transition.
- Every anomaly label references an event with the required economic type.
- Ledger, card, loan, investment, transfer, balance-sheet, and leakage invariants remain mandatory.
- Repeating a run with identical inputs produces identical hidden state, output records, ordering,
  file bytes, and manifest hashes.

## Bounded limitations

- Life events target already selected sources; they do not create a new source ID.
- Employment transitions do not model taxes, severance, unemployment insurance, or arrears.
- Marriage and divorce do not create a second customer or merge assets.
- Dependents, property, and vehicles are counts without age, identity, valuation, financing, or
  depreciation models.
- Explicit anomalies are deterministic configured events, not a stochastic anomaly population.
- Observation degradation, duplicate/reversal injection, consent coverage, and provider-specific
  descriptions remain Phase 6 work.
