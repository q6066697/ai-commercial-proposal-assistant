"""
Converts hand-written gold notes (data/tenders/gold/<id>_notes.md) into gold
JSON following schemas/tender_requirements.schema.json.

Notes format, one fact per line under "## " section headings:

    - what | обязательно/желательно/условно | «verbatim quote» | tz
    - what | «verbatim quote» | contract                (flags)
    - text                                              (gaps: no quote)
    - заказчик: value | «quote» | contract              (passport)
    - режим: fixed_dates                                (passport, no quote)

"tz" / "contract" is the file stem in the tender folder (tz.docx ...).
Quotes are carried over unchanged and then checked with the same grounding
check as the model's quotes; anything not found is reported, never fixed.
A line that cannot be parsed is an error with its line number -- nothing is
guessed or skipped silently.

Every gold item keeps "notes_line" (and requirements keep "section" /
"strength") so the eval report can point back to the notes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from extractor import SERVICE_KEYWORDS
from price_list import LEGACY_SERVICE_ALIASES, load_price_list
from tender.grounding import GroundingReport, ground
from tender.loader import TenderDocument
from tender.models import TenderRequirements

LINE = re.compile(
    r"^- (?P<what>.+?)\s*\|\s*(?:(?P<strength>обязательно|желательно|условно)\s*\|\s*)?"
    r"«(?P<quote>.+)»\s*\|\s*(?P<doc>[\w.-]+)\s*$"
)
PASSPORT_PLAIN = re.compile(r"^- (?P<key>[^:|]+):\s*(?P<value>[^|]+?)\s*$")
VOLUME = re.compile(r"(\d[\d ]*(?:[.,]\d+)?)\s*(м²|м2|кв\.?\s*м|м\.?\s*п\.?|п\.?\s*м\.?|шт)", re.IGNORECASE)
REPEATS = re.compile(r"(\d+)\s*раз", re.IGNORECASE)
UNITS = {"м²": "m2", "м2": "m2", "кв": "m2", "м.п": "lm", "мп": "lm", "п.м": "lm", "пм": "lm", "шт": "pcs"}

CATEGORY_BY_SECTION = [  # first stem found in the section title wins
    ("персонал", "personnel_admission"), ("допуск", "personnel_admission"),
    ("безопасн", "safety"), ("охрана труда", "safety"),
    ("материал", "materials"), ("оборудован", "equipment"),
    ("срок", "schedule"),
]
PASSPORT_KEYS = {"заказчик": "customer", "предмет": "subject", "место": "location",
                 "страна": "country", "режим": "mode", "высота": "height"}
# A "not filled in the contract, given in the ТЗ" note is information, not a
# gap: the pipeline itself drops such gaps (llm_extract.drop_resolved_gaps).
INFORMATIONAL_GAP = re.compile(r"есть (?:только )?в (?:тз|техническом задании|договоре)", re.IGNORECASE)
PROVENANCE = "Gold: черновик подготовлен с помощью ИИ, каждая строка вручную сверена с документами"


class NotesError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("Не удалось разобрать заметки:\n" + "\n".join(errors))
        self.errors = errors


@dataclass
class Conversion:
    gold: dict
    grounding: GroundingReport
    issues: list[str] = field(default_factory=list)  # "line N: status «quote» [doc]"

    @property
    def status(self) -> str:
        return self.gold["_meta"]["status"]


def _section_kind(title: str) -> str:
    t = title.lower()
    if t.startswith("объём") or t.startswith("объем"):
        return "scope"
    if "gaps" in t or t.startswith("чего нет"):
        return "gaps"
    if t.startswith("флаг"):
        return "flags"
    if t.startswith("паспорт"):
        return "passport"
    return "requirements"


def _category(title: str) -> str:
    t = title.lower()
    return next((cat for stem, cat in CATEGORY_BY_SECTION if stem in t), "other")


def _detect_service(text: str) -> Optional[str]:
    lowered = text.lower()
    for service_id, keywords in SERVICE_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return LEGACY_SERVICE_ALIASES.get(service_id, service_id)
    return None


def _gap_issue(text: str) -> str:
    t = text.lower()
    if "не заполнен" in t:
        return "blank_in_template"
    if "не ясно" in t or "неясно" in t or "не определено" in t:
        return "ambiguous"
    return "missing"


def _scope_item(what: str, units: dict[str, str]) -> tuple[Optional[dict], Optional[str]]:
    service_id = _detect_service(what)
    if service_id is None:
        return None, "не удалось определить услугу по прайсу"
    repeats_match = REPEATS.search(what)
    item = {"service_id": service_id, "description": what, "surface": None, "side": None, "building": None,
            "volume": None, "unit": units.get(service_id), "repeats": int(repeats_match.group(1)) if repeats_match else 1,
            "period": None}
    volume_match = VOLUME.search(what)
    if volume_match:
        item["volume"] = float(volume_match.group(1).replace(" ", "").replace(",", "."))
        unit_raw = re.sub(r"[\s.]", "", volume_match.group(2).lower())
        item["unit"] = next((u for k, u in UNITS.items() if unit_raw.startswith(k.replace(".", ""))), item["unit"])
    elif "не указан" not in what.lower():
        return None, "нет объёма (число с м², м.п. или шт) и нет пометки «не указана»"
    return item, None


def parse_notes(text: str, doc_names: list[str]) -> dict:
    """Notes text -> gold dict (without grounding). doc_names: files of the
    tender; a line's "tz" / "contract" must match exactly one file stem."""
    stems = {name.rsplit(".", 1)[0]: name for name in doc_names}
    units = {sid: s["unit"] for sid, s in load_price_list()["services"].items()}
    errors: list[str] = []
    section, kind = None, None
    passport: dict = {}
    passport_lines: dict[str, int] = {}
    scope, requirements, gaps, flags = [], [], [], []

    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("## "):
            section = line[3:].strip()
            kind = _section_kind(section)
            continue
        if not line.startswith("- ") or kind is None:
            continue

        if kind == "gaps":
            issue = _gap_issue(line[2:])
            gap = {"field": "", "issue": issue, "question_to_customer": line[2:].strip(), "notes_line": n,
                   "scored": True}
            if issue == "blank_in_template" and INFORMATIONAL_GAP.search(line):
                gap.update(scored=False, info_reason="в договоре не заполнено, значение есть в другом документе "
                                                     "(пайплайн такие gaps снимает)")
            gaps.append(gap)
            continue

        m = LINE.match(line)
        if kind == "passport" and not m:
            plain = PASSPORT_PLAIN.match(line)
            if plain and plain.group("key").strip().lower() in PASSPORT_KEYS:
                passport[PASSPORT_KEYS[plain.group("key").strip().lower()]] = (plain.group("value"), None)
                continue
        if not m:
            errors.append(f"строка {n}: формат не распознан: {line}")
            continue
        if m.group("doc") not in stems:
            errors.append(f"строка {n}: файл «{m.group('doc')}» не найден среди {sorted(stems)}")
            continue
        evidence = {"quote": m.group("quote"), "doc": stems[m.group("doc")]}
        what = m.group("what")

        if kind == "passport":
            key, _, value = what.partition(":")
            if key.strip().lower() not in PASSPORT_KEYS:
                errors.append(f"строка {n}: неизвестное поле паспорта «{key.strip()}»")
                continue
            passport[PASSPORT_KEYS[key.strip().lower()]] = (value.strip(), evidence)
            passport_lines[f"tender.{PASSPORT_KEYS[key.strip().lower()]}"] = n
        elif kind == "flags":
            flags.append({"text": what, "evidence": evidence, "notes_line": n})
        elif kind == "scope":
            item, error = _scope_item(what, units)
            if error:
                errors.append(f"строка {n}: {error}: {line}")
                continue
            scope.append({**item, "evidence": evidence, "notes_line": n})
        else:
            strength = m.group("strength")
            if strength is None:
                errors.append(f"строка {n}: не указано обязательно/желательно/условно: {line}")
                continue
            requirements.append({
                "text": what, "category": _category(section), "mandatory": strength == "обязательно",
                "evidence": evidence, "section": section, "strength": strength, "notes_line": n,
            })

    for key in ("customer", "subject", "mode"):
        if key not in passport:
            errors.append(f"паспорт: нет поля «{next(k for k, v in PASSPORT_KEYS.items() if v == key)}»")
    for key in ("customer", "subject"):
        if key in passport and passport[key][1] is None:
            errors.append(f"паспорт: у поля «{key}» нет цитаты")
    if errors:
        raise NotesError(errors)

    def fact(key):
        value, evidence = passport[key]
        return {"value": value, "evidence": evidence}

    height_value = passport.get("height", ("", None))[0]
    height_m = re.search(r"(\d+(?:[.,]\d+)?)\s*м", height_value or "")
    floors = re.search(r"(\d+)\s*этаж", height_value or "")
    gold = {
        "_meta": {"status": "draft", "source": "notes", "provenance": PROVENANCE, "passport_lines": passport_lines},
        "tender": {
            "customer": fact("customer"),
            "subject": fact("subject"),
            **({"location": fact("location")} if "location" in passport and passport["location"][1] else {}),
            **({"country": passport["country"][0]} if "country" in passport else {}),
            "source_docs": list(doc_names),
        },
        "scope": scope,
        "height": {"max_height_m": float(height_m.group(1).replace(",", ".")) if height_m else None,
                   "floors": int(floors.group(1)) if floors else None},
        "schedule": {"start": None, "end": None, "duration_days": None, "mode": passport["mode"][0],
                     "response_time_days": None, "working_hours": None, "contract_term": None, "evidence": []},
        "requirements": requirements,
        "gaps": gaps,
        "flags": flags,
    }
    return gold


