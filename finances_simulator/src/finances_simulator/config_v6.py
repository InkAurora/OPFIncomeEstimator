"""Validated configuration contract for schema 1.6 corrected re-posts.

Schema 1.6 adds no configuration field. ADR 0004 makes the corrected re-post mandatory rather than
configurable, because a reversal without its correction is the defect the contract version exists to
remove, and a rate that could disable the correction would leave that defect reachable.
"""

from __future__ import annotations

from typing import Literal

from finances_simulator.config_v5 import ScenarioConfigV5


class ScenarioConfigV6(ScenarioConfigV5):
    """Complete validated configuration for contract schema 1.6."""

    schema_version: Literal["1.6"]


ScenarioConfigV1_6 = ScenarioConfigV6

__all__ = [
    "ScenarioConfigV1_6",
    "ScenarioConfigV6",
]
