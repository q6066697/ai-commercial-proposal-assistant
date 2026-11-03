"""
Pydantic model of schemas/tender_requirements.schema.json -- the JSON schema
file is the contract, tests/test_tender_models.py checks the two agree.

Some fields are filled only by code, never by the LLM: evidence.verified /
evidence.match (grounding check) and tender.source_docs (the loaded file
names). They are marked CODE_ONLY and stripped from the schema the LLM gets
for structured output (llm_response_schema()), so the model cannot claim its
own quotes are verified.
"""
from __future__ import annotations

import copy
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

CODE_ONLY = {"code_only": True}

Category = Literal[
    "license", "experience", "personnel_admission", "medical", "equipment", "ppe",
    "insurance", "site_visit", "safety", "materials", "schedule", "documents",
    "confidentiality", "other",
]
MatchStatus = Literal["verified", "wrong_doc", "not_found", "too_short", "value_mismatch"]


def _drop_none(v) -> bool:
    return v is None


class Evidence(BaseModel):
    quote: str = Field(description="Дословная цитата из документа, без пересказа")
    doc: str = Field(description="Имя файла из списка документов тендера")
    verified: Optional[bool] = Field(None, exclude_if=_drop_none, json_schema_extra=CODE_ONLY)
    match: Optional[MatchStatus] = Field(None, exclude_if=_drop_none, json_schema_extra=CODE_ONLY)


class TextFact(BaseModel):
    value: Optional[str]
    evidence: Evidence


class TenderInfo(BaseModel):
    customer: TextFact
    subject: TextFact
    location: Optional[TextFact] = Field(None, exclude_if=_drop_none)
    country: Optional[Literal["BY", "RU", "UZ", "other"]] = Field(None, exclude_if=_drop_none)
    source_docs: list[str] = Field(default_factory=list, json_schema_extra=CODE_ONLY)

    @field_validator("country", mode="before")
    @classmethod
    def _country_as_plain_value(cls, v):
        # Models tend to wrap it like the sibling facts: {"value": "RU", "evidence": ...}
        return v.get("value") if isinstance(v, dict) else v


class ScopeItem(BaseModel):
    service_id: Optional[str] = Field(description="id услуги из прайса; null, если такой услуги в прайсе нет")
    description: str
    surface: Optional[str] = None
    side: Optional[Literal["outer", "inner"]] = None
    building: Optional[str] = None
    volume: Optional[float] = Field(description="Объём одной строки; не итог по нескольким строкам")
    unit: Optional[Literal["m2", "lm", "pcs"]]
    repeats: int = Field(1, ge=1, description="Сколько раз эта строка выполняется за срок договора")
    period: Optional[str] = None
    evidence: Evidence


class Height(BaseModel):
    max_height_m: Optional[float] = None
    floors: Optional[int] = None
    evidence: Optional[Evidence] = Field(None, exclude_if=_drop_none)


class Schedule(BaseModel):
    start: Optional[str] = Field(None, description="YYYY-MM-DD")
    end: Optional[str] = Field(None, description="YYYY-MM-DD")
    duration_days: Optional[int] = None
    mode: Literal["fixed_dates", "on_request", "periodic"]
    response_time_days: Optional[int] = None
    working_hours: Optional[str] = None
    contract_term: Optional[str] = None
    evidence: list[Evidence] = Field(default_factory=list)


class Requirement(BaseModel):
    text: str
    category: Category
    mandatory: bool
    cost_impact: Optional[Literal["none", "low", "high"]] = Field(None, exclude_if=_drop_none)
    evidence: Evidence


class Commercial(BaseModel):
    price_includes: list[str] = Field(default_factory=list)
    advance: Optional[bool] = None
    payment_days: Optional[int] = None
    payment_basis: Optional[str] = None
    warranty: Optional[str] = None
    penalties: Optional[str] = None
    liability: Optional[str] = None
    evidence: list[Evidence] = Field(default_factory=list)


class Acceptance(BaseModel):
    documents: list[str] = Field(default_factory=list)
    review_days: Optional[int] = None
    evidence: list[Evidence] = Field(default_factory=list)


class Gap(BaseModel):
    field: str
    issue: Literal["missing", "contradiction", "blank_in_template", "ambiguous"]
    question_to_customer: str


class Flag(BaseModel):
    text: str
    evidence: Evidence


class TenderRequirements(BaseModel):
    tender: TenderInfo
    scope: list[ScopeItem]
    height: Optional[Height] = Field(None, exclude_if=_drop_none)
    schedule: Schedule
    requirements: list[Requirement]
    commercial: Optional[Commercial] = Field(None, exclude_if=_drop_none)
    acceptance: Optional[Acceptance] = Field(None, exclude_if=_drop_none)
    gaps: list[Gap]
    flags: list[Flag]

    def to_json_dict(self) -> dict:
        """Dump that validates against the JSON schema file: optional objects
        the schema doesn't allow as null are omitted, not written as null."""
        return self.model_dump(mode="json")

    def all_evidence(self) -> list[tuple[str, Evidence]]:
        """Every evidence object with a readable path, for the grounding check."""
        out: list[tuple[str, Evidence]] = [
            ("tender.customer", self.tender.customer.evidence),
            ("tender.subject", self.tender.subject.evidence),
        ]
        if self.tender.location:
            out.append(("tender.location", self.tender.location.evidence))
        out += [(f"scope[{i}]", s.evidence) for i, s in enumerate(self.scope)]
        if self.height and self.height.evidence:
            out.append(("height", self.height.evidence))
        out += [(f"schedule.evidence[{i}]", e) for i, e in enumerate(self.schedule.evidence)]
        out += [(f"requirements[{i}]", r.evidence) for i, r in enumerate(self.requirements)]
        if self.commercial:
            out += [(f"commercial.evidence[{i}]", e) for i, e in enumerate(self.commercial.evidence)]
        if self.acceptance:
            out += [(f"acceptance.evidence[{i}]", e) for i, e in enumerate(self.acceptance.evidence)]
        out += [(f"flags[{i}]", f.evidence) for i, f in enumerate(self.flags)]
        return out


def llm_response_schema() -> dict:
    """JSON schema for structured output: code-only fields removed, every
    property required (nullable where optional), no extra properties and no
    defaults -- what strict structured-output modes expect."""
    schema = copy.deepcopy(TenderRequirements.model_json_schema())

    def strip(node):
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                for name in [n for n, p in props.items() if p.get("code_only")]:
                    del props[name]
                node["required"] = list(props)
                node["additionalProperties"] = False
            for key in ("default", "code_only", "title"):
                node.pop(key, None)
            for value in node.values():
                strip(value)
        elif isinstance(node, list):
            for item in node:
                strip(item)

    strip(schema)
    return schema
