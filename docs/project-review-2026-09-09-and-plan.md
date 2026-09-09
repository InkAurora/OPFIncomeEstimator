# Project review and forward plan — 9 September 2026

Baseline: `2023e02`. Follows [the 8 September review](project-review-2026-09-08.md) and
[the bank underwriting assessment](bank-underwriting-assessment.md). Both remain valid; this
document records what their fixes closed, what they did not see, and the plan that follows.

**The main finding is that the estimator is, in measurable part, an inverse of the simulator rather
than a model of income.** Three feature-to-label identities exist in the code that hold in the
generator and cannot hold on a real feed. One of them — a coverage oracle — is the reason the
routing rule promoted yesterday under [ADR 0009](adr/0009-routing-narrowed-and-recalibrated.md)
wins its benchmark. Every promotion gate then measures new draws from the same generator, so none
of them can detect this. The engineering discipline around the numbers is real; the numbers
themselves are a fixed point of the generator.

Nothing in this review changes runtime or model files. Suites were run in this checkout on Windows,
Python 3.14.6: estimator `230 passed, 4 skipped` (opt-in clean-install tests), simulator
`321 passed`, demo `34 passed`. The estimator suite must be run from `estimator/` as CI does; from
the repository root with `-c` it fails to collect `evaluation` and `training`.

## What the 8 September fixes closed

| Finding | Status | Evidence |
| --- | --- | --- |
| 1 Routing benchmark and silent gate | Closed | Gate exits non-zero by default; CI runs it; `ensemble-0.7.0-report.json` is `PROMOTED` |
| 2 Quarterly imputation | Closed | `models/recurring.py` `_due_months`; regression test |
| 3 Coincident debit exclusion | Closed | `transaction_intelligence/features.py` requires a `counterparty_cluster` link |
| 4 Portuguese descriptions | Partial, by design | Reviewed Portuguese payroll terms added; unrecognized recurring credits raise `REVIEW_REQUIRED` rather than becoming income |
| 5 Unsupported point estimates | Closed | `contracts/output_v1_2.py` binds `assessment_status` to `qualified_sustainable_income_minor` |
| 6 Release accepts rejected evidence | Closed | `release/build_bundle.py` validates status, failures, digests; clean-install tests fail instead of skip |
| **7 Retrospective coverage fields** | **Closed later on 9 September** | Was open at review time; see finding 1 below and [ADR 0010](adr/0010-coverage-oracle-removed.md) |
| 8 Date and window validation | Closed | `contracts/v1.py` canonical dates, `months` must match window |
| 9 Fixtures, packaging, CI | Closed | Parquet allowlisted, fixtures pinned LF, demo package declared, demo AppTest in CI |

Finding 7 was left open as the hardest of the nine. It turns out to be the most consequential.

## Findings

### 1. P0 — The promoted routing rule wins by reading a number only the simulator knows

> **Status (9 September, later the same day): closed by
> [ADR 0010](adr/0010-coverage-oracle-removed.md).** Input contract `1.3` forbids the coverage
> record and requires receiver consent scopes; no estimator path reads `eligible_record_count` or
> `observed_original_record_count`; nothing scales income upward. Re-measured on corrected inputs
> the routing rule fired on zero rows and improved no segment, and was removed. Capacity `0.7.0`,
> routing `0.8.0`, calibration `0.13.0`, bundle `production-0.13.0`. Figures in the ADR.

The simulator computes each account's `eligible_record_count` and `observed_original_record_count`
from the records it itself generated and then withheld
([`projector_v5.py`](../finances_simulator/src/finances_simulator/observations/projector_v5.py),
lines 335–359). The adapter copies both into the estimator request
([`adapter.py`](../finances_simulator/src/finances_simulator/integration/adapter.py), lines
108–121). The cash-flow reconstruction then divides observed income by their ratio:

```python
# models/cashflow.py, lines 24-32 and 64
basis_points = observed_original_record_count * 10_000 // eligible_record_count
estimate += (observed * 10_000 + coverage // 2) // coverage
```

