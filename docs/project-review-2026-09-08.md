# Project review — 8 September 2026

Baseline: `4ee69ca`. Goal interpreted from the README and the owner-confirmed use case in [the bank underwriting assessment](bank-underwriting-assessment.md): turn consented Open Finance data into explainable income evidence useful for Brazilian retail underwriting and credit limits.

**The main problem is that progress is measured mostly against the simulator, while the intended product must establish usable evidence for a bank. There are also reproducible correctness and release-control defects that invalidate some current claims.** Preserve the observation boundary, explanations, deterministic simulator, and artifact packaging. Make the next milestone an evidence-qualified assessment of a real consented case under one bank's policy.

The earlier assessment remains directionally correct. This review adds newly reproduced defects: quarterly receipts inflated by imputation, an ensemble that fails its current benchmark, acceptance of failed release evidence, inconsistent request windows and dates, a Windows fixture failure, and a demo packaging failure. Findings below separate measured defects from product gaps. Runtime and promoted model files were not changed.

## Evidence and review scope

Reviewed architecture and target ADRs; simulator generation, observation projection and integration; estimator contracts, normalization, classification, streams, feature construction, models and explanations; training/evaluation boundaries; release assembly/loading and CI; demo service and rendering. Executed existing suites, release checks, additional synthetic probes, fresh stress and ensemble evaluations, and headless Streamlit interaction.

All numerical accuracy results remain synthetic. No provider credentials, real customer data, lending outcomes, new training run, or new lockbox evaluation were used. Headless UI checks exercise rendering and interaction; they are not a visual browser inspection. CI's Ubuntu/Python 3.13 environment was not reproduced: this review ran on Windows/Python 3.14.7 with the repository's pinned core development dependencies.

| Check | Current result |
| --- | --- |
| Estimator suite | 197 passed, 1 failed, 4 initially skipped |
| Simulator suite | 320 passed, 1 failed |
| Demo service suite | 31 passed |
| Opt-in clean-wheel suite | All 4 subsequently passed; none skipped |
| Ruff across all three components | Passed |
| Documented estimator CLI examples | 12 of 12 passed |
| Streamlit interaction | All 5 profiles, all 7 supported profile/history combinations rendered results without app exceptions |
| Current ensemble benchmark | `NOT_PROMOTED`; routing worsens MAE |
| Current stress evaluation | Reproduces severe noisy/volatile interval undercoverage |
| Editable demo installation | Failed during setuptools package discovery |

Unique existing test outcomes: **552 passed, 2 failed** after enabling the four installation tests. Passing software tests do not validate bank suitability.

[Machine-readable evidence](review-evidence-2026-09-08.json) records versions, probe outputs and regenerated metrics. [The probe script](review-probes-2026-09-08.py) reproduces the small correctness examples without modifying runtime or model files.

## Findings and specific remedies

### 1. P1 — The shipped routing rule no longer earns its place

The committed ensemble report evaluates capacity `0.5.0`, digest `f58f13d5…`. The bundle ships capacity `0.6.0`, digest `4ee9c3a6…`. Running the existing benchmark against its current default, on the same seed ranges and 80 customers per suite, produces:

| Configuration | MAE, BRL per customer-month |
| --- | ---: |
| Current capacity model alone | R$149.11 |
| Current routed ensemble | R$160.64 |
| Increase caused by routing | 7.73% |

The comparison contains 26 held-out customers / 312 rows. It is a regression on this benchmark, not an estimate of real-client performance. The previously quoted R$212.27 versus R$232.36 improvement does not describe the shipped model. The current benchmark reports `NOT_PROMOTED`, yet its CLI exits successfully.

Evidence: [old ensemble report](../estimator/evaluation/baselines/ensemble-0.6.0-report.json), [routing](../estimator/src/income_estimator/models/ensemble.py), lines 106–130, and [benchmark](../estimator/evaluation/ensemble_benchmark.py), lines 114–149 and 190–194.

**Fix:** mark the old metric as historical, bind routing evaluation to the exact current pipeline, and compare revised routing with capacity alone. Add a gate mode that returns a nonzero status on failure and make release CI run it. Changing routing changes the residual being calibrated; refit/revalidate intervals together with any routing change. Do not simply remove routing and retain the old calibration claim.

**Exit:** the exact candidate pipeline meets the declared point-error and interval gates, with all reports identifying its artifacts and implementation versions. Reserve fresh confirmation data after selecting the change.

### 2. P1 — Quarterly income is fabricated in nonpayment months

