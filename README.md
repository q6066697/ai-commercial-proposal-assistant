# AI Commercial Proposal Assistant

AI-powered assistant for automated generation of commercial proposals (KP) from
customer requirements. Extracts project parameters from a free-form request,
calculates pricing using deterministic business rules, and generates a
structured DOCX proposal — with a human-in-the-loop review step before
anything goes to a customer.

> **This project is based on a real-world business automation scenario**
> identified during a discovery phase with an industrial rope access
> (industrial climbing) services company. The original business workflow
> involved significant manual effort in reading customer requirements, searching
> old proposals for reusable pricing, calculating costs by hand, and retyping
> everything into Word.
>
> **All company data, prices, and documents in this repository are synthetic**
> and created for demonstration purposes only. No real client data, pricing,
> or business documents are included. The only exception is the tender
> evaluation: its gold markup quotes short fragments of public tender
> documents (the documents themselves are not in the repository).

## Project status

Working prototype. A pilot with the real client (the rope-access company the
scenario comes from) is planned after the end of their working season; until
then the pipeline has been run only on synthetic requests and public tender
documents.

## Evaluation

What is measured: extraction of contractor requirements from real public
tender documentation — a technical specification (ТЗ) plus a draft contract,
facade and window washing of a bank office in Krasnodar (`krasnodar_bank`);
the second prompt was then checked on a held-out tender (`ipu_ran`, below).

**Gold markup:** `data/tenders/gold/krasnodar_bank.json`, v1.1 — 63
requirements, 2 scope lines, 8 flags, 8 gaps; every quote is checked against
the documents by the same grounding check the model output goes through. The
draft of the gold markup was prepared with AI help; every line was then
checked by hand against the documents.

**What counts as a requirement** (gold markup and reports): anything that
binds the contractor or limits the contractor's rights, including penalties,
liability, the customer's right to refuse the contract, the competent court,
confidentiality, notice periods. The customer's obligations, force majeure,
the contract's entry into force, notices of reorganization are not
requirements.

**Matching, no LLM judge:** a model fact matches a gold line if their verbatim
quotes overlap in the source text (≥50% of the shorter one); fallback — by
text (word stems). Only facts with a confirmed quote count, and groundedness
is strict: a quote counts only if it occurs in its file after normalizing
whitespace, quote style, ё/е and case.

Two free models on OpenRouter, 3 runs each, mean (min–max)
([full report](eval/results/krasnodar_bank.md)):

| Metric | `dots-3-note-preview:free` | `nemotron-3.5-super-vl:free` |
|---|---|---|
| Requirement recall (all model facts with a quote) | 39% (33–44%) | 38% (32–43%) |
| Requirement recall, strict (`requirements[]` only) | 23% (17–32%) | 21% (17–29%) |
| Precision of `requirements[]` | 90% (88–92%) | 96% (89–100%) |
| Scope volumes (service + area + repeats) | 100% | 100% |
| Groundedness (confirmed quotes) | 79% (71–93%) | 93% (89–97%) |
| Time per run | 441 s (266–748) | 116 s (88–157) |

What the numbers show:

- **Recall is the weak spot.** Both models find about 40% of the 63
  requirements. Across all 6 runs, materials and equipment (17%) and
  liability and penalties (24%) are found least often, staff admissions
  (57%) most often.
- **What is found is almost always right.** Precision is 90–96%, and the
  "extra" items are mostly real clauses outside the gold markup — two of them
  were added to the gold after manual review (v1.0 → v1.1).
- **Scope volumes are 100% in all 6 runs:** the facade area comes from the ТЗ
  although the contract leaves it blank, and the window area, which neither
  document gives, stays empty instead of being invented.
- **No invented quotes.** Every unconfirmed quote (32 in 6 runs) is at least
  71% similar to the document text, and 28 of them ≥90% — typos, swapped
  words, English words inside Russian sentences, an extra space, a lost table
  separator. They are still not counted: groundedness stays strict.

