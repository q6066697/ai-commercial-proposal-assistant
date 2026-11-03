"""
Deterministic groundedness check -- the main guard against invented tender
requirements. No LLM involved: every evidence.quote must literally occur in
the text of the file named in evidence.doc, after normalizing only what does
not change meaning (whitespace and line breaks, NBSP / soft hyphen, quote and
dash styles, ё/е, letter case).

Statuses written into evidence.match (evidence.verified = status in
{verified, wrong_doc}):

- verified        found in the named file
- wrong_doc       found in another file of the same tender; evidence.doc is
                  corrected to that file
- not_found       not in any file -> the fact is treated as unconfirmed
- too_short       under MIN_QUOTE_CHARS: a 3-char quote proves nothing
- value_mismatch  the quote exists, but the number stated in the fact
                  (scope volume, height) does not occur in it -- "real quote,
                  invented number"

A quote may skip text with an ellipsis ("…" / "..."); then every fragment
must be found, in order.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from tender.loader import TenderDocument
from tender.models import Evidence, TenderRequirements

MIN_QUOTE_CHARS = 10

_CHAR_MAP = str.maketrans({
    "«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‘": "'", "’": "'", "‛": "'", "`": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "ё": "е", "Ё": "Е",
    "­": None,  # soft hyphen
})
_NUMBER = re.compile(r"\d(?:[\d ]*\d)?(?:[.,] ?\d+)?")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_CHAR_MAP)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _find_fragments(fragments: list[str], text: str) -> bool:
    pos = 0
    for fragment in fragments:
        idx = text.find(fragment, pos)
        if idx < 0:
            return False
        pos = idx + len(fragment)
    return True


def numbers_in(text: str) -> list[float]:
    """Numbers as written in Russian documents: "10 740,4", "10740, 4", "5-ти"."""
    out = []
    for raw in _NUMBER.findall(normalize(text)):
        try:
            out.append(float(raw.replace(" ", "").replace(",", ".")))
        except ValueError:
            continue
    return out


def value_in_quote(value: float, quote: str) -> bool:
    return any(abs(n - value) < 0.01 for n in numbers_in(quote))


@dataclass
class GroundingIssue:
    path: str
    status: str
    quote: str
    doc: str

    def to_dict(self) -> dict:
        return {"path": self.path, "status": self.status, "quote": self.quote, "doc": self.doc}


@dataclass
class GroundingReport:
    total: int = 0
    verified: int = 0
    issues: list[GroundingIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"total": self.total, "verified": self.verified, "unverified": self.total - self.verified,
                "issues": [i.to_dict() for i in self.issues]}


class Grounder:
    def __init__(self, docs: list[TenderDocument]):
        self.docs = {d.name: normalize(d.text) for d in docs}

    def locate(self, quote: str, doc: str) -> tuple[str, Optional[str]]:
        """(status, file the quote was found in)."""
        fragments = [f.strip() for f in normalize(quote).split("...")]
        fragments = [f for f in fragments if f]
        if sum(len(f) for f in fragments) < MIN_QUOTE_CHARS:
            return "too_short", None
        if doc in self.docs and _find_fragments(fragments, self.docs[doc]):
            return "verified", doc
        for name, text in self.docs.items():
            if name != doc and _find_fragments(fragments, text):
                return "wrong_doc", name
        return "not_found", None

    def check(self, evidence: Evidence, value: Optional[float] = None) -> str:
        status, found_in = self.locate(evidence.quote, evidence.doc)
        if status == "wrong_doc":
            evidence.doc = found_in
        if status in ("verified", "wrong_doc") and value is not None and not value_in_quote(value, evidence.quote):
            status = "value_mismatch"
        evidence.match = status
        evidence.verified = status in ("verified", "wrong_doc")
        return status


def ground(req: TenderRequirements, docs: list[TenderDocument]) -> GroundingReport:
    """Checks every evidence quote in place (sets evidence.verified / .match)."""
    grounder = Grounder(docs)
    values: dict[int, Optional[float]] = {id(s.evidence): s.volume for s in req.scope}
    if req.height and req.height.evidence:
        values[id(req.height.evidence)] = req.height.max_height_m or req.height.floors

    report = GroundingReport()
    for path, evidence in req.all_evidence():
        status = grounder.check(evidence, values.get(id(evidence)))
        report.total += 1
        if evidence.verified:
            report.verified += 1
        if status != "verified":
            report.issues.append(GroundingIssue(path, status, evidence.quote, evidence.doc))
    return report
