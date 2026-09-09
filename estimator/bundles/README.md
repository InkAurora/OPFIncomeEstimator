# Deployment bundles

One directory per release, holding everything a deployment needs to run the promoted estimator and
nothing it needs to look up elsewhere. Bundle contract `1.0` is defined in
[`contracts/bundle_v1.py`](../src/income_estimator/contracts/bundle_v1.py); the loader that refuses
anything else is [`production.py`](../src/income_estimator/production.py).

A bundle is identified by the SHA-256 of its `manifest.json`. The manifest pins every other file by
digest, so that one number covers the directory transitively, and it is what a production result
reports back.

## `production-0.13.0`

| File | Role | Version |
|---|---|---|
| `artifacts/capacity-estimator-0.7.0.json` | sustainable-income point estimate | `capacity-gbdt-stumps-0.7.0` |
| `artifacts/quantile-calibration-0.13.0.json` | its interval, or an abstention | `conditional-selector-intervals-0.13.0` |
| `provenance/capacity-estimator-0.7.0-report.json` | why that model | — |
| `provenance/quantile-calibration-0.13.0-report.json` | every validation gate | — |
| `provenance/lockbox-…-0.13.0-report.json` | `RELEASE_CONFIRMED` on these exact bytes | — |

Requires feature set `customer-month-features-1.3.0` and `income-estimator` `0.13.0` or newer.
Accepts input contracts `1.0` through `1.3`; emits output `1.2` and explanation `1.0`. Output `1.2`
publishes `qualified_sustainable_income_minor` only where a month's evidence is assessed
`SUPPORTED`; a consumer still on `1.1` calls `estimate_v1_1` and gets what it always got.

Lockbox seeds `1_010_000`+. Floors `610_000`, `710_000`, `810_000`, and `910_000` are already spent
by earlier releases. Lockbox result: coverage `0.8756` on 8639/8640 rows, `RELEASE_CONFIRMED`.

The promotion decision is [ADR 0010](../../docs/adr/0010-coverage-oracle-removed.md). It removed the
coverage oracle from every input path: contract `1.3` forbids the simulator's withheld-record
counts and requires receiver-known `consent_scopes` instead, and no rule now routes away from the
capacity model.

## `production-0.12.0` — superseded

**Superseded — see [ADR 0010](../../docs/adr/0010-coverage-oracle-removed.md).** Its inputs carried
the coverage oracle: `customer-month-features-1.2.0` read per-account record counts only the
simulator could produce, `recurring-streams-0.2.0` scaled by the same ratio, and
`deterministic-routing-0.7.0` routed to the cash-flow component wherever that ratio fired. Every
figure below is withdrawn where it touches `incomplete_observation` or `partial_consent`.

| File | Role | Version |
|---|---|---|
| `artifacts/capacity-estimator-0.6.0.json` | sustainable-income point estimate | `capacity-gbdt-stumps-0.6.0` |
| `artifacts/quantile-calibration-0.12.0.json` | its interval, or an abstention | `conditional-selector-intervals-0.12.0` |
| `provenance/capacity-estimator-0.6.0-report.json` | why that model | — |
| `provenance/quantile-calibration-0.12.0-report.json` | every validation gate | — |
| `provenance/lockbox-…-0.12.0-report.json` | `RELEASE_CONFIRMED` on these exact bytes | — |

One lockbox reading, of the bytes shipped here. `0.11.0` needed two, because its support envelope
was attached after the first reading, so the report that promoted it described bytes no deployment
ran. `0.12.0` is written with its envelope in place: seeds `810_000`+, generated for the first time
by that run and read once, `RELEASE_CONFIRMED`, coverage `0.9122` on `8,636` of `8,640` rows against
a `0.75` floor and a `0.80` nominal, empty failure list.

Requires feature set `customer-month-features-1.2.0` and `income-estimator` `0.12.0` or newer.
Accepts input contracts `1.0` through `1.2`; emits output `1.2` and explanation `1.0`. Output `1.2`
publishes `qualified_sustainable_income_minor` only where a month's evidence is assessed
`SUPPORTED`; a consumer still on `1.1` calls `estimate_v1_1` and gets what it always got.

The promotion decision is [ADR 0009](../../docs/adr/0009-routing-narrowed-and-recalibrated.md).
Read its known limits before deploying anything: the intervals hold in the calibration conditions,
are measured failing badly outside them, and this release makes the `noisy` suite worse rather than
better. That cost is attributed in the ADR and was accepted deliberately.

## Why the artifacts are copied rather than referenced

A bundle that pointed back into `training/artifacts/` would be a bundle only on the machine that
built it. Copies cost a few hundred kilobytes and buy a directory that can be archived, shipped, and
verified anywhere.

## Rebuilding

```bash
cd estimator
python -m release.build_bundle --output bundles/production-0.13.0
```

Deterministic. `tests/test_release.py` asserts the committed bundle is byte-identical to what the
builder emits, so a hand-edited manifest fails the suite. The builder also refuses to assemble a
pair whose calibration was not fitted against the capacity bytes being bundled, or one whose
promotion reports do not record a passing, failure-free run over those exact bytes.

## Line endings

`.gitattributes` pins `estimator/bundles/**/*.json` to `eol=lf`. Every digest in the manifest is
taken over exact bytes, so a checkout that translated line endings would break the bundle's own
integrity check on Windows.

## Verifying one

```bash
cd estimator
python -c "from pathlib import Path; from income_estimator.production import verify_bundle; print(verify_bundle(Path('bundles/production-0.13.0'))[1])"
```

`verify_bundle` checks presence and digests without constructing a model.
`ProductionIncomeEstimator.from_bundle` does that and then also enforces the capacity/calibration
binding, the feature set and its schema fingerprint, the contract versions, and the package floor.
