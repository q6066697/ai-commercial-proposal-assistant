"""
Evaluation harness for the extraction layer.

Modeled on the eval approach used in rag-eval-service: fixed sets of
synthetic cases with known-correct expected output, scored field-by-field
plus an overall exact-match rate, so extraction quality is a number you can
track across changes -- not a vibe.

Two case sets:
    clean  data/eval_cases/cases.json       tidy, form-like requests
                                            (regression baseline, guarded by tests)
    hard   data/eval_cases/hard_cases.json  messy real-world-style input: chat
                                            style, typos, synonyms, number formats,
                                            implicit/negated urgency, missing fields,
                                            out-of-scope work, prompt injection

Besides accuracy, the report includes latency and (for the LLM path) token
usage and estimated cost, because "better" has to be weighed against
"slower and more expensive".

Usage:
    python src/evaluate.py                                  # rule-based, clean set
    python src/evaluate.py --set hard                       # rule-based, hard set
    python src/evaluate.py --extractor llm --set all        # LLM, both sets
    python src/evaluate.py --extractor llm --set all --save # also write JSON report

Optional cost estimate for the LLM path (USD per 1M tokens):
    LLM_PRICE_IN=0.15 LLM_PRICE_OUT=0.60
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

from env import load_env
from extractor import extract_rule_based
from schema import ExtractedRequest

ROOT = Path(__file__).resolve().parent.parent
CASE_FILES = {
    "clean": ROOT / "data" / "eval_cases" / "cases.json",
    "hard": ROOT / "data" / "eval_cases" / "hard_cases.json",
}
RESULTS_DIR = ROOT / "outputs" / "eval_results"
FIELDS = ["service", "quantity", "unit", "height_m", "location", "urgency"]


def load_cases(case_set: str = "clean") -> list[dict]:
    sets = ["clean", "hard"] if case_set == "all" else [case_set]
    cases: list[dict] = []
    for name in sets:
        with open(CASE_FILES[name], "r", encoding="utf-8") as f:
            for case in json.load(f):
                case.setdefault("set", name)
                case.setdefault("tag", "clean" if name == "clean" else "other")
                cases.append(case)
    return cases


def normalize_location(value):
    """'г. Минск' / 'город Минск' / 'минск' / 'Могилев' all compare equal to
    'Минск' / 'Могилёв'. A location is right if it names the right city --
    the prefix or letter case shouldn't count as an extraction error."""
    if value is None:
        return None
    v = str(value).strip().lower().replace("ё", "е")
    v = re.sub(r"^(г\.|город)\s*", "", v)
    return v.strip(" .,") or None


def _field_match(predicted: ExtractedRequest, expected: dict, field: str) -> bool:
    p = getattr(predicted, field)
    e = expected.get(field)
    if field in ("quantity", "height_m"):
        if p is None or e is None:
            return p == e
        return abs(p - e) < 0.01
    if field == "location":
        return normalize_location(p) == normalize_location(e)
    return p == e


