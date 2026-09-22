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


def test_two_system_window_blocks_keep_their_order():
    converter = EPUBConverterThread("book.epub")
    html = f"<body>{_block('Первый')}<p>Между.</p>{_block('Второй')}</body>"

    text = converter._html_to_plain_text(html)

    assert [line[:5] if line.startswith("<div") else line for line in text.split("\n")] == ["<div ", "Между.", "<div "]
    assert text.index("Первый") < text.index("Между.") < text.index("Второй")
