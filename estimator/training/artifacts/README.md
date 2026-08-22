# Experimental transaction classifier 0.3

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
