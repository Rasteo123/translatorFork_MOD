from gemini_translator.ui.dialogs.rulate_export import EPUBConverterThread


def test_html_to_plain_text_drops_style_blocks():
    converter = EPUBConverterThread("book.epub")
    html = """
    <html>
      <head>
        <style>
          p.p2 {margin: 0.0px 0.0px 12.0px 0.0px; font: 12.0px Times; -webkit-text-stroke: #000000}
          span.s1 {font-kerning: none}
        </style>
      </head>
      <body>
        <p class="p2"><span class="s1">Нормальный текст главы.</span></p>
      </body>
    </html>
    """

    text = converter._html_to_plain_text(html)

    assert text == "Нормальный текст главы."
    assert "p.p2" not in text
    assert "span.s1" not in text


def _block(inner, extra_attr=""):
    return (
        f'<div data-sys="notice"{extra_attr} style="border:2px solid #b388ff;color:#efe6ff;">'
        f"{inner}</div>"
    )


def test_system_window_block_survives_as_one_line_without_source_attribute():
    converter = EPUBConverterThread("book.epub")
    block = _block("<b>Динь!</b><br />Очки +1", ' data-sys-orig="&lt;p&gt;[Динь!]&lt;/p&gt;&#10;"')
    html = f"<body><p>До окна.</p>\n{block}\n<p>После окна.</p></body>"

    text = converter._html_to_plain_text(html)

    assert text == (
        "До окна.\n"
        '<div data-sys="notice" style="border:2px solid #b388ff;color:#efe6ff;">'
        "<b>Динь!</b><br />Очки +1</div>\n"
        "После окна."
    )


def test_system_window_block_with_line_breaks_is_collapsed_to_one_line():
    converter = EPUBConverterThread("book.epub")
    block = _block("<b>Динь!</b>\n  <br />\n  Очки +1\n")
    html = f"<body><p>Текст.</p>{block}</body>"

    text = converter._html_to_plain_text(html)

    lines = text.split("\n")
    assert len(lines) == 2
    assert lines[1].startswith("<div data-sys=") and lines[1].endswith("</div>")
    assert "<b>Динь!</b> <br /> Очки +1" in lines[1]


def test_system_window_block_from_a_built_epub_loses_its_source_attribute():
    # Сборка EPUB пересобирает главу через BeautifulSoup, и атрибут с кавычками
    # внутри уходит в одинарные кавычки; на сайт он попадать не должен.
    from bs4 import BeautifulSoup

    from gemini_translator.utils.system_windows import apply_windows, find_windows

    chapter = (
        "<html><head><title>t</title></head><body><h1>Глава 1</h1>"
        '<p class="no-indent">До окна.</p><p class="no-indent">[Динь! Очки +1]</p>'
        '<p class="no-indent">После окна.</p></body></html>'
    )
    wrapped, _ = apply_windows(chapter, find_windows(chapter))
    built = str(BeautifulSoup(wrapped, "html.parser"))
    assert "data-sys-orig='" in built

    text = EPUBConverterThread("book.epub")._html_to_plain_text(built)

    frame = [line for line in text.split("\n") if line.startswith("<div")]
    assert len(frame) == 1
    assert "data-sys-orig" not in frame[0]
    assert frame[0].endswith("</div>")


def test_two_system_window_blocks_keep_their_order():
    converter = EPUBConverterThread("book.epub")
    html = f"<body>{_block('Первый')}<p>Между.</p>{_block('Второй')}</body>"

    text = converter._html_to_plain_text(html)

    assert [line[:5] if line.startswith("<div") else line for line in text.split("\n")] == ["<div ", "Между.", "<div "]
    assert text.index("Первый") < text.index("Между.") < text.index("Второй")


def test_reference_definition_shaped_lines_survive_the_rulate_markdown():
    # Загрузчик Rulate прогоняет md через Markdown: «[Метка]: 80 (обожание)» для
    # него — определение ссылки, и строка пропадала со страницы главы.
    converter = EPUBConverterThread("book.epub")
    html = (
        "<body><p>[Текущая благосклонность]: 80 (обожание)</p>"
        "<p>[Уровень]: 5</p>"
        "<p>[Начальный Горн]: Модификация ядовитой железы.</p><p>Текст.</p></body>"
    )

    lines = converter._html_to_plain_text(html).split("\n")

    assert lines == [
        "\\[Текущая благосклонность]: 80 (обожание)",
        "\\[Уровень]: 5",
        "[Начальный Горн]: Модификация ядовитой железы.",
        "Текст.",
    ]


def test_angle_brackets_in_text_are_not_taken_for_tags():
    converter = EPUBConverterThread("book.epub")

    text = converter._html_to_plain_text("<body><p>Ник &lt;Shadow&gt; вошёл в игру.</p></body>")

    assert text == "Ник &lt;Shadow> вошёл в игру."
