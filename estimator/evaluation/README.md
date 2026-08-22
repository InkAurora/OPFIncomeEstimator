# Evaluation boundary

Evaluation code may compare completed predictions with physically isolated private truth. It must
never pass private fields back into estimator input or runtime features. Runtime code must never
import this directory.

`benchmark.py` compares frozen estimator `0.1` with candidate `0.2` on fixed synthetic held-out
seeds. It adds RMSE, WAPE, SMAPE, interval width, and promotion checks to simulator metrics. Raw
customer identifiers and row-level truth are not written. Generate aggregate JSON and SVG artifacts:

```bash
cd estimator
python -m evaluation.run_benchmark --workers 4
```

`ensemble_benchmark.py` compares estimator `0.6` routing against every individual component on
fixed held-out seeds, records which routing rule fired on each row, and writes
`baselines/ensemble-0.6.0-report.json`. Promotion requires the routed estimate to be no worse than
the best component overall and strictly better in at least one segment:

```bash
cd estimator
python -m evaluation.ensemble_benchmark --population-size-per-suite 80 --workers 4
```
