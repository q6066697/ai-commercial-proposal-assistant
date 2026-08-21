"""
Evaluation harness for the extraction layer.

Modeled on the eval approach used in rag-eval-service: a fixed set of
synthetic cases with known-correct expected output, scored field-by-field
plus an overall exact-match rate, so extraction quality is a number you can
track across changes -- not a vibe.

Usage:
    python src/evaluate.py                    # eval extract_rule_based (default)
    python src/evaluate.py --extractor llm     # eval extract_with_llm (needs LLM_API_BASE etc.)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from extractor import extract_rule_based
from schema import ExtractedRequest

ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = ROOT / "data" / "eval_cases" / "cases.json"
FIELDS = ["service", "quantity", "unit", "height_m", "location", "urgency"]


def load_cases() -> list[dict]:
    with open(CASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _field_match(predicted, expected, field: str) -> bool:
    p = getattr(predicted, field)
    e = expected.get(field)
    if field == "quantity" or field == "height_m":
        if p is None or e is None:
            return p == e
        return abs(p - e) < 0.01
    return p == e


def run_eval(extractor_fn: Callable[[str], ExtractedRequest], cases: list[dict]) -> dict:
    field_correct = {f: 0 for f in FIELDS}
    exact_matches = 0
    per_case_results = []

    for case in cases:
        predicted = extractor_fn(case["text"])
        matches = {f: _field_match(predicted, case["expected"], f) for f in FIELDS}
        for f, ok in matches.items():
            if ok:
                field_correct[f] += 1
        is_exact = all(matches.values())
        exact_matches += int(is_exact)
        per_case_results.append({"id": case["id"], "exact_match": is_exact, "field_matches": matches})

    n = len(cases)
    report = {
        "n_cases": n,
        "exact_match_rate": round(exact_matches / n, 3),
        "field_accuracy": {f: round(field_correct[f] / n, 3) for f in FIELDS},
        "per_case": per_case_results,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the extraction layer against synthetic cases")
    parser.add_argument("--extractor", choices=["rule_based", "llm"], default="rule_based")
    args = parser.parse_args()

    if args.extractor == "llm":
        from extractor import extract_with_llm
        extractor_fn = extract_with_llm
    else:
        extractor_fn = extract_rule_based

    cases = load_cases()
    report = run_eval(extractor_fn, cases)

    print(f"\nExtractor: {args.extractor}")
    print(f"Cases: {report['n_cases']}")
    print(f"Exact-match rate: {report['exact_match_rate'] * 100:.1f}%")
    print("\nField accuracy:")
    for field, acc in report["field_accuracy"].items():
        print(f"  {field:12s} {acc * 100:5.1f}%")

    failed = [c for c in report["per_case"] if not c["exact_match"]]
    if failed:
        print(f"\n{len(failed)} case(s) with mismatches:")
        for c in failed:
            wrong_fields = [f for f, ok in c["field_matches"].items() if not ok]
            print(f"  {c['id']}: mismatched fields -> {wrong_fields}")


if __name__ == "__main__":
    main()