A synthetic stream pays R$9,000 in January, April and July. With 90% declared account coverage, the detector correctly calls it `QUARTERLY`. Reconstruction still fills February, March, May and June with R$9,000 each. **R$27,000 observed becomes R$63,000 reconstructed.**

The eligibility test accepts recurring sources of any cadence, then imputes every unobserved month within their active span. `expected_monthly_amount_minor` is the median paying-month amount. The newer frequency-normalized capacity feature does not repair this realized-income path.

Evidence: [gap reconstruction](../estimator/src/income_estimator/models/recurring.py), lines 76–85 and 100–104; [stream detection](../estimator/src/income_estimator/income_streams/detector.py), lines 150–163.

**Fix:** infer expected payment opportunities from cadence and phase. Impute a missing payment only when it was due and feed evidence supports a missing observation. Keep realized receipts separate from frequency-normalized sustainable rates. If cadence is uncertain, withhold imputation instead of assuming monthly payments.

**Exit:** quarterly nonpayment months stay zero; a genuinely missing quarterly payment can be treated separately; monthly, biweekly and irregular examples preserve their intended timing. Include mixed cadence plus partial coverage in regression evaluation.

### 3. P1 — Coincident debits erase valid income

A R$5,000 salary is counted. Add an unrelated R$5,000 rent debit on another observed account on the same date: salary becomes zero, excluded as `VISIBLE_OWN_TRANSFER_PAIR`. Neither ownership linkage nor counterparty identity is required.

Evidence: [transfer feature](../estimator/src/income_estimator/transaction_intelligence/features.py), lines 120–127; [classification precedence](../estimator/src/income_estimator/transaction_intelligence/rules.py), lines 71–72.

**Fix:** require affirmative linkage between both sides of an internal transfer. Scope matches to ownership/counterparty evidence and enforce sensible one-to-one matching. Same amount and date should contribute evidence, not establish a transfer by themselves. Handle salary portability as an economic flow whose income origin remains traceable.

**Exit:** unrelated debits cannot remove income; genuine internal transfers are counted at most once; uncertain matches remain visible for review. Include duplicates and multiple equal amounts on the same day.

### 4. P1 — Familiar simulator descriptions substitute for Brazilian payment evidence

Changing only a R$5,000 credit's description from `SALARY` to `SALÁRIO` or `FOLHA DE PAGAMENTO` changes realized income from R$5,000 to zero. The latter credits become ambiguous. Repeated unfamiliar descriptions cannot establish streams because clustering receives only credits already classified as income.

Ambiguity is appropriate for a generic PIX receipt; presenting unestablished income as an ordinary zero is the downstream problem. Conversely, English keyword recognition alone does not establish that a receipt is income.

Evidence: [keyword rules](../estimator/src/income_estimator/transaction_intelligence/rules.py), lines 25–40 and 82–102; [stream admission](../estimator/src/income_estimator/income_streams/detector.py), lines 112–116; [synthetic descriptions](../finances_simulator/configs/scenarios/income_diverse.yaml).

**Fix:** build one provider adapter that preserves transaction type, status, counterparty evidence, account identity, timestamps and lifecycle changes. Add reviewed Portuguese payroll cases immediately, then evaluate classification on independently labeled Brazilian examples. Let recurrence contribute to the assessment of ambiguous credits, with safeguards against recurring transfers and other non-income receipts.

