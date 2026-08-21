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
> or business documents are included.

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
   │              │   LLM structured-output (production, stubbed)
   └──────┬───────┘
          ▼
   Structured Project Data
   {service, quantity, height_m, location, urgency}
          │
          ▼
   ┌──────────────┐
   │  Calculator  │   deterministic pricing: base_price × qty ×
   │              │   height_coefficient × urgency_multiplier
   └──────┬───────┘
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

**Calculation:** `4200 × 3.50 BYN × 1.2 (height coeff.) = 17 640.00 BYN`

**Output:** `outputs/facade_cleaning_proposal.docx` — a formatted proposal with
the cost breakdown table and a flagged-warnings section for anything the
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

### Evaluation

Extraction accuracy is measured against a fixed set of synthetic cases
(`data/eval_cases/cases.json`) rather than eyeballed:

```bash
python src/evaluate.py
```

```
Extractor: rule_based
Cases: 8
Exact-match rate: 87.5%

Field accuracy:
  service       100.0%
  quantity      100.0%
  unit          100.0%
  height_m       87.5%
  location      100.0%
  urgency       100.0%
```

The one failing case is deliberate, not swept under the rug: it uses English
phrasing ("height up to 20 meters") that the Russian-pattern regex extractor
doesn't catch — the exact kind of input the LLM extraction path (below) is
meant to handle instead. `tests/test_evaluate.py` guards against this
accuracy silently regressing further.

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

Not wired into `main.py` / `app.py` by default — both use
`extract_rule_based()` so the demo stays runnable with zero setup. Swapping
in `extract_with_llm()` is a one-line change once you have an endpoint.

## Deployment options

This pipeline can run in three shapes depending on who uses it:

| Shape | When it fits | What's needed |
|---|---|---|
| **CLI** (`src/main.py`) | Demo / portfolio use, developer's own machine | Nothing — Python only |
| **Local UI** (`app.py`, Streamlit) | One person, one computer, no need for remote access | Nothing — runs locally, no server |
| **Web service** (FastAPI, not in this repo) | Multiple users, remote access, or embedding into another system (Telegram bot, CRM, website form) | A deployed backend (VPS/hosting) + a database instead of the flat JSON price list |

## Project structure

```
data/
├── demo_requests/       # synthetic incoming customer emails (.txt)
├── eval_cases/           # synthetic cases with expected extraction (for evaluate.py)
├── pricing/              # synthetic price list (JSON)
├── knowledge/            # synthetic company/services/pricing-rules docs
src/
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
└── test_evaluate.py       # accuracy regression guard
outputs/                   # generated .docx proposals (demo run artifacts)
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
  ⬜ PDF/DOCX request ingestion (currently text only).
  ⬜ RAG over `data/knowledge/` (Qdrant) so pricing/service lookups aren't a
  flat JSON table.

- **v0.3 — Agent**
  Give the LLM tools instead of a fixed pipeline: `search_knowledge()`,
  `get_service()`, `calculate_price()`, `find_similar_proposal()`,
  `generate_proposal()` — and let it decide the sequence, similar to the
  tool-calling agent in
  [`langgraph-research-agent`](https://github.com/q6066697/langgraph-research-agent).
  Deliberately not attempted before the pipeline actually needs
  non-deterministic sequencing — the current parse → extract → validate →
  calculate → generate flow doesn't.

## Known limitations of the current demo

- `extractor.extract_rule_based()` is a regex extractor tuned to Russian
  phrasing — the eval harness (`python src/evaluate.py`) shows it at 87.5%
  exact-match on 8 synthetic cases, with the one failure being English
  phrasing it doesn't catch. `extract_with_llm()` in `llm_extractor.py` is a
  real (not stub) implementation of the fix, just not wired in by default.
- Input is text only — no PDF/DOCX request parsing yet (see roadmap).
- Pricing data, company profile, and all knowledge-base content are fictional.