**Prompt v2 on a held-out tender.** The table above is the first prompt (v1).
Prompt v2 asks for every contractor obligation, one item per clause. It was
tuned on `krasnodar_bank` and then checked on `ipu_ran` (window washing for a
research institute, ТЗ only): the `ipu_ran` gold markup was made before any
run, and the prompt was frozen at commit `71f35d4`. `nemotron-3.5-super-vl:free`,
3 runs per cell, mean (min–max)
([krasnodar_bank report](eval/results/krasnodar_bank.md),
[ipu_ran report](eval/results/ipu_ran.md)):

| Metric | krasnodar_bank v1 | krasnodar_bank v2 (tuning) | ipu_ran v1 | ipu_ran v2 (held-out) |
|---|---|---|---|---|
| Requirement recall (all model facts with a quote) | 38% (32–43%) | 88% (81–97%) | 39% (34–44%) | 90% (86–95%) |
| Requirement recall, strict (`requirements[]` only) | 21% (17–29%) | 79% (65–87%) | 23% (17–28%) | 83% (73–92%) |
| Precision of `requirements[]` | 96% (89–100%) | 89% (81–93%) | 91% (91–92%) | 86% (61–98%) |

The recall gain holds on the tender the prompt never saw. The 61% precision
run on `ipu_ran` listed the normative acts of section 7 one per item (25 of its
27 extra items), while the gold markup keeps that list as one line — a
difference in granularity, not invented requirements.

Limitations: two tenders; free models only; gaps are matched by text only, so
that number is approximate; the matching rule for one-word gold lines was
fixed after the first run — before/after numbers on the same runs are in the
report.

Reproduce (the tender documents are third-party and not in the repository:
put them in `data/tenders/raw/krasnodar_bank/`, file names are in
`data/tenders/index.json`):

```bash
# fresh runs: 3 for the model in LLM_MODEL (.env or environment), then the report
LLM_MODEL=nvidia/nemotron-3.5-super-vl:free python scripts/eval_tender.py krasnodar_bank --runs 3

# re-evaluate runs already saved in outputs/tenders/ (git-ignored), no LLM calls
python scripts/eval_tender.py krasnodar_bank --no-llm
```

## Why this exists

Most "RAG chatbot" portfolio projects stop at retrieval. This one shows the
full loop a real back-office automation needs: **unstructured input → AI
extraction → structured data → deterministic calculation → generated document
→ human review**. Pricing is deliberately *not* left to the LLM — it's a
plain Python business-rules engine, so every quote is reproducible and
auditable.

## Architecture

```
Customer Request (free text)
          │
          ▼
   ┌──────────────┐
   │  Extraction  │   rule-based demo (this repo) /
   │              │   LLM structured output (selectable)
   └──────┬───────┘
          ▼
   Structured Project Data
   {service, quantity, height_m | floor, weight_kg, location, urgency, ...}
          │
          ▼
   ┌──────────────┐   deterministic pricing from data/price_list.json:
   │  Calculator  │   rate × qty × height × complex access → + urgency
   │              │   → + materials (separate line) → minimum order;
   └──────┬───────┘   or "manual estimate" / "needs clarification"
          ▼
   ┌──────────────┐
   │   Proposal   │   python-docx → structured DOCX
   │  Generator   │
   └──────┬───────┘
          ▼
     Draft .docx  ──►  Human Review  ──►  Approve/Edit  ──►  Sent to client
```

## Example run

**Input** (`data/demo_requests/facade_cleaning.txt`):
```
Просим предоставить коммерческое предложение на мойку фасада промышленного здания.
Услуга: мойка фасада
Площадь фасада: 4200 м2
Высота здания: до 18 метров
Местоположение объекта: г. Минск
```

**Extraction:**
```json
{
  "service": "facade_cleaning",
  "quantity": 4200.0,
  "unit": "m2",
  "height_m": 18.0,
  "location": "Минск",
  "urgency": "standard"
}
```

