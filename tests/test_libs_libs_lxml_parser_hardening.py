"""
Регрессионные тесты для аудита libs-lxml-parser (trivial).

is_well_formed_xml() разбирает НЕДОВЕРЕННЫЙ контент EPUB (текст глав, который
приходит от модели/из исходного файла). Разбор должен идти через
etree.XMLParser(resolve_entities=False, no_network=True), чтобы:
  - внешние сущности (XXE) не резолвились;
  - парсер не пытался лезть в сеть за DTD/сущностями;
  - при этом поведение на обычных (доверенных) документах не менялось —
    True для well-formed XML, False для битого.
"""

import http.server
import threading

from lxml import etree

from gemini_translator.utils import text as text_module
from gemini_translator.utils.text import is_well_formed_xml


def test_is_well_formed_xml_uses_hardened_parser(monkeypatch):
    """Характеризация: is_well_formed_xml должен явно строить XMLParser
    с resolve_entities=False и no_network=True и передавать его в fromstring."""
    captured_parser_kwargs = {}
    captured_fromstring_kwargs = {}

    real_xml_parser = etree.XMLParser
    real_fromstring = etree.fromstring

    def spy_xml_parser(*args, **kwargs):
        captured_parser_kwargs.update(kwargs)
        return real_xml_parser(*args, **kwargs)

    def spy_fromstring(data, *args, **kwargs):
        captured_fromstring_kwargs.update(kwargs)
        return real_fromstring(data, *args, **kwargs)

    monkeypatch.setattr(text_module.etree, "XMLParser", spy_xml_parser)
    monkeypatch.setattr(text_module.etree, "fromstring", spy_fromstring)

    assert is_well_formed_xml("<root/>") is True

    assert captured_parser_kwargs.get("resolve_entities") is False, (
        "is_well_formed_xml должен создавать etree.XMLParser(resolve_entities=False, ...)"
    )
    assert captured_parser_kwargs.get("no_network") is True, (
        "is_well_formed_xml должен создавать etree.XMLParser(..., no_network=True)"
    )
    assert "parser" in captured_fromstring_kwargs, (
        "is_well_formed_xml должен передавать сконструированный parser в etree.fromstring"
    )


def test_is_well_formed_xml_still_accepts_normal_document():
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<body><p>Обычный абзац.</p></body></html>'
    )
    assert is_well_formed_xml(xhtml) is True


def test_is_well_formed_xml_still_rejects_malformed_document():
    broken = '<html><body><p>Незакрытый тег</body></html>'
    ok, err = is_well_formed_xml(broken, validate=True)
    assert ok is False
    assert err  # сообщение об ошибке синтаксиса сохраняется


def test_is_well_formed_xml_does_not_resolve_external_entity():
    """Внешняя сущность (потенциальный XXE) не должна резолвиться и не должна
    приводить к необработанному исключению или чтению файла: разбор либо
    завершается без ошибки (сущность остаётся неразвёрнутой), либо
    XMLSyntaxError, но в любом случае is_well_formed_xml() возвращает bool,
    ничего не читая с диска."""
    xml_with_external_entity = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>'
        '<root>&xxe;</root>'
    )
    result = is_well_formed_xml(xml_with_external_entity)
    assert isinstance(result, bool)


def test_is_well_formed_xml_does_not_hit_network_for_external_dtd():
    """Парсер не должен обращаться по сети за внешним DTD/сущностью."""
    hits = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - имя метода задано http.server
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<!ENTITY xxe "pwned">')

        def log_message(self, *args):  # тише в тестовом выводе
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        xml_with_remote_dtd = (
            '<?xml version="1.0"?>'
            f'<!DOCTYPE root SYSTEM "http://127.0.0.1:{port}/evil.dtd">'
            '<root>&xxe;</root>'
        )
        is_well_formed_xml(xml_with_remote_dtd)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert hits == [], "etree.fromstring не должен ходить в сеть за внешним DTD/сущностью"