def convert(notes_text: str, docs: list[TenderDocument], tender_id: str) -> Conversion:
    gold = parse_notes(notes_text, [d.name for d in docs])
    gold["_meta"]["tender_id"] = tender_id

    # Same grounding check as for the model; evidence.verified/match are
    # written into the gold so the report shows them, quotes stay untouched.
    req = TenderRequirements.model_validate({k: v for k, v in gold.items() if k != "_meta"})
    report = ground(req, docs)
    checked = req.to_json_dict()
    issues = []
    lines_by_path = _notes_lines(gold)
    for issue in report.issues:
        if issue.status == "verified":
            continue
        issues.append(f"строка {lines_by_path.get(issue.path, '?')}: {issue.status} «{issue.quote}» [{issue.doc}]")
    _copy_evidence_status(checked, gold)
    gold["_meta"]["status"] = "done" if not issues else "draft"
    return Conversion(gold, report, issues)


def _notes_lines(gold: dict) -> dict[str, int]:
    out = dict(gold["_meta"]["passport_lines"])
    for name in ("scope", "requirements", "flags"):
        for i, item in enumerate(gold[name]):
            out[f"{name}[{i}]"] = item["notes_line"]
    return out


def _copy_evidence_status(checked: dict, gold: dict) -> None:
    """Writes verified/match from the checked model back into the gold dict
    (which keeps its extra notes_line / section / strength fields). The doc
    is NOT copied: a quote found in another file is reported, not fixed."""
    def copy(src, dst):
        for key in ("verified", "match"):
            if key in src:
                dst[key] = src[key]

    for key in ("customer", "subject", "location"):
        if key in checked["tender"]:
            copy(checked["tender"][key]["evidence"], gold["tender"][key]["evidence"])
    for name in ("scope", "requirements", "flags"):
        for src, dst in zip(checked[name], gold[name]):
            copy(src["evidence"], dst["evidence"])
