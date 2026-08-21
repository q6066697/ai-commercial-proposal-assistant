"""
Production extraction: a real LLM call with structured output, validated
against the same `ExtractedRequest` Pydantic schema the rule-based extractor
uses.

Uses the OpenAI-compatible chat completions API, which means this works
against:

- A cloud API (OpenAI, or any OpenAI-compatible provider) — set
  LLM_API_BASE / LLM_API_KEY / LLM_MODEL accordingly, or
- A **self-hosted model**, e.g. Qwen2.5-7B-Instruct served via vLLM (vLLM
  exposes an OpenAI-compatible server out of the box) — point LLM_API_BASE
  at your own server, e.g. http://localhost:8000/v1, with any placeholder
  LLM_API_KEY. This is the path used in
  https://github.com/q6066697/langgraph-research-agent
  (see docs/self-hosted-vllm.md there for the vLLM setup itself).

Environment variables:
    LLM_API_BASE   base URL of the OpenAI-compatible endpoint (required)
    LLM_API_KEY    API key / bearer token (required by the client, can be
                   any non-empty placeholder string for a local vLLM server
                   that doesn't check it)
    LLM_MODEL      model name as registered on that endpoint (required)

Not called by default anywhere in main.py / app.py — the demo pipeline uses
extract_rule_based() so it runs with zero setup. Wire this in explicitly
(see README's "LLM extraction" section) once you have an endpoint to point
it at.
"""
from __future__ import annotations

import json
import os

from schema import ExtractedRequest

SYSTEM_PROMPT = """You extract structured project parameters from a customer's
commercial-proposal request (Russian or English, industrial rope-access /
high-altitude services company). Return ONLY a JSON object matching this
schema, no other text:

{
  "service": "facade_cleaning" | "metal_structures_painting" | "seam_sealing" | null,
  "quantity": number | null,
  "unit": "m2" | "m" | null,
  "height_m": number | null,
  "location": string | null,
  "urgency": "standard" | "urgent_within_week"
}

Use null for anything not stated or not confidently inferable — do not guess.
"unit" is "m2" for facade_cleaning/metal_structures_painting, "m" for seam_sealing.
"""


def extract_with_llm(text: str) -> ExtractedRequest:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError(
            "The `openai` package is required for extract_with_llm(). "
            "Install it with: pip install openai"
        ) from e

    api_base = os.environ.get("LLM_API_BASE")
    api_key = os.environ.get("LLM_API_KEY")
    model = os.environ.get("LLM_MODEL")

    if not all([api_base, api_key, model]):
        raise RuntimeError(
            "extract_with_llm requires LLM_API_BASE, LLM_API_KEY and LLM_MODEL "
            "environment variables. Point LLM_API_BASE at a cloud OpenAI-compatible "
            "API, or at your own self-hosted vLLM server (e.g. http://localhost:8000/v1) "
            "-- see this function's docstring for details."
        )

    client = OpenAI(base_url=api_base, api_key=api_key)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    raw = response.choices[0].message.content
    parsed = json.loads(raw)

    return ExtractedRequest(
        service=parsed.get("service"),
        quantity=parsed.get("quantity"),
        unit=parsed.get("unit"),
        height_m=parsed.get("height_m"),
        location=parsed.get("location"),
        urgency=parsed.get("urgency", "standard"),
        raw_text=text,
    )
