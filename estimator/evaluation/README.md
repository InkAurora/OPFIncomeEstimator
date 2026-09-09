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

`ensemble_benchmark.py` compares ensemble routing against every individual component on fixed
held-out seeds, records which routing rule fired on each row, and writes
`baselines/ensemble-0.8.0-report.json`. Promotion requires the routed estimate to be no worse than
the best component overall and strictly better in at least one segment, a condition now
unsatisfiable by construction because no rule routes away from the capacity model; see
[ADR 0010](../../docs/adr/0010-coverage-oracle-removed.md):

```bash
cd estimator
python -m evaluation.ensemble_benchmark --population-size-per-suite 80 --workers 4
```

`stress_report.py` runs the routed estimator across six named suites and reports each separately,
recording for every suite whether the promoted models were trained on its conditions:

```bash
cd estimator
python -m evaluation.stress_report --population-size 20 --workers 4
```
