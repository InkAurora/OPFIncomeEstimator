# Income estimator demo

A single-page Streamlit application that runs the simulator and the promoted estimator in one
process, so the whole flow can be shown without a build step, an API, a database, or a deployment.

## Quick start

From the repository root:

```bash
python -m pip install "streamlit>=1.40,<2"
```

```bash
python -m streamlit run demo_app/app.py
```

The page opens at <http://localhost:8501>. It needs both sibling packages importable; the usual
development install covers that:

```bash
python -m pip install -e finances_simulator -e estimator
```

## What it does

1. You pick a client profile, a seed, and a history length, then press one button.
2. The simulator generates a complete hidden financial life for one client.
3. Only the observable projection of that life is adapted into estimator input `1.2`.
4. The promoted pair answers: capacity model `capacity-gbdt-stumps-0.6.0` and interval calibration
   `conditional-selector-intervals-0.11.0`, under estimator `ensemble-0.6.0`.
5. The private income targets are projected **after** inference and joined on, so the page can show
   how far from the truth the estimate landed.

Every number is reproducible: the same profile, seed, and history length always produce identical
output.

## Profiles

| Profile | Scenario | History | Shows |
| --- | --- | --- | --- |
| Mixed-income professional | `income_diverse.yaml` | 12 or 24 | Several concurrent income streams |
| Salaried client with life events | `life_events.yaml` | 12 or 24 | Income through raises, job loss, job change |
| Salaried client with partial consent | `incomplete_observation.yaml` | 12 | Reconstruction from an incomplete feed |
| High-volatility entrepreneur | `high_volatility.yaml` | 12 | **Documented weak case:** unstable income |
| Noisy financial feed | `noisy_observation.yaml` | 12 | **Documented weak case:** confounding credits |

The last two are in the demo on purpose. They are the two stress suites the estimator does worst
on, and the page shows their interval coverage falling below its nominal 80% rather than hiding it.

### Why history length is restricted per profile

A scenario configures its income sources to cover `default_months`. Generating past that point does
not extend the client's working life: it runs off the end of every configured source. The private
sustainable target then decays toward zero while the estimator, which cannot see a source's end
date, keeps reporting the level it observed. The resulting error measures the stretched
configuration, not the estimator, so the demo only offers horizons a scenario actually configures.
`demo_app.profiles.supported_months` derives this from the scenario file, and a test holds it to
that.

## The trust boundary

`demo_app/service.py` runs four stages in a fixed order, and the signatures enforce it:

```text
generate_world(profile, seed, months)  ->  the hidden world
build_request(world)                   ->  the consented feed, and nothing else
run_inference(estimator, request)      ->  the request is the only argument
project_private_truth(world)           ->  after the estimate exists, for scoring only
```

`test_the_estimator_request_carries_no_private_field` serializes the request and asserts that no
private field name appears anywhere in it, at any nesting depth.

## Layout

| File | Responsibility |
| --- | --- |
| `profiles.py` | Business-facing profile registry and per-profile history lengths |
| `service.py` | Generation, artifact binding, inference, and the truth join |
| `formatting.py` | BRL money, percentages, and plain sentences for machine codes |
| `export.py` | The downloadable evidence JSON |
| `app.py` | Streamlit rendering only |
| `tests/` | Boundary, determinism, abstention, artifact-binding, and budget checks |

## Tests

```bash
python -m pytest demo_app/tests -q
```

## Not in scope

No provider integration, login, consent management, database, REST API, custom scenario editor,
model training, PDF reporting, or deployment pipeline. This proves integration, explainability,
determinism, and the trust boundary. It does not claim production readiness.
