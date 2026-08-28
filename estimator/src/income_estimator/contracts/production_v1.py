"""Production result contract 1.0: an estimate or explanation stamped with its bundle identity.

Output contract ``1.1`` and explanation contract ``1.0`` are frozen. They record which artifact
versions produced a result, which is not the same as recording *which bytes*: two bundles can name
``capacity-gbdt-stumps-0.6.0`` and disagree about what that is, and this repository has already had
one artifact rewritten in place under an unchanged version string.

Rather than add fields to a frozen contract, the production path wraps its result. The payload is
the unmodified ``1.1`` estimate or ``1.0`` explanation a consumer already knows how to read; the
envelope adds the identity of the bundle that produced it. Output contract ``1.2`` will carry the
remaining production fields, at which point this envelope may fold into it.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from income_estimator.contracts.explanation_v1 import EstimationExplanationV1
from income_estimator.contracts.output_v1_1 import IncomeEstimateV11

PRODUCTION_RESULT_CONTRACT_VERSION = "1.0"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProductionResultV1(BaseModel):
    """Exactly one result, and the bundle bytes that produced it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    bundle_contract_version: str = Field(min_length=1)
    bundle_id: str = Field(min_length=1)
    bundle_version: str = Field(min_length=1)
    bundle_digest: str = Field(min_length=64, max_length=64)
    estimator_package_version: str = Field(min_length=1)
    estimator_version: str = Field(min_length=1)
    feature_set_version: str = Field(min_length=1)
    model_versions: tuple[str, ...] = Field(min_length=1)
    estimate: IncomeEstimateV11 | None = None
    explanation: EstimationExplanationV1 | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if not _SHA256.match(self.bundle_digest):
            raise ValueError("bundle_digest must be 64 lowercase hexadecimal characters")
        if (self.estimate is None) == (self.explanation is None):
            raise ValueError("exactly one of estimate or explanation must be present")
        return self


__all__ = [
    "PRODUCTION_RESULT_CONTRACT_VERSION",
    "ProductionResultV1",
]
