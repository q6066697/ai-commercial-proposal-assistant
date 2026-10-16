"""
Regression guard: the rule-based extractor's accuracy on the synthetic eval
set shouldn't silently drop below its documented baseline (87.5% exact-match,
see README). This doesn't replace running src/evaluate.py for the full
breakdown -- it's just a tripwire in CI/pytest.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from evaluate import load_cases, run_eval
from extractor import extract_rule_based


def test_rule_based_exact_match_rate_at_least_baseline():
    cases = load_cases()
    report = run_eval(extract_rule_based, cases)
    assert report["exact_match_rate"] >= 0.80, (
        f"Extraction accuracy dropped to {report['exact_match_rate']:.0%}, "
        f"below the 80% baseline. Failing cases: "
        f"{[c['id'] for c in report['per_case'] if not c['exact_match']]}"
    )


def test_service_and_quantity_fields_always_accurate():
    """These two fields are the ones the calculator can't function without --
    hold them to a stricter bar than the overall exact-match rate."""
    cases = load_cases()
    report = run_eval(extract_rule_based, cases)
    assert report["field_accuracy"]["service"] == 1.0
    assert report["field_accuracy"]["quantity"] == 1.0


def test_rule_based_never_crashes_on_hard_set():
    """Messy input may be extracted badly, but must never crash the pipeline
    (regression for hard_007: '1,5 тыс. м2' used to raise ValueError)."""
    report = run_eval(extract_rule_based, load_cases("hard"))
    assert report["errors"] == 0


def test_location_normalization():
    from evaluate import normalize_location
    assert normalize_location("г. Минск") == normalize_location("Минск")
    assert normalize_location("город Могилёв") == normalize_location("Могилев")
    assert normalize_location(None) is None
