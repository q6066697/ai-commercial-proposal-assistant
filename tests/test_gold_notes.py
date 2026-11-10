"""
Gold notes (.md) -> gold JSON converter, on the synthetic tender from
tender_fixtures (the real documents are git-ignored).
"""
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tender.gold_notes import PROVENANCE, NotesError, convert
from tender.loader import TenderDocument
from tender_fixtures import CONTRACT_TEXT, GOLD_NOTES as NOTES, TZ_TEXT

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schemas" / "tender_requirements.schema.json").read_text(encoding="utf-8"))
DOCS = [TenderDocument("contract.txt", CONTRACT_TEXT), TenderDocument("tz.txt", TZ_TEXT)]



def test_all_sections_are_converted():
    result = convert(NOTES, DOCS, "demo")
    gold = result.gold
    assert result.status == "done" and result.issues == []
    assert gold["_meta"]["provenance"] == PROVENANCE

    facade, windows = gold["scope"]
    assert (facade["service_id"], facade["volume"], facade["unit"], facade["repeats"]) == ("facade_washing", 10740.4, "m2", 1)
    assert (windows["service_id"], windows["volume"], windows["unit"]) == ("window_washing", None, "m2")

    reqs = gold["requirements"]
    assert [(r["category"], r["mandatory"], r["strength"]) for r in reqs] == [
        ("personnel_admission", True, "обязательно"),
        ("safety", False, "условно"),
        ("other", False, "желательно"),
    ]
    assert reqs[0]["section"] == "Требования к персоналу и допускам" and reqs[0]["notes_line"] == 9

    assert gold["flags"][0]["evidence"]["doc"] == "contract.txt"
    tender = gold["tender"]
    assert tender["customer"] == {"value": "КБ Демо Кредит ООО", "evidence": {
        "quote": "КБ «Демо Кредит» ООО", "doc": "contract.txt", "verified": True, "match": "verified"}}
    assert tender["country"] == "RU" and gold["schedule"]["mode"] == "fixed_dates"
    assert gold["height"] == {"max_height_m": None, "floors": None}
    assert tender["source_docs"] == ["contract.txt", "tz.txt"]


def test_gold_validates_against_the_schema():
    gold = convert(NOTES, DOCS, "demo").gold
    errors = list(Draft202012Validator(SCHEMA).iter_errors({k: v for k, v in gold.items() if k != "_meta"}))
    assert errors == []


def test_gaps_issue_and_informational_marking():
    gaps = convert(NOTES, DOCS, "demo").gold["gaps"]
    assert [(g["issue"], g["scored"]) for g in gaps] == [
        ("missing", True),
        ("blank_in_template", False),   # value is in the ТЗ: the pipeline drops such gaps
        ("blank_in_template", True),    # nowhere else: a real gap
        ("ambiguous", True),
    ]
    assert "info_reason" in gaps[1]


def test_quote_not_found_is_listed_and_left_unchanged():
    notes = NOTES.replace("«должны иметь удостоверения промышленного альпиниста»",
                          "«должны иметь удостоверения альпиниста категории 2»")
    result = convert(notes, DOCS, "demo")
    assert result.status == "draft"
    assert result.issues == ["строка 9: not_found «должны иметь удостоверения альпиниста категории 2» [tz.txt]"]
    assert result.gold["requirements"][0]["evidence"]["quote"] == "должны иметь удостоверения альпиниста категории 2"
    assert result.gold["requirements"][0]["evidence"]["verified"] is False


def test_quote_from_the_other_file_is_reported_not_corrected():
    notes = NOTES.replace("«несет полную имущественную ответственность» | contract",
                          "«несет полную имущественную ответственность» | tz")
    result = convert(notes, DOCS, "demo")
    assert result.status == "draft"
    assert any("wrong_doc" in i and "строка 15" in i for i in result.issues)
    assert result.gold["requirements"][2]["evidence"]["doc"] == "tz.txt"


@pytest.mark.parametrize("bad, message", [
    ("- без разделителей вообще", "формат не распознан"),
    ("- нет силы | «обеспечить ограждение опасной зоны» | tz", "не указано обязательно"),
    ("- чужой файл | обязательно | «обеспечить ограждение опасной зоны» | notice", "файл «notice» не найден"),
])
def test_unparseable_lines_are_errors_with_line_numbers(bad, message):
    notes = NOTES.replace("## Безопасность и охрана труда\n", f"## Безопасность и охрана труда\n{bad}\n")
    with pytest.raises(NotesError) as e:
        convert(notes, DOCS, "demo")
    assert any(message in err and err.startswith("строка 12") for err in e.value.errors)


def test_scope_line_without_volume_is_an_error():
    notes = NOTES.replace("мойка фасада, 10740,4 м², 1 раз", "мойка фасада целиком")
    with pytest.raises(NotesError, match="нет объёма"):
        convert(notes, DOCS, "demo")


def test_missing_passport_fields_are_errors():
    notes = NOTES.replace("- режим: fixed_dates\n", "")
    with pytest.raises(NotesError, match="нет поля «режим»"):
        convert(notes, DOCS, "demo")
