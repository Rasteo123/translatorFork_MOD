"""Эквивалентность быстрого извлечения текста (selectolax) и эталона BS4.

`extract_visible_text` заменяет BeautifulSoup(html.parser) +
get_text(separator=' ', strip=True) в детекторе недоперевода. Детектор дальше
делает regex-замены по классам символов и split() по пробелам, поэтому
достаточное условие неизменности его результатов — совпадение извлечённого
текста. Требуем точного совпадения; корпус покрывает сущности, script/style с
CJK внутри, title/meta, комментарии, битую разметку, XHTML-обвязку.
"""

import unittest

from bs4 import BeautifulSoup

from gemini_translator.utils import html_text


def _reference_bs4_extract(html_content: str) -> str:
    """Копия прежней семантики HTMLCleaner.strip_html_tags_preserving_structure."""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        for tag in soup(['script', 'style', 'head', 'title', 'meta']):
            tag.decompose()
        return soup.get_text(separator=' ', strip=True)
    except Exception:
        return html_content


CORPUS = [
    # Обычная глава
    '<html><head><title>Глава 1</title><meta charset="utf-8"/></head>'
    '<body><h1>Глава 1</h1><p>Ли Цинь вошёл в зал.</p><p>Все замолчали.</p></body></html>',
    # XHTML-обвязка (EPUB)
    '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
    '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>'
    '<body><p>Текст главы с <em>курсивом</em> и <strong>жирным</strong>.</p></body></html>',
    # script/style с CJK и латиницей внутри — не должны попасть в текст
    '<body><script>var s = "森罗万象 secret";</script><style>.a { content: "hidden"; }</style>'
    '<p>Видимый текст 森</p></body>',
    # Сущности (частично двойное экранирование)
    '<p>Кавычки &laquo;ёлочки&raquo; &amp; амперсанд &lt;не тег&gt;</p>',
    # Вложенные инлайны, br, разрывы
    '<p>стро<span>ка со <b>вло</b>жен</span>ностью</p><p>вторая<br/>строка</p>',
    # Комментарии и CDATA-подобное
    '<p>до<!-- комментарий 森 -->после</p>',
    # Таблица и списки
    '<table><tr><td>яч1</td><td>яч2</td></tr></table><ul><li>раз</li><li>два</li></ul>',
    # Смешанные скрипты (для detect_mixed_script)
    '<p>покачал 頭 головой</p><p>xiǎo лис 狐狸</p>',
    # Атрибуты не текст
    '<p><img src="a.png" alt="альт не текст"/><a href="#x" title="тайтл">ссылка</a></p>',
    # Пустые и почти пустые
    '',
    '<body></body>',
    '<p>   </p><p>x</p>',
    # Битая разметка
    '<p>незакрытый абзац <div>дивчик</p></div>',
    '<p>сравнение: 3 < 5 и 7 > 2</p>',
    # Только текст без тегов
    'просто текст без разметки 森罗',
]


class HtmlTextExtractionTests(unittest.TestCase):
    def test_backend_is_selectolax(self):
        """Без selectolax модуль тихо падает в BS4 — оптимизация станет no-op."""
        self.assertEqual(html_text.HTML_TEXT_BACKEND, "selectolax")

    def test_matches_bs4_reference_on_corpus(self):
        for sample in CORPUS:
            with self.subTest(sample=sample[:60]):
                self.assertEqual(
                    html_text.extract_visible_text(sample),
                    _reference_bs4_extract(sample),
                )


if __name__ == "__main__":
    unittest.main()