**Calculation** (price list v0.3, `data/price_list.json`):

| Line | Calculation | Sum, BYN | Rule |
|---|---|---|---|
| Мойка фасада: ставка × объём | 1,50 × 4200 м² | 6 300,00 | `services.facade_washing.rate` (market) |
| Коэффициент высоты | ×1,2 (18 м, tier «выше 15 м») | 1 260,00 | `pricing_rules.height_coefficients` (client) |
| Материалы | not required for this work | 0,00 | `pricing_rules.materials` (client) |
| **ИТОГО** | | **7 560,00** | |

Every line is signed with the path of the rule in the price list and its
source (`client` = confirmed by the client, `market` = market rate,
`assumption` = demo assumption), and the lines always sum to the total — the
client mostly edits prices before sending, so they need to see what the
number is made of. Work the price list marks as `manual_estimate_only`
(light boxes, welding, cargo over 500 kg, snow removal at snowfall peak...)
gets no number at all, only "требуется осмотр объекта / ручной расчёт"; a
request without a volume gets "needs clarification" instead of a 0 total.
See `data/knowledge/pricing_rules.md`.

(With the v0.1 demo price list, `data/pricing/demo_price_list.json`, the same
request was `4200 × 3.50 × 1.2 = 17 640.00 BYN`; that file is still loadable
and pinned in `tests/test_pipeline.py`.)

**Output:** `outputs/facade_cleaning_proposal.docx` — a formatted proposal with
the line-by-line cost table and a flagged-warnings section for anything the
extractor couldn't confidently fill in.

## Human-in-the-loop, not full automation

```
AI extraction → Draft Proposal → Human Review → Approve/Edit → Final Proposal
```

The pipeline never sends anything automatically. Every generated `.docx`
includes a **"requires review"** section listing what the extractor couldn't
confidently determine (missing height, missing location, etc.), so a human
always has the final say before a price reaches a customer.

## Running the demo

```bash
pip install -r requirements.txt

# Run one request
python src/main.py data/demo_requests/facade_cleaning.txt

# Run all demo requests
python src/main.py --all

# Run the test suite
pytest tests/ -v
```

### Local UI (no server needed)

For a non-technical user (e.g. the business owner this was scoped for), the
same pipeline is also wrapped in a local Streamlit UI — runs entirely on the
user's own machine, no hosting, no internet round-trip:

```bash
streamlit run app.py
```

Opens in the browser at `http://localhost:8501`: paste or pick a sample
request, click "Сформировать КП", review the extracted parameters and price
breakdown, download the `.docx`. This is the deployment shape that fits a
single person working from one computer — see "Deployment options" below for
when a server is actually needed instead.

### Evaluation: single requests (synthetic cases)

Extraction accuracy is measured against fixed sets of synthetic cases rather
than eyeballed, and reported per field, per case type, with latency (and,
for the LLM path, tokens and cost):

| Set | File | What it contains |
|---|---|---|
| `clean` | `data/eval_cases/cases.json` | 8 tidy, form-like requests — regression baseline |
| `hard` | `data/eval_cases/hard_cases.json` | 22 messy requests: chat style, typos, synonyms, number formats (`4 200 м²`, `1,5 тыс.`), city in another case (`в Витебске`), implicit / negated urgency, floors instead of meters, missing fields, out-of-scope work, distractor numbers, mixed language, prompt injection |

```bash
python src/evaluate.py                                   # rule-based, clean set
python src/evaluate.py --set hard                        # rule-based, hard set
python src/evaluate.py --extractor llm --set all --save  # LLM, both sets, JSON report
```

**Results** (exact match = all 6 fields correct):

| Extractor | clean (8) | hard (22) | Mean latency | Notes |
|---|---|---|---|---|
| Rule-based (regex) | 87.5% | 50.0% | ~0 s | free, deterministic, offline (was 45.5% on hard before the "не срочно" fix) |
| LLM — `gpt-oss:120b-cloud` via Ollama | **100%** | **95.5%** | 3.3 s | ~690 input / ~200 output tokens per request |
| LLM — `qwen2.5:3b` locally (CPU only) | — | — | ~4–5 min | run stopped: impractical on CPU-only hardware |