On the `incomplete_observation` suite this is exact: hidden income is recovered by dividing by
the exact hidden fraction. `cash_flow_last_month` is therefore an oracle wherever coverage is
declared partial.

ADR 0009 routes to `cash_flow_last_month` precisely when `income_cv_12m` is stable **and**
`effective_consent_coverage_basis_points < 10_000`
([`ensemble.py`](../estimator/src/income_estimator/models/ensemble.py), lines 121–128). That
feature is the same ratio, eligible-record weighted
([`coverage.py`](../estimator/src/income_estimator/features/coverage.py), lines 18–40). The
selection result — `12179.10` against `14910.78`, confirmed on fresh seeds `910_000`+ with the
same ranking — is the oracle being confirmed twice. Fresh seeds are new draws from the same
generator and carry the same field.

No receiver of Open Finance data has this number. A provider cannot report how many records a
customer's unconsented institutions hold, and a receiver cannot count records it never fetched.
The `partial_consent` stress result (`0.9375` coverage, `0.66%` sustainable WAPE in the
8 September review) is the same oracle read through the interval.

The calibration `conditional-selector-intervals-0.12.0` was refit around this routing, so it
inherits the dependency.

**Fix:** remove `eligible_record_count` and `observed_original_record_count` from every estimator
code path. Replace with receiver-knowable evidence: consented account list, fetched date range per
account, pagination-complete flag, first-observed timestamp per record, known-missing domains.
Unknown external coverage stays unknown and never scales income upward. Re-run the routing
benchmark on the corrected inputs. The expected result is that `deterministic-routing-0.7.0` loses
to the capacity model alone; if so, revert to capacity-alone, recalibrate, and record the reversal
in an ADR. Keep the simulator's full-window diagnostics in the evaluation zone only, joined after
inference like every other private label.

**Exit:** no estimator input field encodes information unavailable to a receiver at the cutoff;
routing and calibration are re-decided on the corrected inputs; the model card states the change
in every affected metric.

### 2. P0 — Two more feature-to-label identities make the headline metrics tautological

**Sustainable-income target.** The label is
`base × source_seasonality × scenario_seasonality × payment_probability / 12`
([`income_targets.py`](../finances_simulator/src/finances_simulator/ground_truth/income_targets.py),
lines 142–151). The feature `source_monthly_capacity_minor` is
`median(paid amounts) × payments_per_year / 12`
([`detector.py`](../estimator/src/income_estimator/income_streams/detector.py), lines 32–50).
Salary amount noise is uniform, zero-mean, ±250 bp; `day_of_month` is a constant per source, so
observed gaps are 28–31 days and `_frequency` bins to `MONTHLY` deterministically. The gradient
booster is left to learn a division by `payment_probability`, which takes roughly six distinct
values across the three training scenarios. Sustainable WAPE `0.05` is consistent with that, not
with an estimation problem.

**Income classification.** `rules.py` says it directly
([lines 15–17](../estimator/src/income_estimator/transaction_intelligence/rules.py)): *"The
English terms below are the simulator's vocabulary."* Thirteen income description templates
exist across the scenario configurations; every one is covered by a strong keyword with no false
positives. Realized WAPE `0.0` on five of six suites is a lookup succeeding, not classification.

**Fix:** measure the size of this before repairing it. Two ablations: refit capacity without the
`source_*` features; refit with every description replaced by one opaque token. The MAE gap is
the fraction of measured skill that is generator inversion. Then randomize the tells in the
simulator without touching the estimator: per-customer payday jitter with business-day logic,
descriptions sampled from a vocabulary with variants and truncation, amount noise well above
250 bp, occasional misdeclared coverage.

**Exit:** the model card carries a line "share of sustainable-income skill attributable to
generator structure: N%" with the ablation that produced it; realized WAPE on the training
scenarios is a nonzero measured quantity.

### 3. P1 — Every gate measures new customers, never new conditions

Training seeds `110_000`–`130_000`, calibration `410_000`+, validation `510_000`+, lockbox
`810_000`+ all draw from `income_diverse`, `life_events` and `incomplete_observation`, every one
with `start_date: 2024-01-01`. The split is customer-disjoint by hashed ID
([`datasets.py`](../estimator/training/datasets.py), line 21). There is no split by time, income
regime, provider, or vocabulary. Customer-disjoint holdouts from an identical generator measure
estimator variance within that generator; they cannot measure bias against anything outside it.

