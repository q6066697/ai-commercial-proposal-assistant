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
from functools import lru_cache
from typing import Optional

from price_list import load_price_list
from schema import ExtractedRequest

# First match wins, so more specific phrases go first ("рама под баннер"
# before "баннер"). The first three keep their v0.1 ids: the eval set is
# labelled with them, and the calculator maps them onto v0.3 ids.
SERVICE_KEYWORDS = {
    "facade_cleaning": ["мойка фасада", "мойка фасадов", "facade cleaning", "чистка фасада"],
    "metal_structures_painting": ["покраска металлоконструкц", "окраска металлоконструкц", "metal structure"],
    "seam_sealing": ["герметизация шв", "герметизация межпанельных", "seam sealing", "заделка шв"],
    "window_washing": ["мойка окон", "мытье окон", "мытьё окон", "мойка остеклен"],
    "roof_washing": ["мойка кровли", "мойка крыши"],
    "facade_painting": ["покраска фасада", "окраска фасада"],
    "roof_painting": ["покраска крыши", "покраска кровли", "окраска кровли", "окраска крыши"],
    "facade_repair": ["ремонт фасада"],
    "roof_repair": ["ремонт кровли", "ремонт крыши"],
    "facade_insulation": ["утепление фасада", "утепление стен", "утепление квартир", "утепление балкон"],
    "sign_dismantle": ["демонтаж вывес", "демонтаж наружной вывес", "снятие вывес", "снять вывес"],
    "banner_frame": ["рама под баннер", "рамы под баннер", "раму под баннер"],
    "banner_install": ["баннер"],
    "ac_install": ["кондиционер"],
    "icicle_removal": ["сосул"],
    "snow_removal": ["от снега", "уборка снега", "очистка снега", "сброс снега", "снега с кровли", "снега с крыши"],
    "tree_removal": ["удаление дерев", "спил дерев", "спилить дерев", "удалить дерев"],
    "cargo_lift": ["подъём груза", "подъем груза", "поднять груз", "подъём оборудования", "подъем оборудования"],
}
LEGACY_UNITS = {"facade_cleaning": "m2", "metal_structures_painting": "m2", "seam_sealing": "m"}

CITY_PATTERN = re.compile(r"(?:г\.|город)\s*([А-ЯЁ][а-яё]+)", re.IGNORECASE)
AREA_PATTERN = re.compile(r"([\d\s]{1,7})\s*(?:м2|м²|кв\.?\s*м)", re.IGNORECASE)
LENGTH_PATTERN = re.compile(r"([\d\s]{1,7})\s*(?:м\.п\.|погонн)", re.IGNORECASE)
PIECES_PATTERN = re.compile(r"(\d{1,4})\s*(?:шт|ед\.|единиц|кондиционер|дерев|вывес|баннер)", re.IGNORECASE)
HEIGHT_PATTERN = re.compile(r"высот[а-я]*[^\d]{0,25}?(\d{1,3})\s*(?:м(?:етр)?)", re.IGNORECASE)
FLOOR_PATTERNS = [
    re.compile(r"(\d{1,3})\s*(?:-?\s*(?:й|ой|ом|м|го))?\s*этаж", re.IGNORECASE),
    re.compile(r"этаж\w*\s*[:\-–]?\s*(\d{1,3})", re.IGNORECASE),
]
WEIGHT_KG_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кг|килограм)", re.IGNORECASE)
WEIGHT_T_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:тонн|т\.?(?![а-яё]))", re.IGNORECASE)
DISMANTLE_PATTERN = re.compile(r"(?<![а-яё])(?:демонтаж|демонтир|снять|снятие)", re.IGNORECASE)
# Urgency is surcharged only when the request explicitly asks to speed up
# (pricing_rules.urgency.apply = only_if_requested). "не срочно",
# "несрочно" and the field label "Срочность:" must not count.
URGENCY_PATTERN = re.compile(
    r"(?<![а-яё])(в течение недели|срочн(?:о|ая|ый|ые|ую|ое)(?![а-яё])|как можно (?:быстрее|скорее)"
    r"|в кратчайшие сроки|в сжатые сроки|ускор[а-яё]*)",
    re.IGNORECASE,
)
NEGATION_PATTERN = re.compile(r"(?<![а-яё])не\s+$", re.IGNORECASE)


@lru_cache(maxsize=1)
def _price_list_units() -> dict[str, str]:
    return {sid: s["unit"] for sid, s in load_price_list()["services"].items()}


def _detect_service(text: str) -> tuple[Optional[str], Optional[str]]:
    lowered = text.lower()
    for service_key, keywords in SERVICE_KEYWORDS.items():
        for kw in keywords:
            if kw in lowered:
                unit = LEGACY_UNITS.get(service_key) or _price_list_units()[service_key]
                return service_key, unit
    return None, None


def _detect_urgency(text: str) -> Optional[str]:
    for m in URGENCY_PATTERN.finditer(text):
        if not NEGATION_PATTERN.search(text[max(0, m.start() - 5):m.start()]):
            return m.group(1)
    return None


def _detect_floor(text: str) -> Optional[int]:
    for pattern in FLOOR_PATTERNS:
        m = pattern.search(text)
        if m:
            return int(m.group(1))
    return None


def _detect_weight_kg(text: str) -> Optional[float]:
    m = WEIGHT_KG_PATTERN.search(text)
    if m:
        return _to_number(m.group(1))
    m = WEIGHT_T_PATTERN.search(text)
    if m:
        tonnes = _to_number(m.group(1))
        return None if tonnes is None else tonnes * 1000
    return None


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
    elif unit in ("m", "lm"):
        m = LENGTH_PATTERN.search(text)
        if m:
            quantity = _to_number(m.group(1))
    elif unit == "pcs":
        m = PIECES_PATTERN.search(text)
        if m:
            quantity = _to_number(m.group(1))

    weight_kg = _detect_weight_kg(text) if service == "cargo_lift" else None
    if service == "cargo_lift" and quantity is None and weight_kg is not None:
        quantity = 1.0  # "подъём груза 120 кг" = one item of that weight

    height_match = HEIGHT_PATTERN.search(text)
    height_m = float(height_match.group(1)) if height_match else None

    city_match = CITY_PATTERN.search(text)
    location = city_match.group(1) if city_match else None

    urgency_evidence = _detect_urgency(text)
    urgency = "urgent_within_week" if urgency_evidence else "standard"

    return ExtractedRequest(
        service=service,
        quantity=quantity,
        unit=unit,
        height_m=height_m,
        location=location,
        urgency=urgency,
        raw_text=text,
        floor=_detect_floor(text),
        weight_kg=weight_kg,
        is_dismantle=bool(DISMANTLE_PATTERN.search(text)),
        urgency_evidence=urgency_evidence,
    )


def extract_with_llm(text: str) -> ExtractedRequest:
    """Production extraction path. See llm_extractor.py for the real
    implementation (kept in a separate module so this file -- and the demo
    pipeline that imports it -- has no hard dependency on the `openai`
    package or any API credentials)."""
    from llm_extractor import extract_with_llm as _impl

    return _impl(text)
