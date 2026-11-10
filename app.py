"""
Local Streamlit UI for the AI Commercial Proposal Assistant.

No server / hosting needed — this runs entirely on the user's own machine:

    streamlit run app.py

It reuses the exact same extraction/calculation/generation modules as
src/main.py (the CLI version); this file only adds a human-friendly
front end around them. With the rule-based extractor nothing talks to the
network; the LLM extractor calls whatever endpoint LLM_API_BASE points at
(a local Ollama/vLLM server keeps everything on the machine).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from env import load_env  # noqa: E402
from extractor import extract_rule_based, extract_with_llm  # noqa: E402
from calculator import calculate_price  # noqa: E402
from proposal_generator import generate_proposal_docx  # noqa: E402

load_env()

ROOT = Path(__file__).resolve().parent
DEMO_REQUESTS_DIR = ROOT / "data" / "demo_requests"
OUTPUTS_DIR = ROOT / "outputs"

st.set_page_config(page_title="AI Commercial Proposal Assistant", page_icon="📄", layout="centered")

st.title("📄 AI Commercial Proposal Assistant")
st.caption("Demo build — locally-run, no server. All data is synthetic.")

# --- Extractor choice ----------------------------------------------------
llm_configured = all(os.environ.get(k) for k in ("LLM_API_BASE", "LLM_API_KEY", "LLM_MODEL"))
extractor_options = ["Правила (без настройки)"]
if llm_configured:
    extractor_options.append(f"LLM ({os.environ['LLM_MODEL']})")
extractor_choice = st.sidebar.radio("Способ извлечения", extractor_options)
if not llm_configured:
    st.sidebar.caption(
        "LLM-режим появится, если задать LLM_API_BASE, LLM_API_KEY и LLM_MODEL "
        "(см. README, раздел «LLM extraction»)."
    )

# --- Input -------------------------------------------------------------
demo_files = sorted(DEMO_REQUESTS_DIR.glob("*.txt"))
demo_names = ["— вставить свой текст —"] + [f.name for f in demo_files]
choice = st.selectbox("Загрузить пример заявки или ввести свою:", demo_names)

if choice != "— вставить свой текст —":
    default_text = (DEMO_REQUESTS_DIR / choice).read_text(encoding="utf-8")
else:
    default_text = ""

request_text = st.text_area("Текст заявки клиента", value=default_text, height=200)

generate_clicked = st.button("Сформировать КП", type="primary", disabled=not request_text.strip())

# --- Pipeline ------------------------------------------------------------
if generate_clicked:
    try:
        if extractor_choice.startswith("LLM"):
            with st.spinner("Модель извлекает параметры..."):
                extracted = extract_with_llm(request_text)
        else:
            extracted = extract_rule_based(request_text)
    except Exception as exc:
        st.error(f"Ошибка извлечения: {exc}")
        st.stop()

    # Called even without a detected service: negotiated work (e.g. light
    # boxes) is recognised by the price list's manual_estimate_only triggers.
    breakdown = calculate_price(extracted)
    if breakdown.status == "needs_clarification" and "service" in breakdown.clarify_fields:
        st.error(
            "Не удалось распознать услугу в тексте заявки. "
            "Список услуг — в data/price_list.json."
        )
    else:
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Извлечённые параметры")
            st.json(extracted.to_dict())
        with col2:
            st.subheader("Расчёт стоимости")
            st.json(breakdown.to_dict())

        if breakdown.warnings:
            st.warning("Требует проверки перед отправкой клиенту:\n\n" + "\n".join(f"- {w}" for w in breakdown.warnings))

        if breakdown.status == "ok":
            st.table([
                {"Статья": l.label, "Расчёт": l.formula, "Сумма": f"{l.amount:.2f}",
                 "Основание": f"{l.source} ({l.source_type})"}
                for l in breakdown.lines
            ])
            st.metric("ИТОГО", f"{breakdown.total:.2f} {breakdown.currency}")
        elif breakdown.status == "manual_estimate":
            st.metric("ИТОГО", "ручной расчёт")
        else:
            st.metric("ИТОГО", "нужно уточнение")

        output_path = OUTPUTS_DIR / "streamlit_generated_proposal.docx"
        generate_proposal_docx(extracted, breakdown, output_path, proposal_number="UI-DEMO")

        with open(output_path, "rb") as f:
            st.download_button(
                "⬇️ Скачать КП (.docx)",
                data=f.read(),
                file_name="commercial_proposal.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

        st.info(
            "Черновик сгенерирован. Это точка человеческой проверки — перед отправкой "
            "клиенту документ нужно просмотреть и при необходимости отредактировать."
        )