def run_eval(extractor_fn: Callable[[str], ExtractedRequest], cases: list[dict],
             usage_source: dict | None = None, progress: bool = False) -> dict:
    field_correct = {f: 0 for f in FIELDS}
    exact_matches = 0
    errors = 0
    latencies: list[float] = []
    prompt_tokens = completion_tokens = 0
    per_tag = defaultdict(lambda: {"n": 0, "exact": 0})
    per_case_results = []

    for i, case in enumerate(cases, 1):
        if progress:
            print(f"  [{i}/{len(cases)}] {case['id']} ...", end=" ", flush=True)
        start = time.perf_counter()
        try:
            predicted = extractor_fn(case["text"])
            error = None
        except Exception as exc:  # a crash or invalid output is a failed case, not a crashed eval
            predicted = None
            error = f"{type(exc).__name__}: {exc}"[:200]
            errors += 1
        latencies.append(time.perf_counter() - start)
        if progress:
            print(f"{latencies[-1]:.1f}s" + (" ERROR" if error else ""), flush=True)

        if usage_source:
            prompt_tokens += usage_source.get("prompt_tokens", 0)
            completion_tokens += usage_source.get("completion_tokens", 0)

        if predicted is None:
            matches = {f: False for f in FIELDS}
        else:
            matches = {f: _field_match(predicted, case["expected"], f) for f in FIELDS}
        for f, ok in matches.items():
            field_correct[f] += int(ok)
        is_exact = all(matches.values())
        exact_matches += int(is_exact)

        tag = case.get("tag", "other")
        per_tag[tag]["n"] += 1
        per_tag[tag]["exact"] += int(is_exact)

        per_case_results.append({
            "id": case["id"],
            "tag": tag,
            "exact_match": is_exact,
            "field_matches": matches,
            "predicted": None if predicted is None else
                {f: getattr(predicted, f) for f in FIELDS},
            "error": error,
        })

    n = len(cases)
    report = {
        "n_cases": n,
        "exact_match_rate": round(exact_matches / n, 3),
        "field_accuracy": {f: round(field_correct[f] / n, 3) for f in FIELDS},
        "errors": errors,
        "latency_s": {
            "mean": round(statistics.mean(latencies), 3),
            "p95": round(sorted(latencies)[max(0, int(round(0.95 * n)) - 1)], 3),
            "total": round(sum(latencies), 2),
        },
        "per_tag": {t: {"n": v["n"], "exact_match_rate": round(v["exact"] / v["n"], 3)}
                    for t, v in sorted(per_tag.items())},
        "per_case": per_case_results,
    }
    if usage_source is not None:
        report["tokens"] = {"prompt": prompt_tokens, "completion": completion_tokens}
        price_in = os.environ.get("LLM_PRICE_IN")
        price_out = os.environ.get("LLM_PRICE_OUT")
        if price_in and price_out:
            cost = (prompt_tokens * float(price_in) + completion_tokens * float(price_out)) / 1_000_000
            report["cost_usd"] = {"total": round(cost, 5), "per_request": round(cost / n, 6)}
    return report


def print_report(report: dict, label: str) -> None:
    print(f"\nExtractor: {label}")
    print(f"Cases: {report['n_cases']}   Errors: {report['errors']}")
    print(f"Exact-match rate: {report['exact_match_rate'] * 100:.1f}%")
    print("\nField accuracy:")
    for field, acc in report["field_accuracy"].items():
        print(f"  {field:12s} {acc * 100:5.1f}%")
    print("\nBy case type (exact match):")
    for tag, v in report["per_tag"].items():
        print(f"  {tag:20s} {v['exact_match_rate'] * 100:5.1f}%  (n={v['n']})")
    lat = report["latency_s"]
    print(f"\nLatency per request: mean {lat['mean']}s, p95 {lat['p95']}s")
    if "tokens" in report:
        t = report["tokens"]
        print(f"Tokens: {t['prompt']} in / {t['completion']} out")
    if "cost_usd" in report:
        c = report["cost_usd"]
        print(f"Estimated cost: ${c['total']} total, ${c['per_request']} per request")

    failed = [c for c in report["per_case"] if not c["exact_match"]]
    if failed:
        print(f"\n{len(failed)} case(s) with mismatches:")
        for c in failed:
            if c["error"]:
                print(f"  {c['id']} [{c['tag']}]: ERROR {c['error']}")
            else:
                wrong = [f for f, ok in c["field_matches"].items() if not ok]
                print(f"  {c['id']} [{c['tag']}]: mismatched fields -> {wrong}")


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Evaluate the extraction layer against synthetic cases")
    parser.add_argument("--extractor", choices=["rule_based", "llm"], default="rule_based")
    parser.add_argument("--set", dest="case_set", choices=["clean", "hard", "all"], default="clean")
    parser.add_argument("--save", action="store_true", help="write the JSON report to outputs/eval_results/")
    args = parser.parse_args()

    usage_source = None
    if args.extractor == "llm":
        from extractor import extract_with_llm
        import llm_extractor
        extractor_fn = extract_with_llm
        usage_source = llm_extractor.LAST_USAGE
        label = f"llm ({os.environ.get('LLM_MODEL', '?')})"
    else:
        extractor_fn = extract_rule_based
        label = "rule_based"

    cases = load_cases(args.case_set)
    report = run_eval(extractor_fn, cases, usage_source, progress=args.extractor == "llm")
    report["extractor"] = label
    report["case_set"] = args.case_set
    print_report(report, f"{label}, set: {args.case_set}")

    if args.save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label)
        out = RESULTS_DIR / f"{safe}__{args.case_set}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
