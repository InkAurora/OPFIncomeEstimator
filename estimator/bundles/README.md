# Deployment bundles

One directory per release, holding everything a deployment needs to run the promoted estimator and
nothing it needs to look up elsewhere. Bundle contract `1.0` is defined in
[`contracts/bundle_v1.py`](../src/income_estimator/contracts/bundle_v1.py); the loader that refuses
anything else is [`production.py`](../src/income_estimator/production.py).

A bundle is identified by the SHA-256 of its `manifest.json`. The manifest pins every other file by
digest, so that one number covers the directory transitively, and it is what a production result
reports back.

## `production-0.11.0`

| File | Role | Version |
|---|---|---|
| `artifacts/capacity-estimator-0.6.0.json` | sustainable-income point estimate | `capacity-gbdt-stumps-0.6.0` |
| `artifacts/quantile-calibration-0.11.0.json` | its interval, or an abstention | `conditional-selector-intervals-0.11.0` |
| `provenance/capacity-estimator-0.6.0-report.json` | why that model | — |
| `provenance/quantile-calibration-0.11.0-report.json` | every validation gate | — |
| `provenance/lockbox-…-0.11.0-report.json` | `RELEASE_CONFIRMED` on the bytes that were promoted | — |
| `provenance/lockbox-…-0.11.0-release-report.json` | `RELEASE_CONFIRMED` on the bytes shipped here | — |

Two lockbox readings, because they measured different bytes. The promoted artifact gained a support
envelope after the first reading, so the second was taken against exactly what this bundle ships: it
confirms, having withheld `14` of `8,640` rows and altered no published bound. Only the second is a
statement about what a deployment will actually run.

Requires feature set `customer-month-features-1.2.0` and `income-estimator` `0.11.0` or newer.
Accepts input contracts `1.0` through `1.2`; emits output `1.1` and explanation `1.0`.

The promotion decision is [ADR 0008](../../docs/adr/0008-conditional-selector-promotion-and-abstention.md).
Read its known limits before deploying anything: the intervals hold in the calibration conditions
and are measured failing badly outside them.

## Why the artifacts are copied rather than referenced

A bundle that pointed back into `training/artifacts/` would be a bundle only on the machine that
built it. Copies cost a few hundred kilobytes and buy a directory that can be archived, shipped, and
verified anywhere.

## Rebuilding

```bash
cd estimator
python -m release.build_bundle --output bundles/production-0.11.0
```

Deterministic. `tests/test_release.py` asserts the committed bundle is byte-identical to what the
builder emits, so a hand-edited manifest fails the suite. The builder also refuses to assemble a
pair whose calibration was not fitted against the capacity bytes being bundled.

## Line endings

`.gitattributes` pins `estimator/bundles/**/*.json` to `eol=lf`. Every digest in the manifest is
taken over exact bytes, so a checkout that translated line endings would break the bundle's own
integrity check on Windows.

## Verifying one

```bash
cd estimator
python -c "from pathlib import Path; from income_estimator.production import verify_bundle; print(verify_bundle(Path('bundles/production-0.11.0'))[1])"
```

`verify_bundle` checks presence and digests without constructing a model.
`ProductionIncomeEstimator.from_bundle` does that and then also enforces the capacity/calibration
binding, the feature set and its schema fingerprint, the contract versions, and the package floor.
