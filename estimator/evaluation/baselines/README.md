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

# Routing benchmark

`ensemble-0.6.0-report.json` is **historical** and does not describe the shipped model. It records
`deterministic-routing-0.6.0` evaluated against `capacity-gbdt-stumps-0.5.0`, artifact
`f58f13d5...`. The bundle ships `capacity-gbdt-stumps-0.6.0`, artifact `4ee9c3a6...`, so its
`PROMOTED` status and its `21226.734` routed against `23236.3045` best-component MAE are a
measurement of a pair that is no longer assembled by anything.

Re-running the same benchmark against its own current default, on the same seed ranges and 80
customers per suite, reverses the result: routing scores `16064.1282` where the capacity model
alone scores `14910.7821`, a `7.73%` regression, and only `months_under_6` and `partial_high`
improve. The status is `NOT_PROMOTED`.

That failure is not fixed here. It is made visible: the benchmark now exits non-zero when the
status is not `PROMOTED`, and CI runs it as a release gate, so the routing rule can no longer fail
quietly while the bundle digests keep verifying. Changing routing changes the residual the
intervals are calibrated against, so routing and calibration are revised together rather than
separately, and this file is regenerated only once that pair has been re-evaluated.

```bash
cd estimator
python -m evaluation.ensemble_benchmark --output ../output/local --workers 4
```

The gate is on by default. `--no-gate` records a report for a routing rule that is still being
worked on, without claiming it passed.

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

`stress_report.py` defaults to the promoted pair, `capacity-estimator-0.6.0.json` with
`quantile-calibration-0.11.0.json`, and writes `stress-0.11.0-report.json`. That run was deferred
until a candidate had cleared its in-distribution gates. `conditional-selector-intervals-0.11.0`
has, so `stress-0.11.0-report.json` is now the current out-of-distribution evidence and the only
stress report in this directory that is attributable to a reconstructible model pair.

```bash
cd estimator
python -m evaluation.stress_report --population-size 20 --workers 4
```

It reports what the in-distribution gates could not see. On the two held-out income conditions the
promoted interval under-covers badly while still publishing almost every row: `noisy` covers
`0.348` on 224 of 240 published rows, `high_volatility` covers `0.158` on 234 of 240, both against
a nominal `0.80`. Every withheld row is withheld by the support envelope: the reason is
`OUT_OF_CALIBRATED_SUPPORT` on 16 of 240 noisy rows and 6 of 240 high-volatility rows, and on
nothing else. The envelope checks nine features one range at a time, so a row can be nothing like
the calibration population and still sit inside all nine. Mean confidence does not fall to match
either — `noisy` averages `0.744` confidence while covering `0.348`.

This is a measurement of the promoted artifact, unlike the `0.8.0` report above, and it does not
support a claim that intervals are trustworthy outside the calibration conditions. It is the
evidence that they are not.
