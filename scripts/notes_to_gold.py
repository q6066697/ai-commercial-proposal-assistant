"""
Converts hand-written gold notes into gold JSON (see src/tender/gold_notes.py):

    python scripts/notes_to_gold.py krasnodar_bank

Reads data/tenders/gold/<id>_notes.md and the tender documents in
data/tenders/raw/<id>/ (needed to check every quote), writes
data/tenders/gold/<id>.json. Quotes that are not found are listed and the
file is saved with _meta.status = "draft"; nothing is corrected silently.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tender.gold_notes import NotesError, convert  # noqa: E402
from tender.loader import load_tender  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Gold notes (.md) -> gold JSON")
    parser.add_argument("tender_id")
    args = parser.parse_args()

    gold_dir = ROOT / "data" / "tenders" / "gold"
    notes = gold_dir / f"{args.tender_id}_notes.md"
    docs = load_tender(ROOT / "data" / "tenders" / "raw" / args.tender_id)
    try:
        result = convert(notes.read_text(encoding="utf-8"), docs, args.tender_id)
    except NotesError as e:
        sys.exit(str(e))

    out = gold_dir / f"{args.tender_id}.json"
    out.write_text(json.dumps(result.gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    gold = result.gold
    scored_gaps = sum(1 for g in gold["gaps"] if g["scored"])
    print(f"{out.relative_to(ROOT)}  status={result.status}")
    print(f"  scope: {len(gold['scope'])}, requirements: {len(gold['requirements'])}, "
          f"flags: {len(gold['flags'])}, gaps: {len(gold['gaps'])} (в метрике: {scored_gaps})")
    print(f"  цитаты: подтверждено {result.grounding.verified} из {result.grounding.total}")
    for issue in result.issues:
        print(f"  НЕ НАЙДЕНО  {issue}")
    for gap in gold["gaps"]:
        if not gap["scored"]:
            print(f"  информационный gap (строка {gap['notes_line']}): {gap['question_to_customer']}")


if __name__ == "__main__":
    main()
