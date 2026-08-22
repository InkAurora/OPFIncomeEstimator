# Estimator 0.2 held-out baseline

Artifacts use `synthetic-heldout-1.0.0`: three deterministic 100-customer populations with 12
months per customer and fixed seed ranges. Estimator inference sees observations only. Aggregate
private truth is joined afterward inside the evaluation zone.

- `estimator-0.2-heldout-report.json` records dataset, simulator, contract, feature, estimator, and
  model versions plus segmented metrics and promotion checks.
- `estimator-0.2-true-vs-estimated.svg` compares versions `0.1` and `0.2` on incomplete observation.

Regenerate from repository root:

```bash
cd estimator
python -m evaluation.run_benchmark --workers 4
```
