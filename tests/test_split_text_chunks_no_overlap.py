"""split_text_into_chunks: чанки должны покрывать текст ровно один раз, без перехлёста.

Регрессия аудита utils-text/bugs/1: при маленьком окне поиска граница i-1 могла
обогнать идеальную точку разреза i, split_pos откатывался назад, и следующий чанк
повторно захватывал уже отданный кусок — в собранной главе появлялся задвоенный текст.
"""
from gemini_translator.utils.text import split_text_into_chunks


def _book(paragraphs: int) -> str:
    return "".join(
        f"<p>Он открыл дверь и шагнул в тёмный коридор номер {i}. Он прислушался и пошёл дальше.</p>"
        for i in range(paragraphs)
    )


def _assert_chunks_tile_text(text: str, chunks: list[str]) -> None:
    cursor = 0
    for index, chunk in enumerate(chunks):
        position = text.find(chunk, cursor)
        assert position == cursor, (
            f"чанк {index} начинается не там, где закончился предыдущий "
            f"(ожидалось {cursor}, найдено {position})"
        )
        cursor += len(chunk)
    assert cursor == len(text)
    assert "".join(chunks) == text


def test_small_window_chunks_tile_the_text_exactly():
    text = _book(60)
    chunks = split_text_into_chunks(text, 500, 500, 500)
    assert len(chunks) > 1
    _assert_chunks_tile_text(text, chunks)


def test_two_chunk_split_does_not_repeat_the_boundary_paragraph():
    text = _book(12)
    assert 900 < len(text) < 1300
    chunks = split_text_into_chunks(text, 500, 500, 500)
    _assert_chunks_tile_text(text, chunks)


def test_many_targets_never_duplicate_content():
    text = _book(40)
    for target in (500, 600, 700, 900, 1300):
        chunks = split_text_into_chunks(text, target, 500, 500)
        _assert_chunks_tile_text(text, chunks)
