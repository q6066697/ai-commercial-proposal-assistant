"""
The Pydantic model must agree with schemas/tender_requirements.schema.json,
and the schema handed to the LLM must not contain code-only fields.
"""
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tender.models import TenderRequirements, llm_response_schema
from tender_fixtures import sample

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schemas" / "tender_requirements.schema.json").read_text(encoding="utf-8"))
INDEX = json.loads((ROOT / "data" / "tenders" / "index.json").read_text(encoding="utf-8"))
validator = Draft202012Validator(SCHEMA)


def schema_errors(data: dict) -> list[str]:
    return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in validator.iter_errors(data)]


def test_model_dump_validates_against_json_schema():
    req = TenderRequirements.model_validate(sample())
    req.tender.source_docs = ["contract.txt", "tz.txt"]
    assert schema_errors(req.to_json_dict()) == []


def test_code_filled_fields_are_valid_in_the_schema():
    req = TenderRequirements.model_validate(sample())
    req.scope[0].evidence.verified = True
    req.scope[0].evidence.match = "verified"
    dumped = req.to_json_dict()
    assert dumped["scope"][0]["evidence"]["match"] == "verified"
    assert schema_errors(dumped) == []


def test_country_wrapped_as_a_fact_is_accepted():
    data = sample()
    data["tender"]["country"] = {"value": "RU", "evidence": {"quote": "в Российской Федерации", "doc": "tz.txt"}}
    assert TenderRequirements.model_validate(data).tender.country == "RU"


def test_unset_optional_objects_are_omitted_not_null():
    dumped = TenderRequirements.model_validate(sample()).to_json_dict()
    assert "acceptance" not in dumped
    assert "verified" not in dumped["scope"][0]["evidence"]


def test_llm_schema_has_no_code_only_fields_and_is_strict():
    schema = llm_response_schema()
    text = json.dumps(schema)
    for name in ("verified", "match", "source_docs", "code_only"):
        assert f'"{name}"' not in text

    def check(node):
        if isinstance(node, dict):
            if isinstance(node.get("properties"), dict):
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            assert "default" not in node
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)

    check(schema)


@pytest.mark.parametrize("tender", INDEX["tenders"], ids=lambda t: t["id"])
def test_gold_file_exists_for_every_tender(tender):
    gold = json.loads((ROOT / "data" / "tenders" / "gold" / f"{tender['id']}.json").read_text(encoding="utf-8"))
    assert gold["_meta"]["tender_id"] == tender["id"]
    assert gold["_meta"]["status"] in {"template", "done"}
    assert gold["tender"]["source_docs"] == [d["file"] for d in tender["documents"]]


@pytest.mark.parametrize("tender", INDEX["tenders"], ids=lambda t: t["id"])
def test_finished_gold_matches_schema(tender):
    gold = json.loads((ROOT / "data" / "tenders" / "gold" / f"{tender['id']}.json").read_text(encoding="utf-8"))
    if gold["_meta"]["status"] != "done":
        pytest.skip("gold markup not filled in yet")
    assert schema_errors({k: v for k, v in gold.items() if k != "_meta"}) == []
    req = TenderRequirements.model_validate({k: v for k, v in gold.items() if k != "_meta"})

    # The documents are git-ignored: the quote check runs only where they exist.
    raw = ROOT / "data" / "tenders" / "raw" / tender["id"]
    if raw.is_dir():
        from tender.grounding import ground
        from tender.loader import load_tender

        report = ground(req, load_tender(raw))
        assert [(i.path, i.status, i.quote) for i in report.issues] == []
