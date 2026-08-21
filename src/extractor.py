"""
Extraction layer: turns a free-form customer request (RU/EN mixed) into
structured project parameters.

Two implementations are provided:

- `extract_rule_based()` — a regex-based extractor that ships with this repo
  so the demo runs end-to-end with zero external dependencies / API keys.
- `extract_with_llm()` — a stub showing where an LLM call (Claude/GPT with
  structured output / tool use) would replace the regex layer in production.
  This is the intended real-world implementation; the rule-based one exists
  purely so reviewers can run the demo without configuring credentials.

Both return the same schema, so `main.py` can swap one for the other without
touching the rest of the pipeline.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict
from typing import Optional

SERVICE_KEYWORDS = {
    "facade_cleaning": ["мойка фасада", "мойка фасадов", "facade cleaning", "чистка фасада"],
    "metal_structures_painting": ["покраска металлоконструкц", "окраска металлоконструкц", "metal structure"],
    "seam_sealing": ["герметизация шв", "герметизация межпанельных", "seam sealing", "заделка шв"],
}

CITY_PATTERN = re.compile(r"(?:г\.|город)\s*([А-ЯЁ][а-яё]+)", re.IGNORECASE)
AREA_PATTERN = re.compile(r"([\d\s]{1,7})\s*(?:м2|м²|кв\.?\s*м)", re.IGNORECASE)
LENGTH_PATTERN = re.compile(r"([\d\s]{1,7})\s*(?:м\.п\.|погонн)", re.IGNORECASE)
HEIGHT_PATTERN = re.compile(r"высот[а-я]*[^\d]{0,25}?(\d{1,3})\s*(?:м(?:етр)?)", re.IGNORECASE)
URGENCY_PATTERN = re.compile(r"(в течение недели|срочно(?!сть)|как можно быстрее)", re.IGNORECASE)


@dataclass
class ExtractedRequest:
    service: Optional[str]
    quantity: Optional[float]
    unit: Optional[str]  # "m2" or "m"
    height_m: Optional[float]
    location: Optional[str]
    urgency: str  # "standard" | "urgent_within_week"
    raw_text: str

    def to_dict(self) -> dict:
        return asdict(self)


def _detect_service(text: str) -> tuple[Optional[str], Optional[str]]:
    lowered = text.lower()
    for service_key, keywords in SERVICE_KEYWORDS.items():
        for kw in keywords:
            if kw in lowered:
                unit = "m" if service_key == "seam_sealing" else "m2"
                return service_key, unit
    return None, None


def _to_number(raw: str) -> float:
    return float(raw.replace(" ", "").replace(",", "."))


def extract_rule_based(text: str) -> ExtractedRequest:
    """Demo-only extractor. Good enough for the three sample requests in
    data/demo_requests/, NOT a substitute for an LLM extractor on arbitrary
    free-form input — real customer emails are far messier than this."""
    service, unit = _detect_service(text)

    quantity = None
    if unit == "m2":
        m = AREA_PATTERN.search(text)
        if m:
            quantity = _to_number(m.group(1))
    elif unit == "m":
        m = LENGTH_PATTERN.search(text)
        if m:
            quantity = _to_number(m.group(1))

    height_match = HEIGHT_PATTERN.search(text)
    height_m = float(height_match.group(1)) if height_match else None

    city_match = CITY_PATTERN.search(text)
    location = city_match.group(1) if city_match else None

    urgency = "urgent_within_week" if URGENCY_PATTERN.search(text) else "standard"

    return ExtractedRequest(
        service=service,
        quantity=quantity,
        unit=unit,
        height_m=height_m,
        location=location,
        urgency=urgency,
        raw_text=text,
    )


def extract_with_llm(text: str, api_key: Optional[str] = None, model: str = "claude-sonnet-4-6") -> ExtractedRequest:
    """Production path (not wired up in this demo).

    In a real deployment this calls an LLM with a structured-output / tool-use
    schema matching ExtractedRequest, so it correctly handles messy real-world
    requests (typos, mixed units, implicit info, multiple services per email)
    that the regex extractor above cannot.

    Left unimplemented here deliberately: this repo is a synthetic demo and
    should not require the reviewer to provide an API key to run it. Swapping
    this in for `extract_rule_based` in main.py is the only change needed to
    go from "demo" to "production extraction".
    """
    raise NotImplementedError(
        "extract_with_llm is a documented stub for the production extraction "
        "path. Set ANTHROPIC_API_KEY and implement the structured-output call "
        "here to enable it; the demo pipeline uses extract_rule_based instead."
    )
