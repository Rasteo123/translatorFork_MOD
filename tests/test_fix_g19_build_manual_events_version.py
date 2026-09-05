"""Регресс на qa-a/bugs/3-manual-qa-picks-wrong-translat.

build_manual_events должен выбирать ту же версию перевода, что и канонический
селектор select_target_translation_version (тот же, что использует сборка
EPUB), а не первую по алфавиту среди суффиксов файлов. Иначе ручной/книжный
проход QA правит файл, который никогда не попадёт в книгу, а реально
используемая проверенная версия остаётся непроверенной.
"""

from __future__ import annotations

from pathlib import Path

from gemini_translator.qa.assembly import build_manual_events


class _ProjectManager:
    def __init__(self, folder: Path, data: dict) -> None:
        self.project_folder = str(folder)
        self.data = data


def test_build_manual_events_prefers_validated_over_alphabetically_earlier_suffix(
    tmp_path: Path,
) -> None:
    # '_translated.html' лексикографически меньше '_validated.html', поэтому
    # наивная sorted(versions.items()) выбирает непроверенную версию первой.
    (tmp_path / "ch1_translated.html").write_text("unverified", encoding="utf-8")
    (tmp_path / "ch1_validated.html").write_text("verified", encoding="utf-8")

    project = _ProjectManager(
        tmp_path,
        {
            "OEBPS/ch1.xhtml": {
                "_translated.html": "ch1_translated.html",
                "_validated.html": "ch1_validated.html",
            }
        },
    )

    events = build_manual_events(
        project_manager=project,
        epub_path=str(tmp_path / "book.epub"),
    )

    assert len(events) == 1
    chosen = Path(events[0].translated_path)
    assert chosen.name == "ch1_validated.html", (
        "build_manual_events должен предпочесть '_validated.html' версию, "
        "как это делает select_target_translation_version, а не первую по "
        "алфавиту"
    )