Raw reports: `outputs/eval_results/`. The LLM rows were measured with the
v0.2 prompt; the prompt now also covers the v0.3 services, floors, cargo
weight and dismantling, so they need a re-run.

What the numbers show:

- **A clean test set flatters rules.** 87.5% on form-like input drops to
  50% on messy input: the regex extractor fails on every typo, synonym,
  chat-style and mixed-language case, and on implicit urgency ("до пятницы").
  Negated urgency ("не срочно") used to be matched as urgent; fixed in v0.3,
  since urgency is now surcharged only when explicitly requested.
- **The LLM handles exactly what rules can't.** Typos, synonyms, chat style,
  `1,5 тыс.`, city names in other grammatical cases, implicit and negated
  urgency, English input — all correct. It also ignored the embedded
  prompt-injection instruction and the distractor numbers (phone, year).
- **The one LLM miss is a design lesson, not a prompt problem.** In
  `hard_013` (service given, no quantity) the model returned `unit: null`
  although the unit is fully determined by the service. Anything that can be
  derived deterministically should be computed in code, not requested from
  the model — the same principle as pricing. Next step: derive `unit` from
  `service` in the schema. Not applied yet, so the number above isn't tuned
  to the test set.
- **The hard set found a real bug.** `1,5 тыс. м2` crashed the regex
  extractor with a `ValueError`; it now returns a missing field the reviewer
  sees instead of crashing the pipeline (regression test in
  `tests/test_evaluate.py`).
- **Privacy vs. hardware.** A small local model keeps customer data on the
  machine but was far too slow on a CPU-only laptop; a cloud model is fast and
  accurate but sends the request text out. For real customer data the
  realistic options are a local model on a GPU (see the vLLM setup linked
  below) or a cloud API with anonymized input.

Locations are compared after normalization (`г. Минск` = `Минск`,
`Могилёв` = `Могилев`), so a correct city with a different prefix isn't
counted as an error. `tests/test_evaluate.py` guards the clean-set baseline
and checks the pipeline never crashes on the hard set.

### LLM extraction (production path)

`src/llm_extractor.py` is a real implementation (not a stub) using the
OpenAI-compatible chat completions API, so it works against either:

