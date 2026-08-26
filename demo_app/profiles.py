"""Business-facing names for the scenario configurations the demo exposes.

The registry deliberately hides raw YAML. A profile is a label, a description, a scenario file, a
default seed, and an honest note about what the profile is meant to prove. Nothing here is a new
scenario: every entry points at a configuration that already ships with the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIRECTORY = REPOSITORY_ROOT / "finances_simulator" / "configs" / "scenarios"


@dataclass(frozen=True, slots=True)
class Profile:
    """One selectable client profile."""

    key: str
    label: str
    scenario_file: str
    default_seed: int
    description: str
    demonstrates: str
    caveat: str | None = None

    @property
    def scenario_path(self) -> Path:
        return SCENARIO_DIRECTORY / self.scenario_file

    @property
    def is_limitation_profile(self) -> bool:
        """True when the profile exists to show where the estimator degrades."""

        return self.caveat is not None


PROFILES: tuple[Profile, ...] = (
    Profile(
        key="mixed_income_professional",
        label="Mixed-income professional",
        scenario_file="income_diverse.yaml",
        default_seed=1234,
        description=(
            "A client whose money arrives from more than one place: salary alongside "
            "self-employment, rent, or benefit income, paid on different schedules."
        ),
        demonstrates=(
            "Separating several concurrent income streams and reporting a single sustainable "
            "figure from them."
        ),
    ),
    Profile(
        key="salaried_life_events",
        label="Salaried client with life events",
        scenario_file="life_events.yaml",
        default_seed=1234,
        description=(
            "A salaried client whose circumstances change during the window: raises, a promotion, "
            "a job loss, and a job change all land inside the observed history."
        ),
        demonstrates=(
            "Tracking income through discontinuities instead of averaging across them."
        ),
    ),
    Profile(
        key="salaried_partial_consent",
        label="Salaried client with partial consent",
        scenario_file="incomplete_observation.yaml",
        default_seed=1234,
        description=(
            "A salaried client who consented to only part of their banking data, so whole months "
            "of some accounts are missing from the feed."
        ),
        demonstrates=(
            "Reconstructing a recurring salary from an incomplete feed, and saying how much of "
            "the picture was actually visible."
        ),
    ),
    Profile(
        key="high_volatility_entrepreneur",
        label="High-volatility entrepreneur",
        scenario_file="high_volatility.yaml",
        default_seed=1234,
        description=(
            "A self-employed client paid irregularly and in widely varying amounts, with no "
            "observation problems at all: the instability is the income itself."
        ),
        demonstrates=(
            "The documented weak case. Realized income stays accurate; the sustainable figure and "
            "its interval do not."
        ),
        caveat=(
            "Known limitation. On this profile the sustainable-income interval covers the truth "
            "far less often than its nominal 80%. Treat the sustainable number as indicative only."
        ),
    ),
    Profile(
        key="noisy_financial_feed",
        label="Noisy financial feed",
        scenario_file="noisy_observation.yaml",
        default_seed=1234,
        description=(
            "Stable income arriving through a messy feed: duplicated and reversed records, late "
            "arrivals, and credits that look like income but are refunds, transfers, or asset "
            "sales."
        ),
        demonstrates=(
            "The second documented weak case. Non-income credits are mostly excluded correctly, "
            "but the sustainable interval is too narrow."
        ),
        caveat=(
            "Known limitation. Interval coverage on this profile falls below its nominal 80%, so "
            "the published P10-P90 band is narrower than the evidence supports."
        ),
    ),
)

PROFILES_BY_KEY: dict[str, Profile] = {profile.key: profile for profile in PROFILES}


def get_profile(key: str) -> Profile:
    """Return one registered profile.

    Raises:
        KeyError: If no profile carries that key.
    """

    try:
        return PROFILES_BY_KEY[key]
    except KeyError:
        raise KeyError(
            f"unknown profile '{key}'; expected one of {sorted(PROFILES_BY_KEY)}"
        ) from None


@cache
def supported_months(key: str) -> tuple[int, ...]:
    """Return the history lengths this profile can be generated at, longest last.

    A scenario configures its income sources to cover ``default_months``. Generating past that
    point does not extend the client's working life: it runs off the end of every configured
    source, so the private sustainable target decays toward zero while the estimator, which cannot
    see a source's end date, keeps reporting the level it observed. The resulting error says
    nothing about the estimator, so the demo does not offer a horizon a scenario cannot honor.

    Shorter windows are always safe; they truncate the window without ending any source.
    """

    from finances_simulator.config import load_scenario_config

    configured = load_scenario_config(get_profile(key).scenario_path).scenario.default_months
    return tuple(months for months in (12, 24) if months <= configured)


__all__ = [
    "PROFILES",
    "PROFILES_BY_KEY",
    "SCENARIO_DIRECTORY",
    "Profile",
    "get_profile",
    "supported_months",
]
