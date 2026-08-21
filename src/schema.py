"""
Shared structured-output schema for extraction.

Using Pydantic here (rather than a plain dataclass) is deliberate: this is
the schema an LLM structured-output / tool-use call validates against in
llm_extractor.py, and the same validation should apply to the rule-based
extractor's output too, so both paths are held to the same contract.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

ServiceKey = Literal["facade_cleaning", "metal_structures_painting", "seam_sealing"]
Urgency = Literal["standard", "urgent_within_week"]


class ExtractedRequest(BaseModel):
    service: Optional[ServiceKey] = None
    quantity: Optional[float] = Field(default=None, ge=0)
    unit: Optional[Literal["m2", "m"]] = None
    height_m: Optional[float] = Field(default=None, ge=0, le=500)
    location: Optional[str] = None
    urgency: Urgency = "standard"
    raw_text: str
    missing_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _compute_missing_fields(self) -> "ExtractedRequest":
        """Recomputed automatically so callers can't forget to set it, and so
        it always agrees with whatever the extractor actually filled in —
        this is the "validation" layer the calculator's warnings rely on."""
        missing = []
        if self.service is None:
            missing.append("service")
        if self.quantity is None:
            missing.append("quantity")
        if self.height_m is None:
            missing.append("height_m")
        if self.location is None:
            missing.append("location")
        object.__setattr__(self, "missing_fields", missing)
        return self

    def to_dict(self) -> dict:
        return self.model_dump()
