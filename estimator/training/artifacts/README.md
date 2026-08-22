# Frozen training artifacts

## Capacity estimator 0.5 — promoted

- `capacity-estimator-0.5.0.json` is the portable, dependency-free model artifact;
- `capacity-estimator-0.5.0-report.json` records versions, artifact SHA-256, customer and row
  counts, train/validation/test metrics for the candidate and all three baselines, segmented
  results, and the promotion decision.

The model is a hurdle. A logistic gate decides whether sustainable income is zero; an anchored
regressor sizes it when it is not. The regressor boosts `log1p(sustainable_monthly_income)` around
`log1p(income_mean_3m_minor)`, so an empty model reproduces that anchor and every tree moves the
estimate away from it only for a measured reason. Both parts are decision stumps over the same
binned features, and missing features are routed by a direction recorded per stump rather than
imputed.

Two design points are worth keeping in mind when the model is retrained:

- A single regressor on the raw log target loses to a trivial baseline. Zero-income rows have a
  log residual near `-13` where ordinary rows sit near `0.3`, so squared error spends the model's
  capacity on them and drags every other estimate down. Splitting the zero decision out is what
  makes the rest of the fit possible.
- The gate threshold is selected on validation by mean absolute error in minor units, not by
  classification score. A wrong zero costs the whole estimate; a wrong positive costs only its
  error.

Features come from estimator input `1.2` through feature set `customer-month-features-1.1.0`.
Labels come from private contract `income-targets-1.0`, joined only after observed features are
built. Customers are split 70/15/15 by the frozen SHA-256 partition shared with the `0.3` dataset,
so no customer spans two partitions.

Held-out test results on 240 customers across three suites, against the best baseline
(`cash_flow_last_month`):

| metric | candidate | best baseline |
|---|---|---|
| MAE (minor) | 25,055 | 55,455 |
| RMSE (minor) | 48,741 | 131,013 |
| WAPE | 0.0459 | 0.1015 |
| SMAPE | 0.0563 | 0.1396 |

By segment, the candidate improves full coverage (33,629 against 81,919), partial consent
(14,031 against 21,429), volatile income (43,965 against 163,614), short history, and every income
band. It predicts zero-income customers exactly. It is slightly worse than the cash-flow baseline
on the perfectly stable salaried band (15,444 against 12,418), which is expected: for a customer
whose salary never moves, last month's reconstruction is already the answer.

Reproduce from the `estimator` directory:

```bash
python -m training.train_capacity_estimator --population-size-per-suite 80 --workers 4
```

## Experimental transaction classifier 0.3 — not promoted

These files are frozen outputs from the estimator `0.3` supervised transaction-classifier
evaluation:

- `transaction-classifier-0.3.0.json` is the portable, dependency-free model artifact;
- `transaction-classifier-0.3.0-report.json` records versions, artifact SHA-256, customer and record
  counts, train/validation/test metrics, critical false-positive rates, and promotion status.

The dataset uses three deterministic 120-customer suites (`income_diverse`, `life_events`, and
`incomplete_observation`). Features are extracted from estimator input `1.1` before private labels
are joined. Customers are assigned to disjoint 70/15/15 partitions by a versioned SHA-256 function.

Reproduce from the `estimator` directory:

```bash
python -m training.train_transaction_classifier \
  --project-root .. \
  --output training/artifacts \
  --population-size-per-suite 120 \
  --workers 4
```

The held-out candidate ties the frozen rules at F1 `0.99552372` and keeps every critical
false-positive rate at zero. Promotion requires a strict F1 improvement, so the report records
`NOT_PROMOTED`. The artifact is retained for reproducibility and explicit experiments; it is not the
default estimator.
