"""
Groundedness check: an evidence quote counts only if it literally occurs in
the named file (modulo whitespace / typography). This is what stops invented
requirements from reaching a proposal.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tender.grounding import Grounder, ground, numbers_in
from tender.loader import TenderDocument
from tender.models import Evidence, TenderRequirements
from tender_fixtures import CONTRACT_TEXT, TZ_TEXT, ev, sample

DOCS = [TenderDocument("tz.txt", TZ_TEXT), TenderDocument("contract.txt", CONTRACT_TEXT)]


def check(quote: str, doc: str = "tz.txt", value=None) -> tuple[str, Evidence]:
    evidence = Evidence(quote=quote, doc=doc)
    return Grounder(DOCS).check(evidence, value), evidence


def test_quote_across_line_break_and_nbsp_is_verified():
    # in the file: "...опасной зоны\nпод местом..." and "10740,4 кв.м."
    assert check("ограждение опасной зоны под местом производства работ")[0] == "verified"
    assert check("составляет 10740,4 кв.м.")[0] == "verified"


def test_typography_and_case_do_not_matter():
    assert check('КБ "Демо Кредит" ООО, именуемый', "contract.txt")[0] == "verified"
    assert check("РАБОТНИКИ ИСПОЛНИТЕЛЯ ДОЛЖНЫ ИМЕТЬ удостоверения")[0] == "verified"


def test_invented_quote_is_not_found():
    status, evidence = check("Исполнитель обязан иметь лицензию МЧС и страховку на 10 млн рублей")
    assert status == "not_found"
    assert evidence.verified is False and evidence.match == "not_found"


def test_paraphrase_is_not_accepted():
    assert check("Работники должны иметь удостоверение промышленного альпиниста")[0] == "not_found"


def test_quote_from_other_file_is_moved_to_that_file():
    status, evidence = check("полную имущественную ответственность", "tz.txt")
    assert status == "wrong_doc"
    assert evidence.doc == "contract.txt" and evidence.verified is True


def test_unknown_doc_name_is_resolved_too():
    status, evidence = check("полную имущественную ответственность", "Договор")
    assert status == "wrong_doc" and evidence.doc == "contract.txt"


def test_too_short_quote_proves_nothing():
    assert check("5")[0] == "too_short"


def test_ellipsis_fragments_must_appear_in_order():
    assert check("Работники Исполнителя … удостоверения промышленного альпиниста")[0] == "verified"
    assert check("удостоверения промышленного альпиниста ... Работники Исполнителя")[0] == "not_found"


def test_number_must_be_in_the_quote():
    assert check("Общая площадь фасада здания составляет 10740,4 кв.м.", value=10740.4)[0] == "verified"
    status, evidence = check("Общая площадь фасада здания составляет 10740,4 кв.м.", value=17040.4)
    assert status == "value_mismatch" and evidence.verified is False


@pytest.mark.parametrize("text, expected", [
    ("составляет 10740, 4 кв. м", 10740.4),
    ("16 313 кв.м.", 16313.0),
    ("в течении 5-ти банковских дней", 5.0),
])
def test_numbers_as_written_in_documents(text, expected):
    assert expected in numbers_in(text)


def test_ground_whole_requirements():
    data = sample()
    data["requirements"].append({
        "text": "Лицензия МЧС", "category": "license", "mandatory": True, "cost_impact": "high",
        "evidence": ev("Исполнитель должен иметь лицензию МЧС России"),
    })
    req = TenderRequirements.model_validate(data)
    report = ground(req, DOCS)
    assert report.total == 11
    assert report.verified == 10
    assert [(i.path, i.status) for i in report.issues] == [("requirements[2]", "not_found")]
    assert req.requirements[2].evidence.verified is False
    assert req.scope[0].evidence.match == "verified"
