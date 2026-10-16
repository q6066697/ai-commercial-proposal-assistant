"""
Extraction layer: turns a free-form customer request (RU/EN mixed) into
structured project parameters (see schema.py for the shared contract).

Two implementations are provided:

- `extract_rule_based()` — a regex-based extractor that ships with this repo
  so the demo runs end-to-end with zero external dependencies / API keys.
- `extract_with_llm()` — the production path: a real LLM call using
  structured output, validated against the same `ExtractedRequest` schema.
  See llm_extractor.py for the implementation and required environment
  variables. It is NOT called by default (main.py / app.py use the
  rule-based path), so the demo stays runnable with zero setup.

Both return the same schema, so main.py/app.py can swap one for the other
without touching the rest of the pipeline.
"""
from __future__ import annotations

import re
from typing import Optional

from schema import ExtractedRequest

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


def _detect_service(text: str) -> tuple[Optional[str], Optional[str]]:
    lowered = text.lower()
    for service_key, keywords in SERVICE_KEYWORDS.items():
        for kw in keywords:
            if kw in lowered:
                unit = "m" if service_key == "seam_sealing" else "m2"
                return service_key, unit
    return None, None


def _to_number(raw: str) -> Optional[float]:
    """Returns None instead of raising when the regex captured only
    whitespace (e.g. "1,5 тыс. м2" -> the group before "м2" is " "), so an
    unparseable number becomes a missing field the reviewer sees, not a
    crashed pipeline. Found by the hard eval set (case hard_007)."""
    cleaned = raw.replace(" ", "").replace(",", ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_rule_based(text: str) -> ExtractedRequest:
    """Demo-only extractor. Good enough for the sample requests in
    data/demo_requests/ and data/eval_cases/, NOT a substitute for an LLM
    extractor on arbitrary free-form input -- real customer emails are far
    messier than this (typos, implicit info, multiple services per email)."""
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


def extract_with_llm(text: str) -> ExtractedRequest:
    """Production extraction path. See llm_extractor.py for the real
    implementation (kept in a separate module so this file -- and the demo
    pipeline that imports it -- has no hard dependency on the `openai`
    package or any API credentials)."""
    from llm_extractor import extract_with_llm as _impl

    return _impl(text)
