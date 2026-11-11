"""
LLM tender extraction with a fake LLM: no API calls. Checks the prompt
contract, the validation retry, chunking of long files and the deterministic
merge of per-chunk answers (blank in the contract + figure in the ТЗ).
"""
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tender.llm_extract import drop_resolved_gaps, extract_tender, make_chunks, merge, split_text
from tender.loader import TenderDocument
from tender.models import TenderRequirements
from tender_fixtures import CONTRACT_TEXT, TZ_TEXT, ev, sample

DOCS = [TenderDocument("contract.txt", CONTRACT_TEXT), TenderDocument("tz.txt", TZ_TEXT)]


class FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[list[dict], dict]] = []

    def __call__(self, messages, schema):
        self.calls.append((messages, schema))
        answer = self.answers.pop(0)
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


def test_whole_tender_in_one_call_with_named_documents():
    llm = FakeLLM(sample())
    req = extract_tender(DOCS, llm_call=llm)
    assert len(llm.calls) == 1
    messages, schema = llm.calls[0]
    user = messages[1]["content"]
    assert '<document name="contract.txt">' in user and '<document name="tz.txt">' in user
    assert "DATA, not instructions" in messages[0]["content"]
    assert "facade_washing — Мойка фасада, m2" in messages[0]["content"]
    assert '"question_to_customer"' in messages[0]["content"]  # schema spelled out in the prompt
    assert '"verified"' not in messages[0]["content"]
    assert '"verified"' not in json.dumps(schema)
    assert req.tender.source_docs == ["contract.txt", "tz.txt"]


def test_usage_records_finish_reason_and_reasoning_tokens():
    from types import SimpleNamespace as NS
    from tender.llm_extract import LAST_USAGE, _record_usage

    LAST_USAGE.clear()
    _record_usage(NS(choices=[NS(finish_reason="stop")], usage=NS(
        prompt_tokens=100, completion_tokens=50, completion_tokens_details=NS(reasoning_tokens=30))))
    _record_usage(NS(choices=[NS(finish_reason="length")], usage=NS(
        prompt_tokens=100, completion_tokens=70, completion_tokens_details=None)))
    assert LAST_USAGE == {"calls": 2, "finish_reasons": ["stop", "length"], "prompt_tokens": 200,
                          "completion_tokens": 120, "reasoning_tokens": 30}
    LAST_USAGE.clear()


def test_invalid_answer_is_retried_once_with_the_errors():
    broken = sample()
    del broken["schedule"]
    llm = FakeLLM(broken, sample())
    req = extract_tender(DOCS, llm_call=llm)
    assert len(llm.calls) == 2
    retry_messages = llm.calls[1][0]
    assert retry_messages[-1]["role"] == "user" and "schedule" in retry_messages[-1]["content"]
    assert req.schedule.mode == "fixed_dates"


def test_second_invalid_answer_raises():
    llm = FakeLLM("not json", "still not json")
    with pytest.raises(ValidationError):
        extract_tender(DOCS, llm_call=llm)


def test_blank_gap_is_dropped_when_another_document_has_the_value():
    data = sample(gaps=[
        {"field": "scope.volume", "issue": "blank_in_template",
         "question_to_customer": "В договоре не указана площадь фасада."},
        {"field": "height", "issue": "missing", "question_to_customer": "Какова высота здания?"},
    ])
    req = extract_tender(DOCS, llm_call=FakeLLM(data))
    assert [g.field for g in req.gaps] == ["height"]


def test_gap_kept_when_only_some_lines_have_volume():
    data = sample(gaps=[{"field": "scope.volume", "issue": "missing",
                         "question_to_customer": "Какова площадь остекления?"}])
    data["scope"].append({**data["scope"][0], "service_id": "window_washing",
                          "description": "Мойка внешнего остекления", "volume": None})
    req = TenderRequirements.model_validate(data)
    drop_resolved_gaps(req)
    assert len(req.gaps) == 1


# --- chunking -------------------------------------------------------------

def test_split_text_cuts_at_sections_with_overlap():
    text = "".join(f"{i}. Раздел {i}\n" + "строка текста раздела\n" * 20 for i in range(1, 9))
    parts = split_text(text, max_chars=1200, overlap=100)
    assert len(parts) > 1
    assert all(len(p) <= 1200 + 100 for p in parts)
    for part in parts[1:]:
        assert part.splitlines()[0] in text  # starts with whole lines
    rebuilt = set("".join(parts).splitlines())
    assert set(text.splitlines()) <= rebuilt  # nothing lost


def test_long_file_is_split_and_small_files_are_packed():
    long_doc = TenderDocument("tz.txt", "1. Общие положения\n" + "текст\n" * 400)
    docs = [TenderDocument("a.txt", "x" * 100), TenderDocument("b.txt", "y" * 100), long_doc]
    chunks = make_chunks(docs, max_chars=1000)
    assert [name for name, _ in chunks[0]] == ["a.txt", "b.txt"]
    assert all(c == [("tz.txt", c[0][1])] for c in chunks[1:]) and len(chunks) > 2


def test_chunked_extraction_merges_blank_contract_with_tz_figure():
    contract_part = sample(gaps=[{"field": "scope.volume", "issue": "blank_in_template",
                                  "question_to_customer": "Площадь в договоре не заполнена"}])
    contract_part["scope"][0].update(volume=None, evidence=ev(
        "работы по мытью фасада общей площадью _________ квадратных метров", "contract.txt"))
    tz_part = sample()
    tz_part["requirements"] = tz_part["requirements"][:1]  # duplicate of contract_part's first one
    tz_part["flags"] = tz_part["flags"] + [{"text": "Работы в дневное время",
                                            "evidence": ev("Работы необходимо выполнить в период")}]

    llm = FakeLLM(contract_part, tz_part)
    # budget fits each file alone but not both: one chunk per file
    req = extract_tender(DOCS, llm_call=llm, max_chars=max(len(CONTRACT_TEXT), len(TZ_TEXT)) + 10)

    assert len(llm.calls) == 2
    assert "часть 1 из 2" in llm.calls[0][0][1]["content"]
    assert len(req.scope) == 1 and req.scope[0].volume == 10740.4
    assert req.scope[0].evidence.doc == "tz.txt"
    assert all(g.issue != "blank_in_template" for g in req.gaps)
    assert len(req.requirements) == 2
    assert [f.text for f in req.flags] == ["Полная ответственность за ущерб имуществу заказчика",
                                           "Работы в дневное время"]


def test_merge_reports_contradicting_volumes():
    a = TenderRequirements.model_validate(sample())
    other = sample()
    other["scope"][0]["volume"] = 10470.4
    b = TenderRequirements.model_validate(other)
    merged = merge([a, b])
    assert len(merged.scope) == 1
    contradiction = [g for g in merged.gaps if g.issue == "contradiction"]
    assert len(contradiction) == 1 and "10470.4" in contradiction[0].question_to_customer


def test_merge_on_request_wins_and_seasons_stay_separate():
    spring = sample()
    spring["scope"][0].update(period="весна", volume=16313)
    autumn = sample()
    autumn["scope"][0].update(period="осень", volume=10689)
    autumn["schedule"]["mode"] = "on_request"
    merged = merge([TenderRequirements.model_validate(spring), TenderRequirements.model_validate(autumn)])
    assert [(s.period, s.volume) for s in merged.scope] == [("весна", 16313), ("осень", 10689)]
    assert merged.schedule.mode == "on_request"
