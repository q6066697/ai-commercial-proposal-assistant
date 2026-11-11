"""
Matching and metrics of the tender eval, on the synthetic tender: gold from
GOLD_NOTES, model answers built from tender_fixtures.sample(). No API calls.

Gold (GOLD_NOTES): 3 requirements (удостоверения / ограждение from the ТЗ,
полная ответственность from the contract), 2 scope lines (facade 10740.4,
windows with no volume), 1 flag, 4 gaps of which 3 are scored.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tender.eval import (Item, Locator, Span, closeness, closeness_label, coverage, evaluate_run, match,
                         spans_overlap, stems)
from tender.eval_report import ModelRuns, closeness_table, render, section_recall_table
from tender.gold_notes import convert
from tender.loader import TenderDocument
from tender.models import Evidence, TenderRequirements
from tender_fixtures import CONTRACT_TEXT, GOLD_NOTES, TZ_TEXT, ev, sample

DOCS = [TenderDocument("contract.txt", CONTRACT_TEXT), TenderDocument("tz.txt", TZ_TEXT)]
GOLD = convert(GOLD_NOTES, DOCS, "demo").gold


def model(**overrides) -> TenderRequirements:
    return TenderRequirements.model_validate(sample(**overrides))


def item(text, span=None, quote="x", doc="tz.txt", verified=True):
    return Item("requirement", text, Evidence(quote=quote, doc=doc), span, "ref", verified=verified)


# --- building blocks ---------------------------------------------------------

def test_locator_finds_quote_across_nbsp_and_line_break():
    loc = Locator(DOCS)
    span = loc.span(Evidence(quote="ограждение опасной зоны под местом", doc="tz.txt"))
    assert span is not None and span.doc == "tz.txt"
    assert loc.span(Evidence(quote="10740,4 кв.м.", doc="tz.txt")) is not None
    assert loc.span(Evidence(quote="такого текста нет", doc="tz.txt")) is None


def test_span_overlap_rules():
    sentence = Span("tz.txt", 100, 200)
    assert spans_overlap(Span("tz.txt", 120, 150), sentence)          # gold quote inside model sentence
    assert spans_overlap(Span("tz.txt", 180, 220), sentence)          # 20 of 40 chars = 50%
    assert not spans_overlap(Span("tz.txt", 190, 230), sentence)      # 10 of 40
    assert not spans_overlap(Span("contract.txt", 120, 150), sentence)
    assert not spans_overlap(None, sentence)


def test_stems_ignore_case_endings_and_stop_words():
    assert stems("Удостоверения промышленного альпиниста") == stems("удостоверение промышленных альпинистов")
    assert "для" not in stems("для работ на высоте")
    assert coverage("ограждение опасной зоны", "Обеспечить ограждение опасной зоны")[0] == 1.0


def test_match_prefers_quote_then_text():
    gold = item("ограждение опасной зоны", Span("tz.txt", 10, 40))
    assert match(gold, item("что угодно", Span("tz.txt", 0, 100))) == "цитата"
    # same requirement quoted from the other file: no span overlap, text decides
    assert match(gold, item("Ограждение опасной зоны перед работами", Span("contract.txt", 0, 100))) == "текст"
    assert match(gold, item("Уборка мусора", Span("contract.txt", 0, 100))) is None


def test_one_word_gold_line_matches_by_text():
    # gold "конфиденциальность" has a single stem: one shared stem is enough
    gold = item("конфиденциальность", Span("contract.txt", 10, 40))
    model_item = item("Соблюдение конфиденциальности информации", Span("contract.txt", 500, 600))
    assert match(gold, model_item) == "текст"
    assert match(gold, model_item, short_gold_fix=False) is None   # the original rule, kept for the report
    assert match(gold, item("Уборка мусора", Span("contract.txt", 500, 600))) is None


def test_multi_word_gold_line_still_needs_two_shared_stems():
    gold = item("ограждение опасной зоны", Span("tz.txt", 10, 40))
    assert match(gold, item("Ограждение территории", Span("contract.txt", 0, 50))) is None


def test_one_model_sentence_can_cover_two_gold_items():
    gold_hours = item("работы с 8 до 17", Span("tz.txt", 100, 120))
    gold_weekend = item("выходные с сотрудником банка", Span("tz.txt", 130, 160))
    paragraph = item("Режим работ", Span("tz.txt", 90, 170))
    assert match(gold_hours, paragraph) == match(gold_weekend, paragraph) == "цитата"


# --- metrics -------------------------------------------------------------------

def test_metrics_on_the_sample_answer():
    r = evaluate_run(model(), GOLD, DOCS).metrics
    # "полная ответственность" is in the model's commercial.evidence, not requirements[]
    assert (r["recall"]["num"], r["recall"]["den"]) == (3, 3)
    assert (r["recall_strict"]["num"], r["recall_strict"]["den"]) == (2, 3)
    assert r["precision"]["value"] == 1.0
    assert (r["volumes"]["num"], r["volumes"]["den"]) == (1, 2)   # windows line missing
    assert r["groundedness"]["value"] == 1.0
    assert r["flags"]["value"] == 1.0
    assert (r["gaps"]["num"], r["gaps"]["den"]) == (1, 3)         # only "высота" is asked by the model


def test_extra_and_unverified_requirements():
    data = sample()
    data["requirements"].append({"text": "Лицензия МЧС", "category": "license", "mandatory": True,
                                 "evidence": ev("Исполнитель должен иметь лицензию МЧС")})
    data["requirements"][1]["evidence"] = ev("обязан оградить зону работ сеткой высотой 2 м")  # invented quote
    result = evaluate_run(TenderRequirements.model_validate(data), GOLD, DOCS)
    m = result.metrics
    assert m["recall"]["num"] == 2                    # the fencing requirement only has an unconfirmed quote
    assert m["precision"]["num"] == 2 and m["precision"]["den"] == 3
    assert [x.text for x in result.extra_requirements] == ["Лицензия МЧС"]
    assert m["groundedness"]["num"] == m["groundedness"]["den"] - 2
    assert len(result.grounding_issues) == 2


def test_volume_null_in_gold_and_model_is_correct():
    data = sample()
    data["scope"].append({**data["scope"][0], "service_id": "window_washing", "description": "Мойка окон",
                          "volume": None, "evidence": ev("Услуги по мытью окон с внешней стороны")})
    volumes = evaluate_run(TenderRequirements.model_validate(data), GOLD, DOCS).volumes
    assert [(v["found"], v["volume_ok"], v["repeats_ok"]) for v in volumes] == [(True, True, True)] * 2


def test_wrong_volume_and_repeats_are_caught():
    data = sample()
    data["scope"][0].update(volume=17040.4, repeats=2)
    v = evaluate_run(TenderRequirements.model_validate(data), GOLD, DOCS).volumes[0]
    assert v["found"] and not v["volume_ok"] and not v["repeats_ok"]


def test_informational_gap_is_not_scored():
    gaps = evaluate_run(model(), GOLD, DOCS).gaps
    assert [g["scored"] for g in gaps] == [True, False, True, True]


def test_closeness_separates_near_quotes_from_invented_ones():
    texts = Locator(DOCS).texts
    typo = "Работники Исполнителя должны иметь удостоверения промышленого альпиниста"   # one letter missing
    near, doc, nearest = closeness(typo, texts)
    assert near >= 0.9 and closeness_label(near) == "почти дословно" and doc == "tz.txt"
    assert "удостоверения промышленного альпинист" in nearest  # window = quote length (one letter shorter)
    invented, _, _ = closeness("Подрядчик обязан застраховать ответственность на 10 млн рублей", texts)
    assert invented < 0.6 and closeness_label(invented) == "в документе нет похожего текста"


def test_unverified_quotes_get_closeness_diagnostics_but_metric_stays_strict():
    data = sample()
    data["requirements"][0]["evidence"] = ev("Работники Исполнителя должны иметь удостоверения промышленого альпиниста")
    result = evaluate_run(TenderRequirements.model_validate(data), GOLD, DOCS)
    [u] = result.unverified
    assert u["path"] == "requirements[0]" and u["status"] == "not_found" and u["similarity"] >= 0.9
    assert result.metrics["groundedness"]["num"] == result.metrics["groundedness"]["den"] - 1


def test_section_recall_and_closeness_tables():
    # model-a: the sample answer finds all 3 gold lines (one per section), 2 runs.
    # model-b: its only quote for "удостоверения" is a typo -> unconfirmed -> that line is not found.
    data = sample()
    data["requirements"][0]["evidence"] = ev("Работники Исполнителя должны иметь удостоверения промышленого альпиниста")
    models = [
        ModelRuns("model-a", [evaluate_run(model(), GOLD, DOCS)] * 2, [{}, {}]),
        ModelRuns("model-b", [evaluate_run(TenderRequirements.model_validate(data), GOLD, DOCS)], [{}]),
    ]
    sections = section_recall_table(models)
    assert sections[0] == "| Раздел gold | Строк | model-a | model-b | Все прогоны (3) |"
    assert "| Требования к персоналу и допускам | 1 | 100% (2/2) | 0% (0/1) | 67% (2/3) |" in sections
    assert "| Ответственность и штрафы | 1 | 100% (2/2) | 100% (1/1) | 100% (3/3) |" in sections

    closeness_rows = closeness_table(models)
    assert "| model-a | 0 | — | — | — | — |" in closeness_rows
    b_row = next(r for r in closeness_rows if r.startswith("| model-b |"))
    assert b_row.startswith("| model-b | 1 |") and b_row.endswith("| 1 | 0 | 0 |")
    assert closeness_rows[-1].startswith("| Все прогоны (3) | 1 |")


def test_report_compares_models_and_shows_time_tokens_and_diagnostics():
    data = sample()
    data["requirements"][0]["evidence"] = ev("Работники Исполнителя должны иметь удостоверения промышленого альпиниста")
    meta = {"model": "a", "started": "2026-11-10T20:00:00", "seconds": 300.0,
            "usage": {"prompt_tokens": 11000, "completion_tokens": 3000, "calls": 1}}
    b_run = TenderRequirements.model_validate(data)
    models = [
        ModelRuns("model-a", [evaluate_run(model(), GOLD, DOCS)] * 2, [meta, {**meta, "seconds": 100.0}],
                  runs_before_fix=[evaluate_run(model(), GOLD, DOCS, short_gold_fix=False)] * 2),
        ModelRuns("model-b", [evaluate_run(b_run, GOLD, DOCS)], [meta],
                  failed=[{"run": "2", "error": "APIStatusError: 429"}],
                  runs_before_fix=[evaluate_run(b_run, GOLD, DOCS, short_gold_fix=False)]),
    ]
    text = render("demo", {"dates": "2026-11-10", "commit": "abc123", "gold_note": "Gold v1.1: +2"}, GOLD, models)
    assert "- Gold v1.1: +2" in text
    assert "## Изменение методики" in text and "исправлено после первого прогона" in text
    assert "| model-a: до → после | model-b: до → после |" in text
    assert "- Модели: model-a, model-b" in text and "- Коммит: abc123" in text
    assert "Gold: 3 требований, 2 строк объёмов, 1 флагов, 4 gaps (в метрике 3)" in text
    assert "Gold: черновик подготовлен с помощью ИИ, каждая строка вручную сверена с документами" in text
    assert "| **Recall требований** (все факты модели с цитатой) | 100% (100%–100%) | 67% |" in text
    assert "| Время на прогон, с | 200 (100–300) | 300 |" in text
    assert "| Неудачных прогонов | 0 | 1 |" in text
    assert "## Модель: model-a" in text and "### Пропущено моделью" in text and "### Лишнее у модели" in text
    assert "## Recall по разделам gold" in text and "## Близость неподтверждённых цитат к тексту" in text
    assert "почти дословно" in text
