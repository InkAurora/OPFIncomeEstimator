# Private Income Target Contract 1.0

Contract `income-targets-1.0` publishes the five private income targets defined by
[ADR 0001](../../docs/adr/0001-income-target-definitions.md) and constructed by
[ADR 0002](../../docs/adr/0002-income-target-construction.md). It exists because the estimator
needs to separate realized, expected, and sustainable income, while the customer-month record
carries only realized income as `true_income_minor`.

The contract is additive. It introduces no scenario contract version, changes no record of
observation contracts `1.0` through `1.5`, and writes no dataset file. Reference runs committed
under those contracts remain byte-identical.

## Trust boundary

These records are private truth. They may be joined with observed features only inside an isolated
training or evaluation step, and never inside estimator runtime. They are produced on demand rather
than written beside observations:

```python
from finances_simulator.ground_truth import project_income_targets

targets = project_income_targets(generated.simulation)
```

## Availability

Targets require the private income-source record introduced by contract `1.3`. Runs on contracts
`1.0` through `1.2` raise `IncomeTargetProjectionError` rather than emit partial targets. Contracts
`1.3`, `1.4`, and `1.5` are supported; `1.3` has no life events, so effective-dated state reduces to
the source's initial parameters.

## Record

One record per customer and simulated calendar month.

```text
schema_version                        "1.0"
customer_id
month                                 YYYY-MM
currency

realized_income_month_minor           INCOME events posted in the month
expected_income_month_minor           expectation of the month's scheduled attempts, plus bonuses
sustainable_monthly_income_minor      recurring active capacity, monthly equivalent
realized_income_trailing_12m_minor    null before twelve complete months
expected_income_next_12m_minor        expectation of the next twelve months at cutoff state

active_source_count                   sources active at cutoff with a remaining attempt
recurring_source_count                active sources with at least two remaining attempts
bonus_income_month_minor              the part of realized income that is a bonus life event
is_partial_month                      the calendar month is not fully inside the window
```

## Expectation model

The engine pays one attempt with probability `payment_probability_basis_points / 10000` and scales
the base amount by source seasonality, scenario seasonality, and a volatility shock drawn uniformly
from a symmetric interval. Because the shock has zero mean, one attempt's expected amount is exactly

```text
base_effective * source_seasonality * scenario_seasonality * payment_probability / 10**12
```

Targets sum these integer ratios and round half up once. Over a population, realized and expected
totals converge; a test asserts that they agree within `5%` on forty stochastic customers and
exactly on a deterministic scenario.

## Rules worth knowing

- **Forward targets use cutoff state.** `sustainable_monthly_income` and `expected_income_next_12m`
  apply no life event effective after the cutoff. A job loss next month does not reduce this
  month's capacity.
- **Bonuses** count as realized income and as expected income for their own month. They never
  count as sustainable income.
- **Inheritance** is a `GIFT`, and is income under no target.
- **A schedule that fills the window is not an ending source.** Scenarios size `occurrences` to
  cover the simulated months. A source whose next attempt would fall outside the window is treated
  as continuing, so capacity does not decay to zero at the window edge. A source that stops while
  the window still runs is a genuine end and is honored as one.
- **Zero is a value.** A customer with no active source has sustainable income of exactly zero.
  A realized zero never forces the forward targets to zero.
- **Fewer than twelve complete months** leaves `realized_income_trailing_12m_minor` null rather
  than annualizing.

## Determinism

The projection is a pure function of the simulation run. Repeated calls return identical records,
and the scenario income-seasonality multipliers used by the expectation live on the run itself, so
no configuration is needed at projection time.
