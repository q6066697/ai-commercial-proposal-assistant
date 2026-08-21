"""
CLI entry point for the v0.1 rule-based MVP pipeline.

Usage:
    python src/main.py data/demo_requests/facade_cleaning.txt
    python src/main.py --all          # run every file in data/demo_requests/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from extractor import extract_rule_based
from calculator import calculate_price
from proposal_generator import generate_proposal_docx

ROOT = Path(__file__).resolve().parent.parent
DEMO_REQUESTS_DIR = ROOT / "data" / "demo_requests"
OUTPUTS_DIR = ROOT / "outputs"


def run_one(request_path: Path) -> None:
    text = request_path.read_text(encoding="utf-8")
    print(f"\n=== {request_path.name} ===")

    extracted = extract_rule_based(text)
    print("Extracted:", json.dumps(extracted.to_dict(), ensure_ascii=False, indent=2))

    if extracted.service is None:
        print("!! Could not detect a known service — skipping calculation. "
              "This is where an LLM extractor would handle messier phrasing.")
        return

    breakdown = calculate_price(extracted)
    print("Price breakdown:", json.dumps(breakdown.to_dict(), ensure_ascii=False, indent=2))

    output_path = OUTPUTS_DIR / f"{request_path.stem}_proposal.docx"
    generate_proposal_docx(extracted, breakdown, output_path, proposal_number=f"DEMO-{request_path.stem}")
    print(f"-> Draft proposal written to {output_path}")

    if breakdown.warnings:
        print("Human review required before sending — see warnings above.")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Commercial Proposal Assistant — demo pipeline")
    parser.add_argument("request_file", nargs="?", help="Path to a request .txt file")
    parser.add_argument("--all", action="store_true", help="Run all files in data/demo_requests/")
    args = parser.parse_args()

    if args.all:
        for path in sorted(DEMO_REQUESTS_DIR.glob("*.txt")):
            run_one(path)
    elif args.request_file:
        run_one(Path(args.request_file))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