The stress suites are the only regime probe and they are confounded: `noisy_observation` and
`high_volatility` change the income process, the description vocabulary and the feed quality at
once, so a failure is unattributable. They gate nothing. The implementation plan
([lines 663–667](estimator-implementation-plan.md)) already names an `out_of_distribution` suite
built by holding a profile out of training, and declines it as "scoped work".

**Fix:** do the scoped work. Train on the training scenarios minus one income profile
(`BUSINESS_OWNER` or `SELF_EMPLOYED`), gate on the held-out profile. Add one time split: train on
the first eight months of the window, evaluate on the last four. Split the stress suites so each
varies one thing.

**Exit:** the promotion report carries a regime-holdout row and a time-holdout row beside the
customer-holdout row, and the gate reads all three.

### 4. P1 — Intervals fail across regimes and the diagnosis in ADR 0009 is right but unacted

Coverage is `0.90` in distribution against `0.80` nominal, and `0.09`–`0.15` on the two
out-of-distribution suites. ADR 0009 rejects tightening the support envelope on good evidence and
names the real cause: the correction is conditioned on `observed_domain_count`, which barely varies
in distribution, so the selector inflates the modal cell instead of separating regimes.
Over-coverage in distribution is the same symptom, not a safety margin. Customer-clustered standard
errors price sampling noise; the error that dominates under regime shift is bias, which no error
bar sizes.

**Fix:** condition the selector on a variable that separates the regimes — `income_cv_12m`,
detected frequency, or a feed-quality signal — and rank conditioners on the regime holdout from
finding 3, not on in-distribution data where they have nothing to prove. Do not start this before
findings 1 and 2 land, because both move the residual distribution.

**Exit:** out-of-distribution coverage on the split stress suites is reported per suite beside the
in-distribution figure, and the gate has a floor for it.

### 5. P2 — Effort is concentrated on provenance for numbers that do not transfer

- Calibration artifact schema `1.0`→`1.6`, capacity digest binding, `ensemble_version` binding,
  read-once lockbox: rigorous provenance for a coverage figure that holds in distribution only.
- Routing: three ADRs, a dedicated benchmark and a reversal post-mortem to beat a trivial baseline
  by a few percent, on an oracle, at the cost of `noisy` coverage falling `0.348`→`0.089`.
- Explainability: exact per-stump attribution over 104 features, several of which are simulator
  tells. Explaining those splits explains the simulator.
- Seven frozen simulator configuration generations and five engine versions still carried and
  tested, with no consumer.

None of this is wrong in itself. The provenance machinery in particular is what made findings 1
and 2 detectable. It is a question of sequencing: keep it, stop extending it, redirect the effort.

### 6. Structural limits, not defects

- Simulator has no Brazilian payroll mechanics: no 13º salário, férias with the 1/3 addition,
  INSS/IRRF deductions or any gross-versus-net distinction, FGTS, Bolsa Família or BPC, TED or
  boleto, salary on the fifth business day, split payment (adiantamento), overdraft. Salary day is
  a fixed `day_of_month`. Income kinds are six generic ones; every description is an English
  constant.
- No time dimension in any evaluation.
- No provider adapter and no Open Finance payload schema. Zero occurrences of `creditDebitType`,
  `transactionName` or `/accounts/v2` in code. Whether real field truncation destroys features is
  unknown.
- Runtime and training are stdlib plus pydantic; the gradient booster is hand-written. Good for
  the inference artifact; every modelling change is bespoke and training iteration is slow.
- No service surface beyond CLI and Streamlit; no logging, latency or monitoring evidence.
- `counterparty_name` is carried in plaintext when no document hash is present
  ([`normalize.py`](../estimator/src/income_estimator/preprocessing/normalize.py), line 44).
- 53 commits by one author in 26 days. The two written reviews are the only external eyes on
  the code. That velocity produced the fixes in one day; it also produced ADR 0009.

## What is worth keeping

