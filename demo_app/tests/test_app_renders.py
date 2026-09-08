"""The Streamlit page renders, with the dependencies an install actually provides.

Every other test in this suite exercises the service beneath the page. CI installed neither the demo
distribution nor its rendering dependencies, so nothing checked that the page itself runs: a
rendering error reached a person before it reached a test. This is the missing smoke check, and it
is deliberately shallow. It asserts that the app starts, that it raises no exception, and that the
assessment status a lending reader depends on actually reaches the page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

APP = Path(__file__).parents[1] / "app.py"

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

GENERATE_LABEL = "Generate financial history"


def _run(*, generate: bool = True) -> AppTest:
    """Start the page, and press the button that actually produces a result."""

    app = AppTest.from_file(str(APP), default_timeout=900)
    app.run()
    if generate:
        button = next(item for item in app.button if item.label == GENERATE_LABEL)
        button.click().run()
    return app


def _rendered(app: AppTest) -> str:
    return "\n".join(
        [item.value for item in app.markdown]
        + [item.value for item in app.caption]
        + [str(item.label) for item in app.metric]
    )


def test_the_page_renders_before_anything_is_generated() -> None:
    app = _run(generate=False)

    assert not app.exception, [str(item) for item in app.exception]


def test_the_page_renders_a_generated_result() -> None:
    app = _run()

    assert not app.exception, [str(item) for item in app.exception]
    assert app.metric


def test_the_page_states_what_a_policy_may_use() -> None:
    """A number on the page without its standing beside it is the failure this guards."""

    rendered = _rendered(_run())

    assert "Sustainable income a policy may use" in rendered
    assert "assessment" in rendered.lower()
