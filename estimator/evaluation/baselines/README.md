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

`ensemble-0.7.0-report.json` is the current evidence: `deterministic-routing-0.7.0` against
`capacity-gbdt-stumps-0.6.0`, `PROMOTED`. Routing now fires only where income is stable and consent
coverage is declared incomplete.

`ensemble-0.6.0-report.json` is retained and is **historical**. It records the superseded rule
evaluated against `capacity-gbdt-stumps-0.5.0`, artifact `f58f13d5…`, while the bundle of the day
shipped `0.6.0`, artifact `4ee9c3a6…`. Re-run against the model it actually shipped, that rule
scored `16064.13` where the capacity model alone scored `14910.78`: a `7.73%` regression reported as
`NOT_PROMOTED`. Its `21226.734` versus `23236.3045` figures describe a pair nothing assembles.

The reversal is the point. ADR 0008 recorded that conditioning routing on coverage had been measured
and rejected; that measurement was taken against `0.5.0`, and against `0.6.0` it reverses. Digest
checks protect files. They do not notice when a conclusion stops being true.

```bash
cd estimator
python -m evaluation.ensemble_benchmark --workers 4
```

The gate is on by default: a status other than `PROMOTED` exits non-zero, and release CI runs it.
`--no-gate` records a report for a routing rule that is still being worked on, without claiming it
passed. The report is named for the routing version it measures, so a run cannot overwrite a report
about a different rule.

Selection used the seeds above; the chosen rule was confirmed afterwards on seeds `910_000`-`930_000`,
which no run had generated, over 71 customers and 852 rows. See
[ADR 0009](../../../docs/adr/0009-routing-narrowed-and-recalibrated.md).

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
`quantile-calibration-0.12.0.json`, and writes `stress-0.12.0-report.json`. The report is named for
the calibration it measured; the name was pinned at `0.11.0` while the default moved on, so a run of
one pair could overwrite a report about another.

```bash
cd estimator
python -m evaluation.stress_report --population-size 20 --workers 4
```

`stress-0.12.0-report.json` is the current out-of-distribution evidence. Against `stress-0.11.0`,
which measured the previous routing and calibration:

| Suite | In distribution | `0.11.0` | `0.12.0` |
| --- | --- | ---: | ---: |
| `normal` | yes | `0.7583` | **`0.8042`** |
| `partial_consent` | yes | `0.9583` | `0.9375` |
| `life_events` | yes | `0.9750` | **`1.0000`** |
| `noisy` | no | `0.3482` | **`0.0888`** |
| `high_volatility` | no | `0.1581` | `0.1483` |

Nominal is `0.80`. In distribution this is the best result recorded: `normal` lands on nominal rather
than under it. Out of distribution `noisy` is materially worse, and the cost is attributed in
[ADR 0009](../../../docs/adr/0009-routing-narrowed-and-recalibrated.md): routing alone takes it to
`0.1786` and the refit around that routing takes it the rest of the way. It was accepted, not
overlooked.

`high_volatility` is unchanged by any of it. Those rows are not out of support and cannot be fenced
into being: `4` of `240` fall outside any fenced range, and their joint distance from the calibration
centre is lower than the `normal` suite's. What fails is conditional coverage, not support.

`stress-0.11.0-report.json` is retained as the comparison above, and describes a pair the runtime now
refuses to load.