Open Finance Brasil documents counterparty information for PIX and payroll with exceptions, and transaction identifiers/statuses that can change. The adapter must preserve these distinctions; a universal assumption that counterparty data is always present would also be wrong. [Official account guidance](https://openfinancebrasil.atlassian.net/wiki/spaces/OF/pages/1739096252/Orienta%2Bes%2B-%2BDC%2BContas).

**Exit:** replay reviewed provider examples and measure both missed income and false income by payment/source type. Unknown classifications require evidence resolution rather than silently becoming an accepted zero-income determination.

### 5. P1 — Unsupported cases still expose actionable-looking point estimates

A valid 12-month request with two accounts and **no transactions** returns December sustainable income of **R$4,319.65**, confidence zero, and no interval (`OUT_OF_CALIBRATED_SUPPORT`). The point survives because support checking occurs after candidate selection and affects only interval publication.

Fresh evaluation of the exact promoted pair reproduces:

| Suite | Published intervals | Coverage | Sustainable WAPE |
| --- | ---: | ---: | ---: |
| Normal income diversity | 240 / 240 | 75.83% | 12.66% |
| Partial consent | 240 / 240 | 95.83% | 0.66% |
| Life events | 240 / 240 | 97.50% | 2.15% |
| Noisy feed | 224 / 240 | 34.82% | 3.03% |
| High volatility | 234 / 240 | 15.81% | 41.04% |

Nominal coverage is 80%; each suite contains 20 customers. These are diagnostic results. The clean legacy suite cannot evaluate sustainable income because its simulator contract has no target. Confidence in the noisy suite averages 74.42%, which does not warn reliably of its interval failure.

Evidence: [publication logic](../estimator/src/income_estimator/models/ensemble.py), lines 225–254; [support envelope](../estimator/src/income_estimator/models/uncertainty.py), lines 181–224. Nine independent ranges, with missing values accepted, cannot establish support for unfamiliar combinations.

**Fix:** introduce a versioned assessment status such as `SUPPORTED`, `REVIEW_REQUIRED`, or `INSUFFICIENT_EVIDENCE`, with eligible uses and reasons. Empty history must not supply a policy-qualified income. Evaluate missingness, freshness, classification ambiguity and joint support. Keep raw research estimates separate from amounts accepted by policy. Treat confidence as evidence quality until an outcome-probability interpretation is measured.

**Exit:** empty/unsupported evidence cannot silently qualify a lending input. Measure harmful overestimation and interval error together with eligibility/publication rate, by relevant segment. Predeclare thresholds with the bank; avoid passing by rejecting almost everyone.

### 6. P1 — Release assembly validates bytes but not a successful promotion

The builder checks capacity/calibration compatibility, then copies evidence reports without validating their contents. In a temporary directory, this review substituted a release report declaring `RELEASE_REJECTED`, a nonempty failure list, and an all-zero artifact digest. **Both bundle assembly and `ProductionIncomeEstimator.from_bundle` accepted it.** Committed bundle evidence was not changed; the probe demonstrates an unenforced gate, not a claim that the existing release report is forged.

Evidence: [builder](../estimator/release/build_bundle.py), lines 88–141 and 183–190. The [provenance contract](../estimator/src/income_estimator/contracts/bundle_v1.py), lines 65–83, explicitly makes the loader an integrity verifier. Therefore promotion validation must be enforced before assembly or by a mandatory release stage.

**Fix:** validate report schemas, success statuses, failure lists, exact model/calibration digests and required benchmark scope before writing a bundle. Bind the complete prediction pipeline, including routing and feature/rule versions. Require the current routing benchmark alongside interval evidence. Preserve the original promotion report separately when it intentionally describes pre-envelope bytes; require the release report to describe the final bytes.

Also fix clean-install tests: [the fixture](../estimator/tests/test_clean_install.py), lines 61–75 and 87–88, calls `pytest.skip` when wheel construction or installation fails. Once the explicit CI gate is enabled, those failures must fail the job. The four installation tests passed here, so this is a control gap rather than an observed wheel defect.

**Exit:** rejected, stale or missing promotion evidence cannot produce an approved release; enabled build/install failures cannot yield green CI through skips.

### 7. P1 for real-data validation — Coverage contains retrospective knowledge

`eligible_record_count` and `observed_original_record_count` describe the simulator's full generated window. The adapter copies them into the request; monthly slicing retains them unchanged; coverage features use their ratio and eligible-record weights. In a controlled request with identical transactions, changing only these undated aggregates changes January's effective coverage from 100% to 50% and completeness from 44.58% to 32.08%.

The raw observation/private-label separation is useful, but an allowlisted field can still contain unavailable knowledge. Full-window observation totals are not necessarily known at an earlier monthly cutoff. Nor can a real receiver generally count transactions hidden across unconsented institutions. Product records separately lack arrival timestamps, a documented contract limitation.

Evidence: [observation coverage](../finances_simulator/src/finances_simulator/observations/projector_v5.py), lines 335–359; [adapter](../finances_simulator/src/finances_simulator/integration/adapter.py), lines 108–121; [slicing](../estimator/src/income_estimator/features/point_in_time.py), lines 60–88; [coverage features](../estimator/src/income_estimator/features/coverage.py), lines 25–44; [product contract](../estimator/src/income_estimator/contracts/v1_2.py), lines 9–11.

**Fix:** separate known consent scope, fetched account/date ranges, pagination completion, first-observed time and unknown external coverage. Use versioned metadata as it existed at the cutoff. Keep simulator-only completeness diagnostics in evaluation. Rerun model and calibration evaluations with receiver-available information.

**Exit:** adding later transactions or later metadata updates cannot change an earlier as-of assessment. Missing institutions stay unknown; they do not authorize multiplying observed income upward. The magnitude of current metric optimism from these fields remains unmeasured.

### 8. P2 — Validated dates and windows can silently corrupt outputs

Two probes pass the current input validator:

- A January-only window with `months=2` returns both January and February, including **R$5,163.10 sustainable income for February**, beyond the observation window. Reconstruction follows `months`; feature construction stops at `window_end`; the missing feature row is replaced with `{}` and scored.
- A transaction dated `20260105` passes `date.fromisoformat` validation but becomes zero income. Downstream code compares date strings and slices `[:7]`, assuming canonical `YYYY-MM-DD`.

Evidence: [input validation](../estimator/src/income_estimator/contracts/v1.py), lines 14–18 and 96–111; [normalization](../estimator/src/income_estimator/preprocessing/normalize.py), lines 54–74; [monthly assembly](../estimator/src/income_estimator/pipeline.py), lines 253–259.

**Fix:** normalize or reject noncanonical dates before storing them. Require `months` to equal the number of calendar months spanned by the window, or derive it. Assert one feature row per output month; never score a silently missing row. Preserve explicit partial-month semantics.

**Exit:** inconsistent requests fail clearly, accepted date representations produce identical results, and no historical output is emitted outside the accepted window.

### 9. P2 — Reproducibility and demo installation have concrete gaps

- **Simulator reference:** Git contains only two JSON files for the Phase-7 reference population, while generation produces those plus ten Parquet files. `test_phase7_reference_population_is_byte_stable` necessarily fails. Root `.gitignore` excludes `*.parquet`. Restore narrowly allowlisted synthetic fixtures, or commit independently reviewed expected file hashes with the writer/environment contract. Never recreate expected output automatically inside the assertion.
- **Estimator fixture:** `.gitattributes` pins bundle and training JSON to LF but omits `estimator/tests/fixtures/*.json`. This Windows checkout has CRLF fixtures; `test_committed_fixtures_are_what_the_recorder_produces` compares them with LF output. Pin these fixtures and normalize deliberately; do not change the expected financial values.
- **Demo package:** `pip install -e demo_app` fails with `Multiple top-level modules discovered in a flat-layout`. Declare the intended package layout, then test installation. The documented source-run workflow does work; this failure concerns the distribution declared by `demo_app/pyproject.toml`.
- **UI coverage:** CI installs neither the demo distribution nor its rendering dependencies; its demo tests exercise the service. Add one installed-dependency AppTest smoke check. All seven supported combinations passed manually here. Streamlit logged recoverable mixed-type Arrow conversion warnings; those are cleanup, not a release blocker.

Evidence: [simulator assertion](../finances_simulator/tests/test_scale_and_estimator_integration.py), line 206; [attributes](../.gitattributes); [fixture assertion](../estimator/tests/test_release.py), line 170; [demo packaging](../demo_app/pyproject.toml); [CI](../.github/workflows/ci.yml).

### 10. Product blocker — Sustainable income does not define repayment capacity

The synthetic sustainable target is the expected rate from sources active at the cutoff. It excludes post-cutoff life events; it is not a forecast of all cash-flow risk over a loan term. A service/business receipt is not automatically personal disposable income. Existing card, loan and investment features do not establish essential spending, net earnings, debt-service headroom or a credit policy.

The field `sustainable_income_p50_minor` also contains a routed point prediction. The capacity model fits squared error on a log target and routing can choose the latest cash flow. Neither establishes that the result is a conditional median. Realized `confidence_lower_minor` and `confidence_upper_minor` remain heuristic bands.

Evidence: [target ADR](adr/0002-income-target-construction.md), lines 74–109; [regression loss](../estimator/training/capacity_boosting.py), lines 256–275; [output assembly](../estimator/src/income_estimator/pipeline.py), lines 271–287; [realized bands](../estimator/src/income_estimator/models/recurring.py), lines 114–137.

**Fix:** agree with one bank on recognized receipts, accepted recurring personal income, and available cash after obligations and essential spending. Preserve gross/net/unknown interpretation. Publish point estimates and calibrated quantiles under different fields in a new contract. Add a separate, versioned policy layer for payment/exposure proposals; retain human review and recorded overrides.

This direction is consistent with Basel credit-risk guidance concerning repayment sources, current repayment capacity, future cash flows under scenarios and independent assessment. It is an architectural recommendation, not a conclusion that the project meets supervisory requirements. [BIS credit-risk principles](https://www.bis.org/committees/bcbs/basel-consolidated-guidelines/module/cri/10).

**Exit:** a bank analyst can explain which income was accepted, which obligations were reconciled, why a case needs more evidence, and how the result changes the bank's existing decision under explicit policy assumptions.

## Why the current process misses these problems

1. **Customer holdouts still share scenario design.** Three training families reuse description templates, source schedules and product structures. New seeds test variations within that generator. They do not establish provider or income-regime generalization.
2. **Important combinations are absent.** Quarterly income and partial coverage each have coverage in the project, yet their interaction fabricates receipts. Add semantic transformations and crossed conditions: translated descriptions, unrelated equal debits, missing domains, different cadences, late metadata and future extensions.
3. **Frozen output checks prove stability more readily than correctness.** They are worth retaining, but need independent economic expectations. The expected answer to a quarterly nonpayment month should not be derived from the implementation being tested.
4. **Release evidence can outlive its pipeline.** Digest checks protect files; they do not automatically prove that the current complete pipeline passed its gates. The failed routing benchmark is the concrete consequence.
5. **The bank feedback loop is missing.** No real-data accuracy, analyst utility or lending-outcome evidence appears in the repository. More synthetic model versions cannot settle these questions.

## Delivery sequence toward the goal

Use small reviewable changes with explicit stop conditions. Do not begin a broad rewrite.

| Order | Work | Acceptance condition |
| --- | --- | --- |
| 1 | Restore fixture reliability and enforce release evidence/status checks | Fresh checkout passes supported-environment tests; deliberately failed/stale evidence blocks assembly |
| 2 | Fix transfer matching, cadence-aware reconstruction and input validation | Controlled reproductions become regression tests with independently specified expected behavior |
| 3 | Add evidence eligibility and honest point/interval fields in a new contract | Empty, ambiguous and unsupported cases cannot supply a policy-qualified amount; old consumers have an explicit adapter |
| 4 | Reevaluate the full changed pipeline | Current component comparison, crossed-condition stress results and interval evidence agree on exact versions; recalibration follows upstream changes |
| 5 | Integrate one provider and obtain one independently reviewed label set | Reproducible consented cases retain lifecycle/provenance; classification and completeness use receiver-available information |
| 6 | Add reconciled expenses/obligations and one bank-policy shadow workflow | Analyst can inspect accepted income, payment headroom, binding assumptions and overrides beside the incumbent assessment |
| 7 | Independently validate before wider use | Customer, time and provider/segment holdouts meet predeclared error, eligibility, operational and bank-value criteria |

Work on bank access, use-case agreement and labels can start alongside orders 1–4. Do not wait for another synthetic milestone to begin that dependency. Model changes and calibration must remain coordinated; a rule fix can alter every downstream feature even when model bytes stay unchanged.

For the first pilot, select one lending product and applicant cohort. Compare the bank's current information with the same information plus the new Open Finance assessment. Separately compare simple recurring-income rules with the learned model, so improvements from additional data are not mistaken for benefits from model complexity. Measure large overestimation, error among eligible cases, eligibility/review burden, feed usability, analyst time and policy changes. Lending loss/utilization conclusions need observed outcomes over the relevant horizon; approved-only histories do not reveal outcomes for all rejected applicants.

Keep deterministic generation, the private-label boundary, integer money, explanation traces, artifact digests and the working single-process demo. Defer more simulator-version expansion, a larger model, microservices, and automatic credit-limit changes until the evidence supports their value. Recast the simulator as a regression and failure-injection tool informed by provider cases.

**Next meaningful milestone:** one replayable consented applicant assessment that returns accepted income or insufficient evidence, reconciles existing obligations, and shows its effect on an explicit bank policy beside the incumbent result.

## Reproduction

Install the two development packages with `finances_simulator/constraints-dev.txt`, then run from the repository root:

```powershell
python docs/review-probes-2026-09-08.py
python -m pytest -c estimator/pyproject.toml estimator/tests -q
python -m pytest -c finances_simulator/pyproject.toml finances_simulator/tests -q
python -m pytest -c demo_app/pyproject.toml demo_app/tests -q
```

From `estimator/`, write fresh evaluation results outside frozen artifact directories:

```powershell
python -m evaluation.ensemble_benchmark --output ../output/review-2026-09-08 --population-size-per-suite 80 --months 12 --workers 1
python -m evaluation.stress_report --output ../output/review-2026-09-08 --population-size 20 --months 12 --workers 1
python -m release.check_documented_cli
$env:INCOME_ESTIMATOR_CLEAN_INSTALL_TEST = '1'
python -m pytest tests/test_clean_install.py -q -rs
```

The review did not reread the reserved release lockbox or overwrite committed benchmark reports. New benchmark results and UI diagnostics are under the ignored `output/review-2026-09-08/` directory; the compact evidence JSON beside this review preserves the findings for version control.