Observation/private-label boundary; integer money; immutable contracts with explicit versions;
point-in-time slicing of every dated record; bundle digests and refusal to load mismatched pairs;
transaction-level explanations; assessment status as a biconditional with the qualified amount;
honest recording of failed experiments in ADRs. These transfer even if every coefficient is
discarded.

## Plan

Small reviewable changes with explicit stop conditions. Orders 1 and 2 come before any synthetic
metric is quoted externally again; they decide which of the current numbers survive. Nothing here
starts a rewrite.

| Order | Work | Exit condition | Estimate |
| --- | --- | --- | --- |
| 1 | **Done** — [ADR 0010](adr/0010-coverage-oracle-removed.md). Coverage oracle removed; contract `1.3` consent scopes; routing removed after losing on corrected inputs; recalibrated as `0.13.0` | No input field encodes post-cutoff or unconsented knowledge; routing and calibration re-decided on corrected inputs | 2–3 days |
| 2 | Ablations sizing generator inversion; regime holdout and time holdout added to the gate | Model card states attributable share; promotion report carries three holdout rows | 2 days |
| 3 | Randomize generator tells: payday jitter with business days, sampled descriptions with variants and truncation, wider amount noise, misdeclared coverage | Estimator code unchanged; realized WAPE nonzero; stress suites split to vary one factor each | 3 days |
| 4 | Regime-conditioned interval selector, conditioner ranked on the regime holdout | Per-suite out-of-distribution coverage reported and floored in the gate | 3–5 days |
| 5 | Provider adapter from Open Finance Brasil sandbox `accounts/v2` and `transactions/v2` payloads into input `1.2`, with first-observed timestamps and a dated evidence store | One real-shaped payload runs end to end; features lost to field shape are listed | 1 week |
| 6 | Brazilian payroll mechanics and Portuguese vocabulary in the simulator: 13º, férias, INSS net, fifth business day, adiantamento, benefits | Regression tests with independently specified expected values, not recorded output | 1 week |
| 7 | First real-data numbers: 50–100 volunteer or staff statements with declared income; two annotators reconstruct the ADR 0002 target from documents; report agreement; domain classifier on real-versus-simulated feature vectors, per-feature AUC | Repository carries real-data results labelled as such; ADR 0002 shown reproducible by humans or revised | 2–3 weeks, parallel |
| 8 | Bank shadow pilot per the underwriting assessment | Unchanged from that document | External dependency |

Order 7 can start alongside orders 1–4; it needs no bank and no code. Orders 5 and 6 can be
started by a second contributor. Order 8 depends on the partner and should not wait for order 7.

**Freeze until order 4 lands:** new calibration artifact schema versions, new routing rules,
explainability depth, new simulator engine versions. **Archive:** engine profiles `0.5.0` and
below, unless a consumer is named.

## Why the process did not catch this

The 8 September review listed five process gaps and they hold. One more sits under them: **the
generator and the estimator share an author, a vocabulary, and a set of derived quantities, and
nothing in the pipeline is adversarial to that.** Every scenario was written knowing what the
rules match; every coverage field was projected knowing what the reconstruction divides by; every
target was built from parameters the features can reconstruct. Customer-disjoint holdouts, digest
binding and a read-once lockbox are all protections against the model memorizing customers. None
protects against the model memorizing the generator. Order 2 adds the first such protection; order
7 is the only real one.

## Reproduction

```powershell
.\.venv\Scripts\python.exe -m pip install -c finances_simulator/constraints-dev.txt -e "finances_simulator[dev]" -e "estimator[dev]" -e "demo_app[dev]"
Push-Location estimator; ..\.venv\Scripts\python.exe -m pytest -q; Pop-Location
.\.venv\Scripts\python.exe -m pytest -c finances_simulator/pyproject.toml finances_simulator/tests -q
.\.venv\Scripts\python.exe -m pytest -c demo_app/pyproject.toml demo_app/tests -q
```

The oracle in finding 1 can be reproduced from the 8 September probe script: identical
transactions, change only `eligible_record_count` and `observed_original_record_count`, and watch
`cash_flow_last_month` move.
