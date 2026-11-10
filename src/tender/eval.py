"""
Tender extraction eval against hand-made gold markup, without an LLM judge.

Matching a model fact to a gold item:

1. by quote ("цитата"): both quotes are verbatim, so each is located in the
   normalized text of its file; they match if they are in the same file and
   overlap by at least SPAN_OVERLAP of the shorter one (a 5-10 word gold
   quote inside the model's full sentence overlaps 100%).
2. by text ("текст"), only when the spans don't overlap -- e.g. the same
   requirement is in both the ТЗ and the contract and the model quoted the
   other file. Crude Russian stems (first 5 letters, stop words dropped):
   the model's stems must cover TEXT_COVERAGE of the gold item's stems,
   with at least 2 shared. Reported separately for a manual check.

Matching is many-to-many: one model sentence may cover two gold items (e.g.
working hours and weekends in one paragraph) and is not penalised for that.
Only model facts with a confirmed quote count towards recall -- unconfirmed
ones never reach the proposal.

Metrics: requirement recall (main: against every model fact with a quote;
strict: only model requirements[]), precision of model requirements[],
volumes, groundedness, flags, gaps (text only, approximate).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from price_list import LEGACY_SERVICE_ALIASES
from tender.grounding import ground, normalize
from tender.loader import TenderDocument
from tender.models import Evidence, TenderRequirements

SPAN_OVERLAP = 0.5
TEXT_COVERAGE = 0.6
GAP_COVERAGE = 0.4
MIN_SHARED_STEMS = 2

STOP_WORDS = set("""
и в во на с со по для не ни за от до к ко о об обо или а но что это при все всех всё
его ее её их как так же ли бы из у под над без через также либо этот эта эти то тот
быть должен должна должны должно иметь который которые
""".split())
# Words that carry no meaning in a gap question ("укажите ..., не указано ...")
GAP_STOP_STEMS = {"указа", "укажи", "уточн", "какой", "какая", "каков", "требу", "предус", "отдел", "прямо"}


def stems(text: str, extra_stop: set[str] = frozenset()) -> set[str]:
    out = set()
    for token in re.findall(r"[а-яa-z0-9]+", normalize(text)):
        if token in STOP_WORDS or (len(token) < 3 and not token.isdigit()):
            continue
        stem = token[:5]
        if stem not in extra_stop:
            out.add(stem)
    return out


def coverage(gold_text: str, model_text: str, extra_stop: set[str] = frozenset()) -> tuple[float, int]:
    g, m = stems(gold_text, extra_stop), stems(model_text, extra_stop)
    if not g:
        return 0.0, 0
    shared = len(g & m)
    return shared / len(g), shared


@dataclass(frozen=True)
class Span:
    doc: str
    start: int
    end: int


class Locator:
    """Quote -> span in the normalized text of its file (first occurrence;
    with "…" the span runs from the first fragment to the last)."""

    def __init__(self, docs: list[TenderDocument]):
        self.texts = {d.name: normalize(d.text) for d in docs}

    def span(self, evidence: Evidence) -> Optional[Span]:
        text = self.texts.get(evidence.doc)
        if text is None:
            return None
        fragments = [f.strip() for f in normalize(evidence.quote).split("...") if f.strip()]
        if not fragments:
            return None
        start = pos = text.find(fragments[0])
        if start < 0:
            return None
        for fragment in fragments:
            idx = text.find(fragment, pos)
            if idx < 0:
                return None
            pos = idx + len(fragment)
        return Span(evidence.doc, start, pos)


def spans_overlap(a: Optional[Span], b: Optional[Span]) -> bool:
    if a is None or b is None or a.doc != b.doc:
        return False
    inter = min(a.end, b.end) - max(a.start, b.start)
    return inter > 0 and inter / min(a.end - a.start, b.end - b.start) >= SPAN_OVERLAP


@dataclass
class Item:
    """One fact with a quote: a gold line or a model fact."""
    kind: str             # requirement / flag / scope / tender / schedule / commercial / acceptance
    text: str
    evidence: Evidence
    span: Optional[Span]
    ref: str              # "строка 12" for gold, "requirements[3]" for the model
    verified: bool = True
    extra: dict = field(default_factory=dict)


def match(gold: Item, model: Item, short_gold_fix: bool = True) -> Optional[str]:
    """short_gold_fix: a gold line with a single stem ("конфиденциальность")
    needs one shared stem, not MIN_SHARED_STEMS -- otherwise it can never
    match by text. Fixed after the first krasnodar_bank runs; False keeps the
    original rule so the report can show metrics before and after."""
    if spans_overlap(gold.span, model.span):
        return "цитата"
    cov, shared = coverage(gold.text, model.text)
    need = min(MIN_SHARED_STEMS, len(stems(gold.text))) if short_gold_fix else MIN_SHARED_STEMS
    if cov >= TEXT_COVERAGE and shared >= max(need, 1):
        return "текст"
    return None


# --- diagnostics for unconfirmed quotes ----------------------------------------

def closeness(quote: str, texts: dict[str, str]) -> tuple[float, Optional[str], str]:
    """How close an unconfirmed quote is to the documents: the best
    character-level similarity (0..1) between the normalized quote and a
    same-length window of a normalized document, anchored at their longest
    common run. Diagnostics only -- groundedness stays strict (exact match).
    Returns (similarity, file, nearest document text)."""
    q = normalize(quote)
    best = (0.0, None, "")
    if not q:
        return best
    for name, text in texts.items():
        m = SequenceMatcher(None, text, q, autojunk=False).find_longest_match(0, len(text), 0, len(q))
        if m.size == 0:
            continue
        start = max(0, m.a - m.b)
        window = text[start:start + len(q)]
        ratio = SequenceMatcher(None, q, window, autojunk=False).ratio()
        if ratio > best[0]:
            best = (ratio, name, window)
    return best


def closeness_label(similarity: float) -> str:
    if similarity >= 0.9:
        return "почти дословно"
    if similarity >= 0.6:
        return "искажено / пересказ"
    return "в документе нет похожего текста"


# --- building items ----------------------------------------------------------

def gold_items(gold: dict, locator: Locator) -> dict[str, list[Item]]:
    def item(kind, text, ev, line, extra=None):
        evidence = Evidence(quote=ev["quote"], doc=ev["doc"])
        return Item(kind, text, evidence, locator.span(evidence), f"строка {line}", extra=extra or {})

    lines = gold["_meta"].get("passport_lines", {})
    return {
        "requirements": [item("requirement", r["text"], r["evidence"], r["notes_line"],
                              {"section": r.get("section"), "strength": r.get("strength")})
                         for r in gold["requirements"]],
        "flags": [item("flag", f["text"], f["evidence"], f["notes_line"]) for f in gold["flags"]],
        "scope": [item("scope", s["description"], s["evidence"], s["notes_line"]) for s in gold["scope"]],
        "tender": [item("tender", gold["tender"][k]["value"], gold["tender"][k]["evidence"], lines.get(f"tender.{k}", "?"))
                   for k in ("customer", "subject", "location") if k in gold["tender"]],
    }


def model_items(req: TenderRequirements, locator: Locator) -> dict[str, list[Item]]:
    def item(kind, text, evidence, ref):
        return Item(kind, text, evidence, locator.span(evidence), ref, verified=bool(evidence.verified))

    out = {
        "requirements": [item("requirement", r.text, r.evidence, f"requirements[{i}]") for i, r in enumerate(req.requirements)],
        "flags": [item("flag", f.text, f.evidence, f"flags[{i}]") for i, f in enumerate(req.flags)],
        "other": [],
    }
    for key in ("customer", "subject", "location"):
        fact = getattr(req.tender, key)
        if fact:
            out["other"].append(item("tender", fact.value or fact.evidence.quote, fact.evidence, f"tender.{key}"))
    blocks = [("schedule", req.schedule)] + [(n, getattr(req, n)) for n in ("commercial", "acceptance")]
    for name, block in blocks:
        if block is None:
            continue
        for i, ev in enumerate(block.evidence):
            out["other"].append(item(name, ev.quote, ev, f"{name}.evidence[{i}]"))
    return out


# --- evaluation --------------------------------------------------------------

@dataclass
class Found:
    gold: Item
    model: Optional[Item]
    how: Optional[str]


@dataclass
class RunEval:
    metrics: dict
    requirements: list[Found]          # per gold requirement (main pool)
    requirements_strict: list[Found]
    extra_requirements: list[Item]     # model requirements matching nothing in gold
    volumes: list[dict]
    extra_scope: list[str]
    flags: list[Found]
    extra_flags: list[Item]
    gaps: list[dict]
    extra_gaps: list[str]
    grounding_issues: list[str]
    unverified: list[dict] = field(default_factory=list)  # diagnostics, not a metric


def _best(gold: Item, pool: list[Item], fix: bool) -> Found:
    candidates = [(m, match(gold, m, fix)) for m in pool if m.verified]
    by_quote = next((m for m, how in candidates if how == "цитата"), None)
    if by_quote:
        return Found(gold, by_quote, "цитата")
    by_text = next((m for m, how in candidates if how == "текст"), None)
    return Found(gold, by_text, "текст" if by_text else None)


def _service(sid: Optional[str]) -> Optional[str]:
    return LEGACY_SERVICE_ALIASES.get(sid, sid) if sid else sid


def evaluate_run(model: TenderRequirements, gold: dict, docs: list[TenderDocument],
                 short_gold_fix: bool = True) -> RunEval:
    report = ground(model, docs)  # re-checked from the source files, not trusted from the run
    locator = Locator(docs)
    g = gold_items(gold, locator)
    m = model_items(model, locator)

    pool_all = m["requirements"] + m["flags"] + m["other"]
    reqs = [_best(item, pool_all, short_gold_fix) for item in g["requirements"]]
    reqs_strict = [_best(item, m["requirements"], short_gold_fix) for item in g["requirements"]]

    gold_all = g["requirements"] + g["flags"] + g["scope"] + g["tender"]
    extra_reqs = [mi for mi in m["requirements"] if not any(match(gi, mi, short_gold_fix) for gi in gold_all)]

    volumes, used = [], set()
    for gs in gold["scope"]:
        candidates = [i for i, s in enumerate(model.scope) if _service(s.service_id) == _service(gs["service_id"])]
        ok_volume = ok_repeats = False
        found = None
        for i in candidates:
            s = model.scope[i]
            vol_ok = (s.volume is None and gs["volume"] is None) or (
                s.volume is not None and gs["volume"] is not None and abs(s.volume - gs["volume"]) < 0.01)
            if found is None or (vol_ok and not ok_volume):
                found, ok_volume, ok_repeats = i, vol_ok, s.repeats == gs["repeats"]
        if found is not None:
            used.add(found)
        ms = model.scope[found] if found is not None else None
        volumes.append({
            "line": gs["notes_line"], "service_id": gs["service_id"],
            "gold_volume": gs["volume"], "gold_repeats": gs["repeats"],
            "model_volume": ms.volume if ms else None, "model_repeats": ms.repeats if ms else None,
            "found": ms is not None, "volume_ok": ok_volume, "repeats_ok": ok_repeats,
        })
    extra_scope = [f"{s.service_id}: {s.description} ({s.volume} {s.unit}, ×{s.repeats})"
                   for i, s in enumerate(model.scope) if i not in used]

    flags = [_best(item, m["flags"], short_gold_fix) for item in g["flags"]]
    extra_flags = [mf for mf in m["flags"] if not any(match(gf, mf, short_gold_fix) for gf in g["flags"])]

    gaps, matched_model_gaps = [], set()
    for gg in gold["gaps"]:
        best = max(((coverage(gg["question_to_customer"], f"{mg.field} {mg.question_to_customer}", GAP_STOP_STEMS), j)
                    for j, mg in enumerate(model.gaps)), default=((0.0, 0), None))
        (cov, shared), j = best
        hit = j is not None and cov >= GAP_COVERAGE and shared >= MIN_SHARED_STEMS
        if hit and gg["scored"]:
            matched_model_gaps.add(j)
        gaps.append({"line": gg["notes_line"], "text": gg["question_to_customer"], "scored": gg["scored"],
                     "found": hit, "score": round(cov, 2),
                     "model": model.gaps[j].question_to_customer if j is not None else None})
    extra_gaps = [f"[{mg.issue}] {mg.question_to_customer}" for j, mg in enumerate(model.gaps)
                  if j not in matched_model_gaps]

    scored_gaps = [x for x in gaps if x["scored"]]
    flag_evidence = [f.evidence for f in model.flags]
    metrics = {
        "recall": _ratio(sum(1 for f in reqs if f.how), len(reqs)),
        "recall_strict": _ratio(sum(1 for f in reqs_strict if f.how), len(reqs_strict)),
        "precision": _ratio(len(m["requirements"]) - len(extra_reqs), len(m["requirements"])),
        "volumes": _ratio(sum(1 for v in volumes if v["found"] and v["volume_ok"] and v["repeats_ok"]), len(volumes)),
        "groundedness": _ratio(report.verified, report.total),
        "groundedness_flags": _ratio(sum(1 for e in flag_evidence if e.verified), len(flag_evidence)),
        "flags": _ratio(sum(1 for f in flags if f.how), len(flags)),
        "gaps": _ratio(sum(1 for x in scored_gaps if x["found"]), len(scored_gaps)),
        "model_requirements": len(m["requirements"]),
        "matched_by_text": sum(1 for f in reqs if f.how == "текст"),
    }
    issues = [f"{i.status} {i.path}: «{i.quote}» [{i.doc}]" for i in report.issues if i.status != "verified"]
    unverified = []
    for i in report.issues:
        if i.status in ("verified", "wrong_doc"):
            continue
        similarity, doc, nearest = closeness(i.quote, locator.texts)
        unverified.append({"path": i.path, "status": i.status, "quote": i.quote, "doc": i.doc,
                           "similarity": round(similarity, 3), "label": closeness_label(similarity),
                           "nearest_doc": doc, "nearest": nearest})
    return RunEval(metrics, reqs, reqs_strict, extra_reqs, volumes, extra_scope, flags, extra_flags,
                   gaps, extra_gaps, issues, unverified)


def _ratio(num: int, den: int) -> dict:
    return {"num": num, "den": den, "value": (num / den) if den else None}