- a cloud API (OpenAI or any OpenAI-compatible provider), or
- a **self-hosted model** — e.g. Qwen2.5-7B-Instruct served via vLLM, the
  same setup used in
  [`langgraph-research-agent`](https://github.com/q6066697/langgraph-research-agent/blob/master/docs/self-hosted-vllm.md).
  vLLM exposes an OpenAI-compatible server, so no code changes are needed —
  just point `LLM_API_BASE` at it.

```bash
pip install openai
export LLM_API_BASE="http://localhost:8000/v1"   # or a cloud endpoint
export LLM_API_KEY="..."
export LLM_MODEL="Qwen2.5-7B-Instruct"            # or e.g. "gpt-4o-mini"

python src/evaluate.py --extractor llm
```

A local model through **Ollama** works the same way — Ollama serves an
OpenAI-compatible API:

```bash
export LLM_API_BASE="http://localhost:11434/v1"
export LLM_API_KEY="ollama"          # any non-empty placeholder
export LLM_MODEL="qwen2.5:7b"
```

The prompt treats the customer text as data inside `<request>` tags and
explicitly refuses embedded instructions (see the `prompt_injection` case).

In the Streamlit UI the extractor is selectable in the sidebar once these
variables are set; the CLI demo (`main.py`) keeps the zero-setup rule-based
path.

## Tender documentation

The client's main time sink is "reading the whole tender to find every
requirement". The tender track turns a folder of tender documents into a
requirements sheet with evidence, plus a priced draft proposal:

```
data/tenders/raw/<id>/  (ТЗ, договор, ... .docx / .pdf / .txt)
          │  src/tender/loader.py      text in document order, tables kept
          ▼
   LLM structured output   src/tender/llm_extract.py — one call per tender
          │                (long tenders: chunks + deterministic merge)
          ▼
   TenderRequirements      schemas/tender_requirements.schema.json
          │  src/tender/grounding.py  every quote must literally occur in its file
          ▼
   price_tender()          src/tender/pricing.py — same price list & rules as
          │                the calculator; repeats; on_request → unit prices
          ▼
   outputs/tenders/<id>/   requirements.json, grounding.json, quote.json, proposal.docx
```

```bash
python src/main.py --tender data/tenders/raw/krasnodar_bank         # LLM (LLM_* env vars)
python src/main.py --tender data/tenders/raw/krasnodar_bank \
    --requirements data/tenders/gold/krasnodar_bank.json            # no LLM: ready markup
```

- **Groundedness is checked in code, not by the model.** Each `evidence.quote`
  must occur in the named file after normalizing whitespace, NBSP, quote/dash
  style, ё/е and case. Statuses: `verified`, `wrong_doc` (found in another file
  of the tender, `doc` corrected), `not_found`, `too_short`, `value_mismatch`
  (the volume/height number is not in its own quote). Unconfirmed facts stay in
  the JSON with `verified: false`, are marked ⚠ in the proposal and are never
  priced. `verified` / `match` are not in the schema the LLM receives.
  Flags carry a quote too and are checked the same way; a flag without a
  confirmed quote is not shown in the proposal. Gaps (questions about what is
  *missing*) have no quote by nature.
- **Which model may see which documents.** Public tenders (published on
  procurement platforms) may be processed by a cloud model. Client documents
  (anything received privately from a customer or the client's own files) —
  only by a local model (Ollama / vLLM on the client's machine or server),
  never sent to a cloud API.
- **One tender = several files.** All files go into one request, so a blank in
  the contract template ("площадью _________ кв. м") is filled from the ТЗ; a
  "blank"/"missing" gap whose field has a value after combining the files is
  dropped in code.
- **Pricing.** Minimum order once per tender, not per line; `repeats` multiply
  a line; framework contracts (`schedule.mode = on_request`) get a price per
  unit and a note that the minimum applies to each request. Tenders outside
  Belarus carry "цена ориентировочная, прайс для РБ".
- **Documents are not published.** `data/tenders/raw/` and `outputs/tenders/`
  are git-ignored; `data/tenders/index.json` lists file names and roles only.
  Legacy `.doc` is converted once with `scripts/convert_doc.py` (Windows +
  Word, read-only, macros disabled); the pipeline itself reads only
  `.docx` / `.pdf` / `.txt`.
- **Gold markup** for evaluation: `data/tenders/gold/<id>.json`, written as
  notes (`<id>_notes.md`) and converted with `scripts/notes_to_gold.py`, which
  lists every quote it cannot find instead of fixing it. Files marked `"done"`
  are validated against the schema and their quotes are grounding-checked in
  `tests/test_tender_models.py`. Metrics: see "Evaluation" above.

## API service

The single-request pipeline as an async HTTP service: FastAPI + PostgreSQL
(SQLAlchemy 2.0, asyncpg, Alembic). It reuses the same extractor, calculator
and DOCX generator as the CLI and Streamlit, which keep working without a
database. Extraction is rule-based only for now; **no LLM is involved, and
the price is always deterministic code.**

| Endpoint | What it does |
|---|---|
| `GET /health` | 200, or 503 if the database is unavailable |
| `POST /requests` | Text → extracted fields, stored; 201. Invalid or impossible values → 422 |
| `GET /requests/{id}` | Stored request with its positions |
| `POST /requests/{id}/quote` | Price + DOCX: 201, or 200 with the existing quote. Service not detected → 422 with `clarify_fields`; no document is made |
| `GET /quotes/{id}/download` | The DOCX draft |

- **Idempotent quotes.** The same request data on the same price list version
  always returns the same quote, also under parallel calls (row lock + unique
  constraint). A new price list version makes a new quote; the old one stays.
  When the calculation *code* changes, increase `CALCULATION_VERSION` in
  `src/api/service.py`, otherwise old requests keep their old result.
- **Prices.** `data/price_list.json` is the master; the database holds an
  immutable copy of every imported version. The import rejects changed prices
  or rules under an already imported `meta.version`. Importing an older,
  known version makes it active again. A test checks that quotes priced from
  the database and from the JSON are identical line by line.
- **Money.** `NUMERIC(12,2)` in the database, a string in JSON (`"7560.00"`).
  `total` is `null` for manual estimates and missing data, never 0.
- **Tables:** `price_list_versions`, `price_list`, `requests`,
  `request_items`, `quotes` (see `src/db/models.py`).
- **No authentication yet:** keep it on localhost. Both the API and the
  database port listen on `127.0.0.1` only.

All commands below are for Windows PowerShell, run from the project folder.
Docker Desktop must be running.

### First start

```powershell
# 1. Virtual environment and dependencies
python -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install -r requirements.txt

# 2. Settings: creates .env with a random database password.
#    If you already have a .env, add the POSTGRES_* lines from .env.example to it instead.
if (-not (Test-Path .env)) {
    (Get-Content .env.example) -replace '^POSTGRES_PASSWORD=$', "POSTGRES_PASSWORD=$([guid]::NewGuid().ToString('N'))" |
        Set-Content .env -Encoding ascii
}

# 3. Database
docker compose up -d --wait

# 4. Migrations and prices
.\.venv-win\Scripts\alembic.exe upgrade head
.\.venv-win\Scripts\python.exe scripts\import_price_list.py
```

PostgreSQL stores the password in its volume on the first start: changing it
later in `.env` alone breaks the connection. The default port is 5433, so
that it doesn't clash with another local PostgreSQL (`POSTGRES_PORT`).

### Run

```powershell
.\.venv-win\Scripts\uvicorn.exe api.main:create_app --factory --app-dir src --port 8001
```

Interactive documentation: <http://127.0.0.1:8001/docs>. The same from
PowerShell, in a second window:

```powershell
$api = 'http://127.0.0.1:8001'
# ReadAllText, not Get-Content -Raw: PowerShell 5.1 turns the latter into an object in JSON
$text = [IO.File]::ReadAllText("$PWD\data\demo_requests\facade_cleaning.txt")
$body = [Text.Encoding]::UTF8.GetBytes((@{ text = $text; client_name = 'Demo' } | ConvertTo-Json))
$request = Invoke-RestMethod "$api/requests" -Method Post -ContentType 'application/json; charset=utf-8' -Body $body
$quote = Invoke-RestMethod "$api/requests/$($request.id)/quote" -Method Post
$quote.total                                                                        # 7560.00
Invoke-WebRequest "$api$($quote.download_url)" -OutFile "$env:TEMP\kp.docx" -UseBasicParsing
```

PowerShell 5.1 shows Cyrillic from JSON answers garbled; the data itself is
correct (use `/docs` to read it).

### After changing prices

```powershell
# edit data\price_list.json, change meta.version, then:
.\.venv-win\Scripts\python.exe scripts\import_price_list.py
```

### Migrations

```powershell
.\.venv-win\Scripts\alembic.exe current                                 # applied version
.\.venv-win\Scripts\alembic.exe check                                   # models match the migrations?
.\.venv-win\Scripts\alembic.exe revision --autogenerate -m "what changed"
.\.venv-win\Scripts\alembic.exe upgrade head
```

### Tests

```powershell
.\.venv-win\Scripts\python.exe -m pytest tests -q        # everything, about 2 minutes
.\.venv-win\Scripts\python.exe -m pytest tests -q -rs    # with reasons for skipped tests
```

Database tests create and use a separate database, `<POSTGRES_DB>_test`, and
refuse any other name. Without Docker they are skipped, the rest still runs.

### Stop

```powershell
docker compose stop       # data is kept
docker compose down -v    # deletes the database with all requests and quotes
```

## Deployment options

This pipeline can run in three shapes depending on who uses it:

| Shape | When it fits | What's needed |
|---|---|---|
| **CLI** (`src/main.py`) | Demo / portfolio use, developer's own machine | Nothing — Python only |
| **Local UI** (`app.py`, Streamlit) | One person, one computer, no need for remote access | Nothing — runs locally, no server |
| **API service** (`src/api/`, FastAPI + PostgreSQL) | Embedding into another system (Telegram bot, CRM, website form); later multiple users and remote access | Docker for the database; runs locally so far. Authentication and hosting are needed before remote access |

## Project structure

```
data/
├── demo_requests/       # synthetic incoming customer emails (.txt)
├── eval_cases/           # synthetic cases with expected extraction (for evaluate.py)
├── pricing/              # synthetic price list (JSON)
├── knowledge/            # synthetic company/services/pricing-rules docs
├── price_list.json       # price list v0.3 (client-calibrated rules)
├── tenders/
│   ├── index.json        # tenders: file names and roles (documents not in git)
│   ├── gold/             # hand-made reference markup per tender (notes + JSON)
│   └── raw/              # third-party tender documents (git-ignored)
eval/results/             # tender eval reports (krasnodar_bank.md)
schemas/
└── tender_requirements.schema.json
scripts/
├── convert_doc.py        # one-off .doc -> .docx via Word
├── make_gold_template.py # empty gold markup per tender
├── notes_to_gold.py      # gold notes (.md) -> gold JSON, quotes checked
├── eval_tender.py        # extraction runs + eval report
└── import_price_list.py  # price_list.json -> PostgreSQL (API service)
migrations/               # Alembic (API service)
docker-compose.yml        # PostgreSQL for the API service
.env.example              # settings template; .env itself is git-ignored
src/
├── api/                  # API service: endpoints, schemas, service logic
├── db/                   # API service: tables, sessions, price list import
├── settings.py           # API service settings (.env)
├── tender/               # tender track: loader, models, llm_extract,
│                         # grounding, pricing, proposal, pipeline
├── schema.py              # shared Pydantic ExtractedRequest schema
├── extractor.py           # request → structured data (rule-based demo)
├── llm_extractor.py       # request → structured data (real LLM structured output)
├── calculator.py          # structured data → price breakdown (deterministic)
├── proposal_generator.py  # price breakdown → .docx
├── evaluate.py             # accuracy evaluation against data/eval_cases/
├── main.py                 # CLI pipeline runner
app.py                      # local Streamlit UI (no server)
tests/
├── test_pipeline.py       # extraction + calculation checks
├── test_evaluate.py       # accuracy regression guard
├── test_tender_*.py       # tender track
├── test_api.py            # API service end to end (needs PostgreSQL)
└── test_db_*.py           # schema, migration, price list import (needs PostgreSQL)
outputs/                   # generated .docx proposals (demo run artifacts)
└── quotes/               # proposals of the API service (git-ignored)
```

## Roadmap

- **v0.1 — Rule-based MVP**
  Regex extraction, JSON price list, DOCX generation. No external dependencies,
  runs fully offline so reviewers don't need an API key.

- **v0.2 — Structured extraction + evaluation (in progress)**
  ✅ Pydantic schema shared by both extractors (`src/schema.py`), with
  automatic missing-field detection.
  ✅ Real LLM structured-output extraction (`src/llm_extractor.py`) — works
  against a cloud API or a self-hosted vLLM server, not wired in by default.
  ✅ Evaluation harness (`src/evaluate.py`) with field-level accuracy against
  synthetic cases, plus a pytest regression guard.
  ✅ Tender documentation (.docx / .pdf / .txt folders) → requirements with
  code-checked evidence quotes → priced draft (see "Tender documentation").
  ✅ Tender extraction metrics against hand-checked gold markup, one tender,
  two free models (see "Evaluation").
  ✅ Extraction prompt v2 (exhaustive requirements): recall ~40% → ~90%,
  confirmed on a held-out tender (`ipu_ran`) with the prompt frozen.
  ⬜ Prompt v3: a list (e.g. of normative acts) as one requirement, not one
  item per entry; needs a new held-out tender (`tashkent_bank`) to check.
  ⬜ Better flags and gaps — v2 did not raise them (flags 33–43%, gaps
  17–38%).
  ⬜ LLM-as-a-Judge for matching and gaps, calibrated against the manual
  markup before its numbers are trusted.
  ⬜ Pilot with the client on a local model (client documents never go to a
  cloud API).
  ⬜ RAG over `data/knowledge/` (Qdrant) so pricing/service lookups aren't a
  flat JSON table.

- **API service** (a separate track, not tied to the version numbers above)
  ✅ Step 1: FastAPI + PostgreSQL, versioned price list, idempotent quotes,
  DOCX download (see "API service").
  ⬜ Several services in one request (see "Known issues").
  ⬜ LLM extraction through LiteLLM, with an eval set for single requests.
  ⬜ Queue (Redis), input from PDF / Excel, Telegram bot.

- **v0.3 — Agent**
  Give the LLM tools instead of a fixed pipeline: `search_knowledge()`,
  `get_service()`, `calculate_price()`, `find_similar_proposal()`,
  `generate_proposal()` — and let it decide the sequence, similar to the
  tool-calling agent in
  [`langgraph-research-agent`](https://github.com/q6066697/langgraph-research-agent).
  Deliberately not attempted before the pipeline actually needs
  non-deterministic sequencing — the current parse → extract → validate →
  calculate → generate flow doesn't.

## Known issues

- **The second service in a request is silently lost.** «Мойка фасада 100 м2 и
  мойка окон 900 м2, высота 10 м, г. Минск» is priced as facade washing only,
  650 BYN, without a warning. The extractor returns one service per request,
  so the CLI, Streamlit and the API are all affected (the API's
  `request_items` table already allows several positions). **Check every
  request by eye until this is fixed; it is the next step.**
- **The rule-based extractor misses ordinary wording.** «Нужно помыть фасад
  офисного здания, площадь 500 м2, высота 25 метров. Минск.» — the service is
  not recognized («помыть фасад» instead of «мойка фасада»), and the volume
  is lost with it, because the unit depends on the service. A city without
  «г.» is not recognized either; the height is. The result is a request for
  clarification, not a wrong price. This is the case for LLM extraction and
  an eval set of real requests in the next steps.
- **Impossible numbers crash the extractor.** A height over 500 m or a floor
  over 200 raises a validation error, and a very long weight becomes `inf`.
  The API answers 422; the CLI stops with a traceback.
- **The calculation works in `float`.** Results are rounded to kopecks and
  stored as `NUMERIC`; the calculator itself will be moved to `Decimal` later
  (increase `CALCULATION_VERSION` then).
- **The DOCX shows technical keys.** The «Основание» column holds paths like
  `services.facade_washing.rate (рынок)`: useful for the internal review, not
  for a customer. A separate customer version of the proposal is needed.
- **The API has no authentication** and no endpoint for editing a request: a
  correction is a new request.

## Known limitations of the current demo

- `extractor.extract_rule_based()` is a regex extractor tuned to Russian
  phrasing — the eval harness (`python src/evaluate.py`) shows it at 87.5%
  exact-match on 8 synthetic cases, with the one failure being English
  phrasing it doesn't catch. `extract_with_llm()` in `llm_extractor.py` is a
  real (not stub) implementation of the fix, just not wired in by default.
- Input is text only — no PDF/DOCX request parsing yet (see roadmap).
- Pricing data, company profile, and all knowledge-base content are fictional.
