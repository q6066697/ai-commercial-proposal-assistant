"""
End-to-end tender pipeline with a fake LLM on a synthetic two-file tender:
folder -> requirements.json (schema-valid, quotes checked) -> quote -> .docx.
"""
import json
import sys
from pathlib import Path

from docx import Document
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tender.pipeline import run_tender, summary
from tender_fixtures import ev, sample, write_tender

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schemas" / "tender_requirements.schema.json").read_text(encoding="utf-8"))


def docx_text(path: Path) -> str:
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        parts += [" | ".join(c.text for c in row.cells) for row in table.rows]
    return "\n".join(parts)


def llm_answer() -> dict:
    data = sample()
    data["requirements"].append({
        "text": "Лицензия МЧС", "category": "license", "mandatory": True, "cost_impact": "high",
        "evidence": ev("Исполнитель обязан иметь лицензию МЧС России"),  # invented
    })
    data["flags"].append({"text": "Обязательный осмотр объекта до подачи заявки",
                          "evidence": ev("Осмотр объекта до подачи заявки обязателен")})  # invented
    return data


def test_tender_folder_to_proposal(tmp_path):
    folder = write_tender(tmp_path / "demo_bank")
    calls = []

    def fake_llm(messages, schema):
        calls.append(messages)
        return json.dumps(llm_answer(), ensure_ascii=False)

    result = run_tender(folder, tmp_path / "out", llm_call=fake_llm)

    assert len(calls) == 1
    assert result.grounding.total == 12 and result.grounding.verified == 10
    assert result.quote.total == 19332.72  # 10740.4 × 1.50 × 1.2 (height unknown)

    saved = json.loads((tmp_path / "out" / "requirements.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(SCHEMA).iter_errors(saved)) == []
    assert saved["tender"]["source_docs"] == ["contract.txt", "tz.txt"]
    assert saved["requirements"][2]["evidence"] == {
        "quote": "Исполнитель обязан иметь лицензию МЧС России", "doc": "tz.txt",
        "verified": False, "match": "not_found"}
    assert json.loads((tmp_path / "out" / "grounding.json").read_text(encoding="utf-8"))["unverified"] == 2
    assert saved["flags"][1]["evidence"]["match"] == "not_found"

    text = docx_text(tmp_path / "out" / "proposal.docx")
    assert "Заказчик: КБ «Демо Кредит» ООО" in text
    assert "ИТОГО: 19 332,72 BYN" in text
    assert "Лицензия МЧС | лицензия | да | высокое | tz.txt ⚠ не подтверждено" in text
    assert "Какова высота здания?" in text
    assert "Цена ориентировочная: прайс для РБ" in text
    # confirmed flag shown with its quote, invented one left out
    assert "Полная ответственность за ущерб имуществу заказчика (contract.txt:" in text
    assert "Обязательный осмотр объекта" not in text

    report = summary(result)
    assert "Цитаты: подтверждено 10 из 12" in report and "not_found" in report
    assert "✗ not_found Обязательный осмотр объекта до подачи заявки" in report


def test_ready_requirements_json_skips_the_llm(tmp_path):
    folder = write_tender(tmp_path / "demo_bank")
    data = sample()
    data["schedule"]["mode"] = "on_request"
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"_meta": {"status": "done"}, **data}, ensure_ascii=False), encoding="utf-8")

    def no_llm(messages, schema):
        raise AssertionError("LLM must not be called")

    result = run_tender(folder, tmp_path / "out", llm_call=no_llm, requirements_path=gold)
    assert result.quote.mode == "unit_prices" and result.quote.total is None
    assert result.quote.unit_prices[0].price_per_unit == 1.8
    text = docx_text(tmp_path / "out" / "proposal.docx")
    assert "1,80 / м²" in text and "Минимальная сумма одной заявки — 650,00 BYN" in text
