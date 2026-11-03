"""
Creates an empty gold-markup template for each tender in data/tenders/index.json
(existing files are never overwritten):

    python scripts/make_gold_template.py

The template mirrors schemas/tender_requirements.schema.json with empty values
and one example row per list. It is filled by hand; set _meta.status to "done"
when finished -- only "done" files are validated by the tests (schema + every
quote must be found in the tender text).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "data" / "tenders" / "index.json"
GOLD_DIR = ROOT / "data" / "tenders" / "gold"

EVIDENCE = {"quote": "", "doc": ""}


def template(tender: dict) -> dict:
    return {
        "_meta": {
            "status": "template",
            "tender_id": tender["id"],
            "annotator": "",
            "how_to": (
                "Заполнить вручную по документам из data/tenders/raw/<id>/. quote — дословная цитата, "
                "doc — имя файла из tender.source_docs. scope — самые подробные строки объёмов, не итоги; "
                "разные объёмы по сезонам — отдельные строки с period и repeats=1. "
                "Пустые примеры строк удалить. Закончив, поставить status = \"done\"."
            ),
        },
        "tender": {
            "customer": {"value": None, "evidence": dict(EVIDENCE)},
            "subject": {"value": None, "evidence": dict(EVIDENCE)},
            "location": {"value": None, "evidence": dict(EVIDENCE)},
            "country": None,
            "source_docs": [d["file"] for d in tender["documents"]],
        },
        "scope": [{
            "service_id": None, "description": "", "surface": None, "side": None, "building": None,
            "volume": None, "unit": None, "repeats": 1, "period": None, "evidence": dict(EVIDENCE),
        }],
        "height": {"max_height_m": None, "floors": None, "evidence": dict(EVIDENCE)},
        "schedule": {
            "start": None, "end": None, "duration_days": None, "mode": None, "response_time_days": None,
            "working_hours": None, "contract_term": None, "evidence": [dict(EVIDENCE)],
        },
        "requirements": [{"text": "", "category": None, "mandatory": None, "cost_impact": None, "evidence": dict(EVIDENCE)}],
        "commercial": {
            "price_includes": [], "advance": None, "payment_days": None, "payment_basis": None,
            "warranty": None, "penalties": None, "liability": None, "evidence": [dict(EVIDENCE)],
        },
        "acceptance": {"documents": [], "review_days": None, "evidence": [dict(EVIDENCE)]},
        "gaps": [{"field": "", "issue": None, "question_to_customer": ""}],
        "flags": [{"text": "", "evidence": dict(EVIDENCE)}],
    }


def main() -> None:
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    for tender in json.loads(INDEX.read_text(encoding="utf-8"))["tenders"]:
        path = GOLD_DIR / f"{tender['id']}.json"
        if path.exists():
            print(f"keep  {path.relative_to(ROOT)}")
            continue
        path.write_text(json.dumps(template(tender), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"new   {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
