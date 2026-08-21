"""
Local Streamlit UI for the AI Commercial Proposal Assistant.

No server / hosting needed — this runs entirely on the user's own machine:

    streamlit run app.py

It reuses the exact same extraction/calculation/generation modules as
src/main.py (the CLI version); this file only adds a human-friendly
front end around them. Nothing here talks to the network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from extractor import extract_rule_based  # noqa: E402
from calculator import calculate_price  # noqa: E402
from proposal_generator import generate_proposal_docx  # noqa: E402

ROOT = Path(__file__).resolve().parent
DEMO_REQUESTS_DIR = ROOT / "data" / "demo_requests"
OUTPUTS_DIR = ROOT / "outputs"

st.set_page_config(page_title="AI Commercial Proposal Assistant", page_icon="📄", layout="centered")

st.title("📄 AI Commercial Proposal Assistant")
st.caption(
    "Demo build — locally-run, no server. Extraction is rule-based for this demo; "
    "see src/extractor.py for the LLM-extraction stub used in production."
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
    extracted = extract_rule_based(request_text)

    if extracted.service is None:
        st.error(
            "Не удалось распознать услугу в тексте заявки. "
            "Демо-версия понимает: мойку фасада, покраску металлоконструкций, герметизацию швов."
        )
    else:
        breakdown = calculate_price(extracted)

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Извлечённые параметры")
            st.json(extracted.to_dict())
        with col2:
            st.subheader("Расчёт стоимости")
            st.json(breakdown.to_dict())

        if breakdown.warnings:
            st.warning("Требует проверки перед отправкой клиенту:\n\n" + "\n".join(f"- {w}" for w in breakdown.warnings))

        st.metric("ИТОГО", f"{breakdown.total:.2f} {breakdown.currency}")

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
