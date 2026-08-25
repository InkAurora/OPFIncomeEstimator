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

# Stress suites

`stress-0.8.0-report.json` is **void as evidence** and is retained only so the defect it exposed
stays on the record. It was produced by `evaluation/stress_report.py` with
`capacity-gbdt-stumps-0.5.0` and `conformal-intervals-0.8.0`, whose recorded binding
`f4f10e8d...` names the pre-rename bytes of what is now `capacity-estimator-0.6.0.json`. Those
bytes are not in the repository, so the pair cannot be reconstructed and the run cannot be
reproduced. The tool's own default capacity model had since moved to `0.6.0` without the report
being regenerated, so re-running would not have reproduced it either — and now cannot: the runtime
checks the binding and refuses that combination outright.

The `0.491` noisy and `0.125` high-volatility coverage figures quoted from it are therefore
attributable to no coherent model pair. They are still the reason out-of-distribution behaviour is
an open question; they are not a measurement of any artifact in this repository.

`stress_report.py` now defaults to the only pair here whose binding resolves,
`capacity-estimator-0.6.0.json` with `quantile-calibration-0.9.0.json`, and writes
`stress-0.9.0-report.json`. That run is deferred: `adaptive-intervals-0.9.0` is `NOT_PROMOTED`, and
measuring out-of-distribution coverage is worth doing once against a candidate that has cleared its
in-distribution gates.

```bash
cd estimator
python -m evaluation.stress_report --population-size 20 --workers 4
```
