# ADR 0001: Income target definitions

- Status: Accepted
- Date: 2026-08-21
- Estimator version: `0.1.0`

## Context

Income can mean observed cash receipts, expected source output, or sustainable future capacity.
Using one unnamed target would make evaluation and later supervised training ambiguous.

## Decision

All amounts use integer minor currency units. A calendar month follows the transaction currency's
agreed business timezone; contract `1.0` provides dates without intraday timezone conversion.

- `realized_income_month`: sum of economic `INCOME` events posted during the calendar month. Include
  salary, business/service receipts, pension, dividends, and realized employment bonuses. Exclude
  own-account transfers, gifts, refunds, reversals, borrowing, investment principal redemptions,
  inheritance, and asset-sale proceeds. A job change affects the target when each payment posts.
- `expected_income_month`: sum of probability-weighted expected receipts from sources active at the
  month cutoff. Apply source frequency, seasonality, payment probability, and effective-dated state.
  This value may be positive when realized income is zero.
- `sustainable_monthly_income`: robust central monthly value of non-one-off sources active at the
  reference cutoff over the next 12 calendar months. Exclude extraordinary bonuses and one-off
  inflows. A ended source contributes zero after its end date.
- `realized_income_trailing_12m`: sum of `realized_income_month` over 12 complete months ending at
  the reference month. When fewer than 12 complete months are observable, return unavailable rather
  than annualize silently.
- `expected_income_next_12m`: sum of `expected_income_month` over the 12 months after the reference
  cutoff, using only state known at that cutoff.

Estimator `0.1.0` targets only `realized_income_month`. It reconstructs every requested simulation
month from observations available by `window_end`. Partial first or last months are retained but
must carry reduced confidence in a future output contract. Missing history is not interpreted as
zero sustainable income.

## Consequences

- Runtime inference cannot import or inspect economic event labels. Private target construction
  remains in training/evaluation code.
- Point-in-time features reject observations arriving after the request cutoff.
- Future estimator contracts must name realized and sustainable outputs separately.
- Supervised training cannot start until dataset builders implement these definitions verbatim and
  leakage tests verify historical cutoffs.
