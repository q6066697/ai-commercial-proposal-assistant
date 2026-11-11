"""
Tender extraction eval against gold markup.

    LLM_MODEL=<model> python scripts/eval_tender.py krasnodar_bank --runs 3                   # v1 runs, then eval
    LLM_MODEL=<model> python scripts/eval_tender.py krasnodar_bank --runs 3 --extraction v2   # v2 runs
    python scripts/eval_tender.py krasnodar_bank --no-llm                     # re-evaluate saved runs only

Runs use LLM_API_BASE / LLM_API_KEY / LLM_MODEL (.env, environment wins)
and are saved to outputs/tenders/<id>/runs/<model>/<extraction>/<n>/
(git-ignored).
Already saved runs are kept: --runs 3 only adds what is missing. On HTTP 429
the run waits a minute and retries once, on a provider error (HTTP 5xx) it
waits RETRY_WAIT_5XX seconds and retries once; a run that still fails is
recorded in error.json and reported, not retried again.

The report covers every model and extraction variant found under runs/
and goes to the console and to eval/results/<id>.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from env import load_env  # noqa: E402
from tender.eval import evaluate_run  # noqa: E402
from tender.eval_report import ModelRuns, comparison_table, render  # noqa: E402
from tender.llm_extract import LAST_USAGE, STRATEGIES  # noqa: E402
from tender.loader import load_tender  # noqa: E402
from tender.models import TenderRequirements  # noqa: E402
from tender.pipeline import run_tender  # noqa: E402

RATE_LIMIT_WAIT = 60
RETRY_WAIT_5XX = 30


def model_slug(model: str) -> str:
    return re.sub(r"[^\w.-]", "_", model)


def _commit() -> str:
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = git("status", "--porcelain", "--untracked-files=no")
    return git("rev-parse", "--short", "HEAD") + (" + незакоммиченные изменения" if dirty else "")


def _one_run(folder: Path, run_dir: Path, model: str, strategy: str) -> None:
    for attempt in (1, 2):
        started = datetime.now()
        t0 = time.perf_counter()
        try:
            run_tender(folder, run_dir, strategy=strategy)
        except Exception as e:  # recorded and reported; the key is never part of these messages
            status = getattr(e, "status_code", None)
            transient = status == 429 or (status is not None and status >= 500)
            if transient and attempt == 1:
                wait = RATE_LIMIT_WAIT if status == 429 else RETRY_WAIT_5XX
                print(f"  HTTP {status}, waiting {wait} s, one retry", flush=True)
                time.sleep(wait)
                continue
            error = {"model": model, "started": started.isoformat(timespec="seconds"),
                     "seconds": round(time.perf_counter() - t0, 1), "status": status,
                     "error": f"{type(e).__name__}: {re.sub(r'.user_id.: .[^,}]*', '', str(e))[:400]}"}
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "error.json").write_text(json.dumps(error, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  failed: {error['error'][:200]}", flush=True)
            return
        meta = {"model": model, "extraction": strategy, "started": started.isoformat(timespec="seconds"),
                "seconds": round(time.perf_counter() - t0, 1), "usage": dict(LAST_USAGE)}
        (run_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "error.json").unlink(missing_ok=True)
        print(f"  ok: {meta['seconds']} s, {meta['usage']}", flush=True)
        return


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Eval tender extraction against gold markup")
    parser.add_argument("tender_id")
    parser.add_argument("--runs", type=int, default=3, help="target number of runs for LLM_MODEL (default 3)")
    parser.add_argument("--no-llm", action="store_true", help="only re-evaluate runs already saved")
    parser.add_argument("--gold-note", help="extra header line about the gold version")
    parser.add_argument("--note", action="append", default=[], help="extra header line (repeatable)")
    parser.add_argument("--extraction", choices=STRATEGIES, default="v1",
                        help="extraction prompt for new runs (default v1)")
    args = parser.parse_args()

    gold = json.loads((ROOT / "data" / "tenders" / "gold" / f"{args.tender_id}.json").read_text(encoding="utf-8"))
    if gold["_meta"]["status"] != "done":
        sys.exit("gold markup is not done (run scripts/notes_to_gold.py and fix the listed quotes)")
    folder = ROOT / "data" / "tenders" / "raw" / args.tender_id
    docs = load_tender(folder)
    runs_root = ROOT / "outputs" / "tenders" / args.tender_id / "runs"

    if not args.no_llm:
        model = os.environ.get("LLM_MODEL")
        if not model:
            sys.exit("LLM_MODEL is not set")
        for i in range(1, args.runs + 1):
            run_dir = runs_root / model_slug(model) / args.extraction / str(i)
            if (run_dir / "requirements.json").exists():
                print(f"{model} {args.extraction} run {i}: already saved, skipped", flush=True)
                continue
            print(f"{model} {args.extraction} run {i}:", flush=True)
            _one_run(folder, run_dir, model, args.extraction)

    models: list[ModelRuns] = []
    variant_dirs = [v for m in sorted(runs_root.iterdir()) if m.is_dir()
                    for v in sorted(m.iterdir()) if v.is_dir() and v.name in STRATEGIES] if runs_root.exists() else []
    for variant_dir in variant_dirs:
        run_dirs = sorted((d for d in variant_dir.iterdir() if d.is_dir() and d.name.isdigit()), key=lambda d: int(d.name))
        runs, before, metas, failed = [], [], [], []
        for d in run_dirs:
            if (d / "requirements.json").exists():
                model_req = TenderRequirements.model_validate_json((d / "requirements.json").read_text(encoding="utf-8"))
                runs.append(evaluate_run(model_req, gold, docs))
                before.append(evaluate_run(model_req, gold, docs, short_gold_fix=False))
                meta_file = d / "run_meta.json"
                metas.append(json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {})
            elif (d / "error.json").exists():
                failed.append({"run": d.name, **json.loads((d / "error.json").read_text(encoding="utf-8"))})
        if runs:
            name = next((m["model"] for m in metas if m.get("model")), variant_dir.parent.name)
            models.append(ModelRuns(f"{name} · {variant_dir.name}", runs, metas, failed, before))
    if not models:
        sys.exit(f"no saved runs in {runs_root}")

    dates = sorted(meta.get("started", "") for m in models for meta in m.metas if meta.get("started"))
    header = {"dates": f"{dates[0]} … {dates[-1]}" if dates else "?", "commit": _commit(),
              "gold_note": args.gold_note, "notes": args.note}
    out = ROOT / "eval" / "results" / f"{args.tender_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(args.tender_id, header, gold, models), encoding="utf-8")
    print("\n".join(comparison_table(models)))
    print(f"\n-> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
