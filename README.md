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

## Project structure

```
data/
├── demo_requests/       # synthetic incoming customer emails (.txt)
├── pricing/              # synthetic price list (JSON)
├── knowledge/            # synthetic company/services/pricing-rules docs
src/
├── extractor.py          # request → structured data (rule-based demo + LLM stub)
├── calculator.py         # structured data → price breakdown (deterministic)
├── proposal_generator.py # price breakdown → .docx
├── main.py                # CLI pipeline runner
tests/
└── test_pipeline.py      # extraction + calculation checks
outputs/                  # generated .docx proposals (demo run artifacts)
```

## Roadmap

- **v0.1 — Rule-based MVP (this repo)**
  Regex extraction, JSON price list, DOCX generation. No external dependencies,
  runs fully offline so reviewers don't need an API key.

- **v0.2 — RAG**
  Replace the static price list lookup with retrieval over `data/knowledge/`
  (Qdrant) so the assistant can answer from previous proposals and service
  descriptions, not just a fixed table.

- **v0.3 — Agent**
  Give the LLM tools instead of a fixed pipeline: `search_knowledge()`,
  `get_service()`, `calculate_price()`, `find_similar_proposal()`,
  `generate_proposal()` — and let it decide the sequence, similar to the
  tool-calling agent in
  [`langgraph-research-agent`](https://github.com/q6066697/langgraph-research-agent).

## Known limitations of the v0.1 demo

- `extractor.extract_rule_based()` is a regex extractor tuned to the three
  sample requests in `data/demo_requests/` — it will not generalize to messy
  real-world emails. `extractor.extract_with_llm()` documents where a real
  structured-output LLM call replaces it in production; swapping the two
  functions in `main.py` is the only change needed.
- Pricing data, company profile, and all knowledge-base content are fictional.
