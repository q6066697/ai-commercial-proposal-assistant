"""
Markdown / console report for tender eval runs (see tender/eval.py), one or
several models. Lists are there for a manual check of disputed cases: what
the model missed, what it added that the gold does not have, which matches
were made by text only, and how close each unconfirmed quote came to the
document text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from tender.eval import RunEval

METRIC_ROWS = [
    ("recall", "**Recall требований** (все факты модели с цитатой)"),
    ("recall_strict", "Recall строгий (только requirements[])"),
    ("precision", "Precision requirements[]"),
    ("volumes", "Объёмы (услуга + площадь + повторы)"),
    ("groundedness", "Groundedness (подтверждённые цитаты)"),
    ("groundedness_flags", "Groundedness флагов"),
    ("flags", "Флаги из gold найдены"),
    ("gaps", "Gaps из gold найдены (приблизительно)"),
]


@dataclass
class ModelRuns:
    model: str
    runs: list[RunEval]
    metas: list[dict]                                   # run_meta.json per run: seconds, usage, started
    failed: list[dict] = field(default_factory=list)    # runs that produced no answer
    runs_before_fix: list[RunEval] = field(default_factory=list)  # same runs, original matching rule


def _cell(r: dict) -> str:
    if r["value"] is None:
        return "—"
    return f"{r['value']:.0%} ({r['num']}/{r['den']})"


def _summary(values: list) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "—"
    if len(vals) == 1:
        return f"{vals[0]:.0%}"
    return f"{mean(vals):.0%} ({min(vals):.0%}–{max(vals):.0%})"


def _num_summary(values: list, fmt: str = "{:.0f}") -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "—"
    if len(vals) == 1:
        return fmt.format(vals[0])
    return f"{fmt.format(mean(vals))} ({fmt.format(min(vals))}–{fmt.format(max(vals))})"


def metrics_table(runs: list[RunEval]) -> list[str]:
    head = "| Метрика | " + " | ".join(f"Прогон {i}" for i in range(1, len(runs) + 1)) + " | Среднее (мин–макс) |"
    lines = [head, "|" + "---|" * (len(runs) + 2)]
    for key, label in METRIC_ROWS:
        cells = [_cell(r.metrics[key]) for r in runs]
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {_summary([r.metrics[key]['value'] for r in runs])} |")
    for key, label in (("model_requirements", "Требований у модели"), ("matched_by_text", "Из них совпало только по тексту")):
        cells = [str(r.metrics[key]) for r in runs]
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {mean(r.metrics[key] for r in runs):.1f} |")
    return lines


def comparison_table(models: list[ModelRuns]) -> list[str]:
    lines = ["| Метрика | " + " | ".join(f"{m.model} ({len(m.runs)} прог.)" for m in models) + " |",
             "|---|" + "---|" * len(models)]
    for key, label in METRIC_ROWS:
        lines.append(f"| {label} | " + " | ".join(
            _summary([r.metrics[key]["value"] for r in m.runs]) for m in models) + " |")
    lines.append("| Требований у модели | " + " | ".join(
        _num_summary([r.metrics["model_requirements"] for r in m.runs]) for m in models) + " |")
    lines.append("| Время на прогон, с | " + " | ".join(
        _num_summary([meta.get("seconds") for meta in m.metas]) for m in models) + " |")
    lines.append("| Токены вход / выход на прогон | " + " | ".join(
        f"{_num_summary([meta.get('usage', {}).get('prompt_tokens') for meta in m.metas])} / "
        f"{_num_summary([meta.get('usage', {}).get('completion_tokens') for meta in m.metas])}" for m in models) + " |")
    lines.append("| Неудачных прогонов | " + " | ".join(str(len(m.failed)) for m in models) + " |")
    return lines


RULE_FIX_ROWS = ["recall", "recall_strict", "precision", "flags"]


def rule_fix_table(models: list[ModelRuns]) -> list[str]:
    """Same saved runs, same gold: original rule vs one-stem gold lines fixed."""
    lines = ["| Метрика | " + " | ".join(f"{m.model}: до → после" for m in models) + " |",
             "|---|" + "---|" * len(models)]
    labels = dict(METRIC_ROWS)
    for key in RULE_FIX_ROWS:
        cells = []
        for m in models:
            before = _summary([r.metrics[key]["value"] for r in m.runs_before_fix])
            after = _summary([r.metrics[key]["value"] for r in m.runs])
            cells.append(f"{before} → {after}")
        lines.append(f"| {labels[key]} | " + " | ".join(cells) + " |")
    return lines


def _q(text: str, limit: int = 140) -> str:
    text = " ".join((text or "").split()).replace("|", "/")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _model_section(m: ModelRuns, gold: dict) -> list[str]:
    runs, n = m.runs, len(m.runs)
    out = [f"## Модель: {m.model}", "", *metrics_table(runs), ""]

    out += ["### Время и токены", "", "| Прогон | Начало | Время, с | Вход | Выход | Вызовов |", "|---|---|---|---|---|---|"]
    for i, meta in enumerate(m.metas, 1):
        usage = meta.get("usage", {})
        out.append(f"| {i} | {meta.get('started', '?')} | {meta.get('seconds', '?')} | {usage.get('prompt_tokens', '?')} | "
                   f"{usage.get('completion_tokens', '?')} | {usage.get('calls', '?')} |")
    for f in m.failed:
        out.append(f"| ✗ {f.get('run')} | {f.get('started', '?')} | {f.get('seconds', '?')} | — | — | "
                   f"{_q(f.get('error', ''), 120)} |")
    out.append("")

    out += ["### Пропущено моделью", "",
            "Требования из gold, не найденные хотя бы в одном прогоне (основной recall).", "",
            "| Строка | Раздел | Что | Цитата | Найдено |", "|---|---|---|---|---|"]
    rows = []
    for idx, found in enumerate(runs[0].requirements):
        hits = sum(1 for r in runs if r.requirements[idx].how)
        if hits < n:
            g = found.gold
            rows.append((hits, f"| {g.ref} | {g.extra.get('section', '')} | {_q(g.text, 80)} | «{_q(g.evidence.quote, 90)}» "
                               f"({g.evidence.doc}) | {hits}/{n} |"))
    out += [row for _, row in sorted(rows, key=lambda r: r[0])] or ["| — | | | | |"]
    out.append("")

    out += ["### Лишнее у модели", "",
            "Требования модели, не совпавшие ни с одной строкой gold. Не обязательно ошибка — "
            "возможно, пункт стоит добавить в gold.", ""]
    for i, r in enumerate(runs, 1):
        out.append(f"**Прогон {i}** ({len(r.extra_requirements)})")
        out.append("")
        for item in r.extra_requirements:
            status = "✓" if item.verified else f"⚠ {item.evidence.match}"
            out.append(f"- {_q(item.text, 100)} — «{_q(item.evidence.quote, 110)}» ({item.evidence.doc}) {status}")
        out.append("")

    text_pairs = [(i, f) for i, r in enumerate(runs, 1) for f in r.requirements if f.how == "текст"]
    if text_pairs:
        out += ["### Совпадения только по тексту (проверить глазами)", ""]
        for i, f in text_pairs:
            out.append(f"- П{i}, gold {f.gold.ref}: {_q(f.gold.text, 80)} ↔ модель {f.model.ref}: {_q(f.model.text, 100)}")
        out.append("")

    out += ["### Объёмы", "", "| Прогон | Строка gold | Услуга | Gold | Модель | Площадь | Повторы |",
            "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(runs, 1):
        for v in r.volumes:
            model = f"{v['model_volume']} ×{v['model_repeats']}" if v["found"] else "нет строки"
            out.append(f"| {i} | {v['line']} | {v['service_id']} | {v['gold_volume']} ×{v['gold_repeats']} | {model} | "
                       f"{'✓' if v['volume_ok'] else '✗'} | {'✓' if v['repeats_ok'] else '✗'} |")
    for i, r in enumerate(runs, 1):
        if r.extra_scope:
            out += ["", f"Лишние строки объёмов, прогон {i}: " + "; ".join(r.extra_scope)]
    out.append("")

    out += ["### Флаги", "", "| Строка | Флаг gold | " + " | ".join(f"П{i}" for i in range(1, n + 1)) + " |",
            "|---|---|" + "---|" * n]
    for idx, found in enumerate(runs[0].flags):
        out.append(f"| {found.gold.ref} | {_q(found.gold.text, 90)} | " + " | ".join(r.flags[idx].how or "✗" for r in runs) + " |")
    for i, r in enumerate(runs, 1):
        if r.extra_flags:
            out += ["", f"Флаги модели вне gold, прогон {i}: " + "; ".join(
                f"{_q(f.text, 70)} ({'✓' if f.verified else '⚠ ' + str(f.evidence.match)})" for f in r.extra_flags)]
    out.append("")

    out += ["### Gaps", "", "| Строка | Gap gold | В метрике | " + " | ".join(f"П{i}" for i in range(1, n + 1))
            + " | Ближайший вопрос модели (П1) |", "|---|---|---|" + "---|" * n + "---|"]
    for idx, gap in enumerate(runs[0].gaps):
        cells = [("✓" if r.gaps[idx]["found"] else "✗") + f" {r.gaps[idx]['score']:.2f}" for r in runs]
        scored = "да" if gap["scored"] else "нет (информационный)"
        out.append(f"| {gap['line']} | {_q(gap['text'], 80)} | {scored} | " + " | ".join(cells)
                   + f" | {_q(gap['model'] or '—', 90)} |")
    for i, r in enumerate(runs, 1):
        if r.extra_gaps:
            out += ["", f"Gaps модели вне gold, прогон {i}: " + "; ".join(_q(g, 90) for g in r.extra_gaps)]
    out.append("")

    out += ["### Неподтверждённые цитаты: насколько близко к тексту", "",
            "Диагностика, в метрики не входит: groundedness остаётся строгим (точное совпадение после "
            "нормализации). Близость — лучшее посимвольное совпадение с фрагментом документа той же длины: "
            "≥90% — «почти дословно» (опечатки, замена слова), 60–90% — искажение или пересказ, "
            "<60% — похожего текста в документах нет.", ""]
    total = sum(len(r.unverified) for r in runs)
    if not total:
        out += ["Неподтверждённых цитат нет.", ""]
    else:
        out += ["| Прогон | Поле | Статус | Близость | Цитата модели | Ближайший текст документа |", "|---|---|---|---|---|---|"]
        for i, r in enumerate(runs, 1):
            for u in sorted(r.unverified, key=lambda u: -u["similarity"]):
                out.append(f"| {i} | {u['path']} | {u['status']} | {u['similarity']:.0%} — {u['label']} | "
                           f"«{_q(u['quote'], 110)}» | «{_q(u['nearest'], 110)}» ({u['nearest_doc'] or '—'}) |")
        out.append("")
    return out


def render(tender_id: str, header: dict, gold: dict, models: list[ModelRuns]) -> str:
    scored_gaps = sum(1 for g in gold["gaps"] if g["scored"])
    out = [
        f"# Eval извлечения: {tender_id}",
        "",
        f"- Модели: {', '.join(m.model for m in models)}",
        f"- Прогоны: {header['dates']}",
        f"- Коммит: {header['commit']}",
        f"- Gold: {len(gold['requirements'])} требований, {len(gold['scope'])} строк объёмов, "
        f"{len(gold['flags'])} флагов, {len(gold['gaps'])} gaps (в метрике {scored_gaps})",
        f"- {gold['_meta'].get('provenance', '')}",
        *([f"- {header['gold_note']}"] if header.get("gold_note") else []),
        "",
        "## Сравнение моделей",
        "",
        "Среднее по прогонам (мин–макс).",
        "",
        *comparison_table(models),
        "",
        "Сопоставление без LLM: по пересечению цитат в тексте документа (≥50% более короткой), "
        "иначе по тексту (основы слов, покрытие ≥60%). Gaps — только по тексту, оценка приблизительная. "
        "В recall засчитываются только факты модели с подтверждённой цитатой.",
        "",
    ]
    if any(m.runs_before_fix for m in models):
        out += [
            "## Изменение методики",
            "",
            "Правило для однословных строк gold исправлено после первого прогона: строка с одной основой "
            "(«конфиденциальность») требует одной общей основы, а не двух — иначе она не могла совпасть по тексту. "
            "Те же прогоны, тот же gold, среднее (мин–макс):",
            "",
            *rule_fix_table(models),
            "",
        ]
    for m in models:
        out += _model_section(m, gold)
    return "\n".join(out)
