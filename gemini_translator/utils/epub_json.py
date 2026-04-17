# -*- coding: utf-8 -*-

import base64
import codecs
import copy
import html
import json
import mimetypes
import os
import re
import zipfile
from json import JSONDecodeError

from bs4 import BeautifulSoup, Comment, NavigableString, Tag, UnicodeDammit

from .text import process_body_tag


BOOK_SCHEMA_VERSION = "rulate.epub_book.v1"
DOCUMENT_SCHEMA_VERSION = "rulate.document.v1"
TRANSLATION_PAYLOAD_VERSION = "rulate.translation_payload.v1"
BATCH_TRANSLATION_PAYLOAD_VERSION = "rulate.translation_batch.v1"
TRANSPORT_PAYLOAD_VERSION = 1
DEFAULT_TEXT_ENCODING = "utf-8"

HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}
TEXT_EXTENSIONS = {
    ".opf",
    ".ncx",
    ".xml",
    ".css",
    ".js",
    ".json",
    ".txt",
    ".svg",
    ".smil",
    ".md",
}
VOID_TAGS = {"br", "hr", "img", "meta", "link", "input", "source", "track", "wbr"}
INLINE_TAGS = {
    "a",
    "abbr",
    "b",
    "bdi",
    "bdo",
    "br",
    "cite",
    "code",
    "del",
    "dfn",
    "em",
    "i",
    "img",
    "ins",
    "kbd",
    "mark",
    "q",
    "rp",
    "rt",
    "ruby",
    "s",
    "samp",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "time",
    "u",
    "var",
    "wbr",
}
EXPLICIT_BLOCK_TAGS = {
    "address",
    "aside",
    "blockquote",
    "caption",
    "dd",
    "details",
    "div",
    "dt",
    "figcaption",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "label",
    "legend",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "summary",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
RAW_HTML_TAGS = {"audio", "canvas", "iframe", "math", "object", "picture", "script", "style", "svg", "video"}
TRANSLATABLE_ATTRS = {"alt", "aria-description", "aria-label", "summary", "title"}


def _normalize_zip_path(path):
    return str(path).replace("\\", "/")


def _normalize_encoding_name(encoding):
    if not encoding:
        return DEFAULT_TEXT_ENCODING
    try:
        return codecs.lookup(str(encoding)).name
    except LookupError:
        return str(encoding)


def _decode_text_bytes(raw_bytes, *, is_html=False):
    if raw_bytes is None:
        return "", DEFAULT_TEXT_ENCODING

    bom_encodings = (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF16_BE, "utf-16"),
        (codecs.BOM_UTF16_LE, "utf-16"),
    )
    for bom, encoding in bom_encodings:
        if raw_bytes.startswith(bom):
            return raw_bytes.decode(encoding), _normalize_encoding_name(encoding)

    decoded = UnicodeDammit(raw_bytes, is_html=is_html)
    if decoded.unicode_markup is not None:
        return decoded.unicode_markup, _normalize_encoding_name(decoded.original_encoding)

    return raw_bytes.decode(DEFAULT_TEXT_ENCODING, "replace"), DEFAULT_TEXT_ENCODING


def _encode_text_content(text, encoding):
    normalized_encoding = _normalize_encoding_name(encoding)
    try:
        return str(text).encode(normalized_encoding)
    except (LookupError, UnicodeEncodeError):
        return str(text).encode(DEFAULT_TEXT_ENCODING)


def _guess_media_type(path):
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type or "application/octet-stream"


def _is_html_path(path):
    return os.path.splitext(path.lower())[1] in HTML_EXTENSIONS


def _is_text_path(path, media_type=None):
    extension = os.path.splitext(path.lower())[1]
    if extension in TEXT_EXTENSIONS:
        return True
    if media_type:
        return media_type.startswith("text/") or media_type in {
            "application/xhtml+xml",
            "application/x-dtbncx+xml",
            "application/oebps-package+xml",
            "application/xml",
            "image/svg+xml",
        }
    return False


def _zip_info_to_metadata(info):
    return {
        "path": _normalize_zip_path(info.filename),
        "compress_type": info.compress_type,
        "date_time": list(info.date_time),
        "comment_b64": base64.b64encode(info.comment or b"").decode("ascii"),
        "extra_b64": base64.b64encode(info.extra or b"").decode("ascii"),
        "create_system": getattr(info, "create_system", 0),
        "create_version": getattr(info, "create_version", 20),
        "extract_version": getattr(info, "extract_version", 20),
        "flag_bits": getattr(info, "flag_bits", 0),
        "internal_attr": getattr(info, "internal_attr", 0),
        "external_attr": getattr(info, "external_attr", 0),
        "volume": getattr(info, "volume", 0),
        "reserved": getattr(info, "reserved", 0),
        "is_dir": info.is_dir(),
    }


def _zip_info_from_metadata(metadata):
    zip_info = zipfile.ZipInfo(
        filename=_normalize_zip_path(metadata["path"]),
        date_time=tuple(metadata.get("date_time", (1980, 1, 1, 0, 0, 0))),
    )
    zip_info.compress_type = metadata.get("compress_type", zipfile.ZIP_DEFLATED)
    zip_info.comment = base64.b64decode(metadata.get("comment_b64", "") or "")
    zip_info.extra = base64.b64decode(metadata.get("extra_b64", "") or "")
    zip_info.create_system = metadata.get("create_system", 0)
    zip_info.create_version = metadata.get("create_version", 20)
    zip_info.extract_version = metadata.get("extract_version", 20)
    zip_info.flag_bits = metadata.get("flag_bits", 0)
    zip_info.internal_attr = metadata.get("internal_attr", 0)
    zip_info.external_attr = metadata.get("external_attr", 0)
    zip_info.volume = metadata.get("volume", 0)
    zip_info.reserved = metadata.get("reserved", 0)
    return zip_info


def _build_node_id(path_parts):
    if not path_parts:
        return "root"
    return "n." + ".".join(str(part) for part in path_parts)


def _classify_tag_role(tag_name, attrs):
    tag_name = (tag_name or "").lower()
    attrs = attrs or {}

    if re.fullmatch(r"h[1-6]", tag_name):
        return "heading"
    if tag_name == "p":
        return "paragraph"
    if tag_name == "li":
        return "list_item"
    if tag_name in {"ol", "ul"}:
        return "list"
    if tag_name in {"blockquote"}:
        return "blockquote"
    if tag_name in {"td", "th"}:
        return "table_cell"
    if tag_name in {"dd", "dt"}:
        return "definition_item"
    if tag_name in {"caption", "figcaption"}:
        return "caption"
    if tag_name == "a":
        attrs_map = {key.lower(): value for key, value in attrs.items()}
        epub_type = str(attrs_map.get("epub:type", "")).lower()
        role = str(attrs_map.get("role", "")).lower()
        href = str(attrs_map.get("href", ""))
        if "noteref" in epub_type or "doc-noteref" in role or href.startswith("#"):
            return "note_ref"
        return "link"
    if tag_name in {"strong", "b"}:
        return "strong"
    if tag_name in {"em", "i"}:
        return "emphasis"
    if tag_name in {"sub", "sup"}:
        return "inline_position"
    if tag_name in {"img", "picture", "svg", "audio", "video", "source", "math"}:
        return "media"
    if tag_name in {"section", "article", "aside", "div", "body"}:
        return "container"
    if tag_name in INLINE_TAGS:
        return "inline"
    if tag_name in EXPLICIT_BLOCK_TAGS:
        return "block"
    return "unknown"


def _normalize_attr_value(value):
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if value is None:
        return None
    return str(value)


def _attrs_to_list(tag):
    attr_items = []
    for name, value in tag.attrs.items():
        attr_items.append({
            "name": str(name),
            "value": _normalize_attr_value(value),
        })
    return attr_items


def _attrs_list_to_map(attr_items):
    result = {}
    for item in attr_items or []:
        result[str(item["name"])] = item.get("value")
    return result


def _extract_translatable_attrs(attr_items):
    extracted = []
    for item in attr_items or []:
        name = str(item["name"]).lower()
        value = item.get("value")
        if name not in TRANSLATABLE_ATTRS:
            continue
        if value is None:
            continue
        if isinstance(value, list):
            value = " ".join(str(part) for part in value)
        value = str(value)
        if not value.strip():
            continue
        extracted.append({"name": item["name"], "value": value})
    return extracted


def _update_translatable_attrs(attr_items, translated_attrs):
    translated_map = {
        str(item.get("name")): str(item.get("value", ""))
        for item in (translated_attrs or [])
    }
    updated = []
    for item in attr_items or []:
        current = dict(item)
        if current["name"] in translated_map:
            current["value"] = translated_map[current["name"]]
        updated.append(current)
    return updated


def _bs4_to_node(node, path_parts):
    node_id = _build_node_id(path_parts)

    if isinstance(node, Comment):
        return {
            "node_id": node_id,
            "path": list(path_parts),
            "kind": "comment",
            "text": str(node),
        }

    if isinstance(node, NavigableString):
        return {
            "node_id": node_id,
            "path": list(path_parts),
            "kind": "text",
            "text": str(node),
        }

    if isinstance(node, Tag):
        attrs = _attrs_to_list(node)
        role = _classify_tag_role(node.name, node.attrs)

        if node.name.lower() in RAW_HTML_TAGS:
            return {
                "node_id": node_id,
                "path": list(path_parts),
                "kind": "raw_html",
                "tag": node.name.lower(),
                "role": role,
                "html": str(node),
            }

        return {
            "node_id": node_id,
            "path": list(path_parts),
            "kind": "tag",
            "tag": node.name.lower(),
            "role": role,
            "attrs": attrs,
            "self_closing": bool(getattr(node, "is_empty_element", False) or (node.name.lower() in VOID_TAGS and not node.contents)),
            "children": [
                _bs4_to_node(child, [*path_parts, index])
                for index, child in enumerate(node.contents)
            ],
        }

    return {
        "node_id": node_id,
        "path": list(path_parts),
        "kind": "text",
        "text": str(node),
    }


def _node_has_inline_payload(node):
    for child in node.get("children", []):
        if child["kind"] in {"comment", "raw_html"}:
            return True
        if child["kind"] == "text" and child.get("text", "").strip():
            return True
        if child["kind"] == "tag" and child.get("tag") in INLINE_TAGS:
            return True
    return False


def _should_promote_to_block(node):
    if node.get("kind") != "tag":
        return False
    tag_name = node.get("tag", "")
    if tag_name in {"body", "html", "head"}:
        return False
    if tag_name in EXPLICIT_BLOCK_TAGS:
        return True
    if tag_name in INLINE_TAGS:
        return False
    return _node_has_inline_payload(node)


def _node_to_inline_fragment(node):
    node_id = node["node_id"]
    if node["kind"] == "text":
        return {"id": node_id, "type": "text", "text": node.get("text", "")}
    if node["kind"] == "comment":
        return {"id": node_id, "type": "comment", "text": node.get("text", "")}
    if node["kind"] == "raw_html":
        return {
            "id": node_id,
            "type": "opaque",
            "tag": node.get("tag"),
            "role": node.get("role"),
        }
    if node["kind"] != "tag":
        return {"id": node_id, "type": "text", "text": str(node)}

    tag_name = node.get("tag", "")
    if tag_name in {"br", "hr"}:
        return {"id": node_id, "type": "break", "tag": tag_name, "role": node.get("role")}

    fragment = {
        "id": node_id,
        "type": "element",
        "tag": tag_name,
        "role": node.get("role"),
        "attrs_text": _extract_translatable_attrs(node.get("attrs", [])),
        "children": [_node_to_inline_fragment(child) for child in node.get("children", [])],
    }
    if fragment["role"] in {"link", "note_ref"}:
        fragment["href"] = _attrs_list_to_map(node.get("attrs", [])).get("href")
    return fragment


def _build_translation_blocks(document_model):
    blocks = []

    def walk(node):
        if node["kind"] == "tag" and _should_promote_to_block(node):
            blocks.append({
                "id": node["node_id"],
                "path": list(node.get("path", [])),
                "tag": node.get("tag"),
                "role": node.get("role"),
                "attrs_text": _extract_translatable_attrs(node.get("attrs", [])),
                "inlines": [_node_to_inline_fragment(child) for child in node.get("children", [])],
            })
            return

        if node["kind"] in {"text", "comment", "raw_html"}:
            if node["kind"] != "text" or node.get("text", "").strip():
                blocks.append({
                    "id": node["node_id"],
                    "path": list(node.get("path", [])),
                    "tag": "__synthetic__",
                    "role": "text_flow",
                    "attrs_text": [],
                    "inlines": [_node_to_inline_fragment(node)],
                })
            return

        for child in node.get("children", []):
            walk(child)

    for child in document_model.get("body", {}).get("children", []):
        walk(child)

    return blocks


def _walk_nodes(node, callback):
    callback(node)
    for child in node.get("children", []):
        _walk_nodes(child, callback)


def _build_node_index(document_model):
    node_index = {}
    body = document_model.get("body", {})
    node_index[body.get("node_id", "root")] = body

    for child in body.get("children", []):
        _walk_nodes(child, lambda item: node_index.__setitem__(item["node_id"], item))

    return node_index


def _text_chars_in_payload(payload):
    total = 0

    def walk_fragment(fragment):
        nonlocal total
        if fragment.get("type") == "text":
            total += len(fragment.get("text", ""))
        for attr_item in fragment.get("attrs_text", []):
            total += len(str(attr_item.get("value", "")))
        for child in fragment.get("children", []):
            walk_fragment(child)

    for block in payload.get("blocks", []):
        for attr_item in block.get("attrs_text", []):
            total += len(str(attr_item.get("value", "")))
        for inline in block.get("inlines", []):
            walk_fragment(inline)
    return total


def _text_chars_in_transport_fragment(fragment):
    total = 0
    if not isinstance(fragment, dict):
        return total

    if "x" in fragment:
        total += len(str(fragment.get("x", "")))

    for value in fragment.get("a", []):
        total += len(str(value))

    for child in fragment.get("c", []):
        total += _text_chars_in_transport_fragment(child)

    return total


def _text_chars_in_transport_payload(payload):
    total = 0
    for block in payload.get("b", []):
        for value in block.get("a", []):
            total += len(str(value))
        for inline in block.get("c", []):
            total += _text_chars_in_transport_fragment(inline)
    return total


def _payload_text_chars(payload):
    if not isinstance(payload, dict):
        return 0

    documents = payload.get("documents")
    if isinstance(documents, list):
        return sum(_payload_text_chars(document) for document in documents)

    if "b" in payload:
        return _text_chars_in_transport_payload(payload)

    if "blocks" in payload:
        return _text_chars_in_payload(payload)

    return 0


def build_html_document_model(html_content, document_id=None, source_encoding=None):
    prefix_html, body_inner_html, suffix_html = process_body_tag(
        html_content,
        return_parts=True,
        body_content_only=True,
    )
    has_body_wrapper = bool(prefix_html or suffix_html)
    body_soup = BeautifulSoup(body_inner_html, "html.parser")
    body_children = [
        _bs4_to_node(child, [index])
        for index, child in enumerate(body_soup.contents)
    ]

    return {
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "document_id": document_id or "",
        "source_encoding": _normalize_encoding_name(source_encoding) if source_encoding else None,
        "kind": "html_document",
        "has_body_wrapper": has_body_wrapper,
        "prefix_html": prefix_html if has_body_wrapper else "",
        "suffix_html": suffix_html if has_body_wrapper else "",
        "body": {
            "node_id": "root",
            "kind": "root",
            "role": "body",
            "children": body_children,
        },
    }


def render_document_html(document_model):
    def render_node(node):
        kind = node.get("kind")
        if kind == "text":
            return html.escape(node.get("text", ""), quote=False)
        if kind == "comment":
            return f"<!--{node.get('text', '')}-->"
        if kind == "raw_html":
            return node.get("html", "")
        if kind == "root":
            return "".join(render_node(child) for child in node.get("children", []))

        if kind != "tag":
            return html.escape(str(node), quote=False)

        tag_name = node.get("tag", "div")
        attr_parts = []
        for item in node.get("attrs", []):
            name = str(item["name"])
            value = item.get("value")
            if value is None:
                attr_parts.append(name)
                continue
            if isinstance(value, list):
                value = " ".join(str(part) for part in value)
            value = html.escape(str(value), quote=True)
            attr_parts.append(f'{name}="{value}"')

        attrs_text = (" " + " ".join(attr_parts)) if attr_parts else ""
        children_html = "".join(render_node(child) for child in node.get("children", []))
        if node.get("self_closing") and not children_html:
            return f"<{tag_name}{attrs_text}/>"
        return f"<{tag_name}{attrs_text}>{children_html}</{tag_name}>"

    body_html = "".join(render_node(child) for child in document_model.get("body", {}).get("children", []))
    if document_model.get("has_body_wrapper"):
        return f"{document_model.get('prefix_html', '')}{body_html}{document_model.get('suffix_html', '')}"
    return body_html


def build_translation_payload(document_model, document_id=None):
    payload = {
        "schema_version": TRANSLATION_PAYLOAD_VERSION,
        "document_id": document_id or document_model.get("document_id", ""),
        "blocks": _build_translation_blocks(document_model),
    }
    return payload


def _compact_attr_values(attrs_text):
    return [str(item.get("value", "")) for item in (attrs_text or [])]


def _to_transport_fragment(source_fragment):
    fragment_type = source_fragment.get("type")
    if fragment_type == "text":
        return {"x": source_fragment.get("text", "")}
    if fragment_type == "comment":
        return {"m": source_fragment.get("text", "")}
    if fragment_type == "opaque":
        return {"o": source_fragment.get("tag")}
    if fragment_type == "break":
        return {"k": source_fragment.get("tag")}

    fragment = {"t": source_fragment.get("tag")}
    attrs = _compact_attr_values(source_fragment.get("attrs_text", []))
    children = [_to_transport_fragment(child) for child in source_fragment.get("children", [])]
    if attrs:
        fragment["a"] = attrs
    if children:
        fragment["c"] = children
    if source_fragment.get("href"):
        fragment["h"] = source_fragment.get("href")
    return fragment


def build_transport_payload(source_payload):
    if isinstance(source_payload, dict) and "b" in source_payload and "blocks" not in source_payload:
        return copy.deepcopy(source_payload)

    return {
        "v": TRANSPORT_PAYLOAD_VERSION,
        "doc": source_payload.get("document_id", ""),
        "b": [
            {
                **({"t": block.get("tag")} if block.get("tag") else {}),
                **({"a": _compact_attr_values(block.get("attrs_text", []))} if block.get("attrs_text") else {}),
                "c": [_to_transport_fragment(item) for item in block.get("inlines", [])],
            }
            for block in source_payload.get("blocks", [])
        ],
    }


def build_batch_translation_payload(documents):
    prepared_documents = []
    for document in documents:
        if isinstance(document, dict):
            prepared_documents.append(build_transport_payload(document))
        else:
            prepared_documents.append(document)

    return {
        "schema_version": BATCH_TRANSLATION_PAYLOAD_VERSION,
        "transport_version": TRANSPORT_PAYLOAD_VERSION,
        "documents": prepared_documents,
    }


def _validate_attr_translation(source_attrs, candidate_attrs):
    if len(source_attrs) != len(candidate_attrs):
        raise ValueError("Количество attrs_text изменилось.")

    normalized_candidate = []
    for source_item, candidate_item in zip(source_attrs, candidate_attrs):
        if str(source_item.get("name")) != str(candidate_item.get("name")):
            raise ValueError("Порядок или имена attrs_text изменились.")
        normalized_candidate.append({
            "name": source_item.get("name"),
            "value": str(candidate_item.get("value", "")),
        })

    return normalized_candidate


def _validate_transport_attr_values(source_attrs, candidate_values):
    candidate_values = candidate_values or []
    if len(source_attrs) != len(candidate_values):
        raise ValueError("Количество attrs_text изменилось.")
    return [str(value) for value in candidate_values]


def _validate_transport_fragment(source_fragment, candidate_fragment):
    if not isinstance(candidate_fragment, dict):
        raise ValueError("Inline-элемент в compact JSON должен быть объектом.")

    fragment_type = source_fragment.get("type")
    if fragment_type == "text":
        if "x" not in candidate_fragment:
            raise ValueError("Для текстового фрагмента отсутствует поле x.")
        return {"x": str(candidate_fragment.get("x", ""))}

    if fragment_type == "comment":
        return {"m": source_fragment.get("text", "")}

    if fragment_type == "opaque":
        if candidate_fragment.get("o") and candidate_fragment.get("o") != source_fragment.get("tag"):
            raise ValueError("Изменился opaque-тег в compact JSON.")
        return {"o": source_fragment.get("tag")}

    if fragment_type == "break":
        if candidate_fragment.get("k") and candidate_fragment.get("k") != source_fragment.get("tag"):
            raise ValueError("Изменился break-тег в compact JSON.")
        return {"k": source_fragment.get("tag")}

    if candidate_fragment.get("t") != source_fragment.get("tag"):
        raise ValueError("Изменился тег inline-элемента в compact JSON.")

    source_children = source_fragment.get("children", [])
    candidate_children = candidate_fragment.get("c", [])
    if len(source_children) != len(candidate_children):
        raise ValueError("Изменилось число дочерних inline-элементов в compact JSON.")

    normalized = {"t": source_fragment.get("tag")}
    attr_values = _validate_transport_attr_values(source_fragment.get("attrs_text", []), candidate_fragment.get("a", []))
    if attr_values:
        normalized["a"] = attr_values
    if source_children:
        normalized["c"] = [
            _validate_transport_fragment(source_child, candidate_child)
            for source_child, candidate_child in zip(source_children, candidate_children)
        ]
    if source_fragment.get("href"):
        normalized["h"] = source_fragment.get("href")
    return normalized


def validate_transport_payload(source_payload, candidate_payload):
    if not isinstance(candidate_payload, dict):
        raise ValueError("Ответ transport JSON должен быть объектом.")

    source_blocks = source_payload.get("blocks", [])
    candidate_blocks = candidate_payload.get("b")
    if not isinstance(candidate_blocks, list):
        raise ValueError("В compact JSON отсутствует список b.")
    if len(source_blocks) != len(candidate_blocks):
        raise ValueError("Количество блоков в compact JSON изменилось.")

    normalized_blocks = []
    for source_block, candidate_block in zip(source_blocks, candidate_blocks):
        if not isinstance(candidate_block, dict):
            raise ValueError("Блок в compact JSON должен быть объектом.")
        if candidate_block.get("t") != source_block.get("tag"):
            raise ValueError("Изменился тег блока в compact JSON.")

        source_inlines = source_block.get("inlines", [])
        candidate_inlines = candidate_block.get("c", [])
        if len(source_inlines) != len(candidate_inlines):
            raise ValueError("Изменилось число inline-элементов в compact JSON.")

        normalized_block = {
            "t": source_block.get("tag"),
            "c": [
                _validate_transport_fragment(source_inline, candidate_inline)
                for source_inline, candidate_inline in zip(source_inlines, candidate_inlines)
            ],
        }
        attr_values = _validate_transport_attr_values(source_block.get("attrs_text", []), candidate_block.get("a", []))
        if attr_values:
            normalized_block["a"] = attr_values
        normalized_blocks.append(normalized_block)

    return {
        "v": TRANSPORT_PAYLOAD_VERSION,
        "doc": source_payload.get("document_id", ""),
        "b": normalized_blocks,
    }


def _validate_fragment(source_fragment, candidate_fragment):
    if source_fragment.get("id") != candidate_fragment.get("id"):
        raise ValueError("Нарушен порядок или состав inline-элементов.")
    if source_fragment.get("type") != candidate_fragment.get("type"):
        raise ValueError("Изменился тип inline-элемента.")

    fragment_type = source_fragment.get("type")

    if fragment_type == "text":
        return {
            "id": source_fragment["id"],
            "type": "text",
            "text": str(candidate_fragment.get("text", "")),
        }

    if fragment_type in {"comment", "opaque", "break"}:
        if candidate_fragment.get("tag") and source_fragment.get("tag") != candidate_fragment.get("tag"):
            raise ValueError("Изменился служебный тег inline-элемента.")
        return copy.deepcopy(source_fragment)

    if source_fragment.get("tag") != candidate_fragment.get("tag"):
        raise ValueError("Изменился тег inline-элемента.")

    source_children = source_fragment.get("children", [])
    candidate_children = candidate_fragment.get("children", [])
    if len(source_children) != len(candidate_children):
        raise ValueError("Изменилось число дочерних inline-элементов.")

    return {
        "id": source_fragment["id"],
        "type": "element",
        "tag": source_fragment.get("tag"),
        "role": source_fragment.get("role"),
        "href": source_fragment.get("href"),
        "attrs_text": _validate_attr_translation(
            source_fragment.get("attrs_text", []),
            candidate_fragment.get("attrs_text", []),
        ),
        "children": [
            _validate_fragment(source_child, candidate_child)
            for source_child, candidate_child in zip(source_children, candidate_children)
        ],
    }


def validate_translation_payload(source_payload, candidate_payload):
    if not isinstance(candidate_payload, dict):
        raise ValueError("Ответ не является JSON-объектом.")

    source_blocks = source_payload.get("blocks", [])
    candidate_blocks = candidate_payload.get("blocks")
    if not isinstance(candidate_blocks, list):
        raise ValueError("В ответе отсутствует список blocks.")
    if len(source_blocks) != len(candidate_blocks):
        raise ValueError("Количество блоков изменилось.")

    normalized_blocks = []
    for source_block, candidate_block in zip(source_blocks, candidate_blocks):
        if source_block.get("id") != candidate_block.get("id"):
            raise ValueError("Нарушен порядок или идентификаторы блоков.")
        if source_block.get("tag") != candidate_block.get("tag"):
            raise ValueError("Изменился тег блока.")

        source_inlines = source_block.get("inlines", [])
        candidate_inlines = candidate_block.get("inlines")
        if not isinstance(candidate_inlines, list) or len(source_inlines) != len(candidate_inlines):
            raise ValueError("Изменилось количество inline-элементов в блоке.")

        normalized_blocks.append({
            "id": source_block["id"],
            "path": list(source_block.get("path", [])),
            "tag": source_block.get("tag"),
            "role": source_block.get("role"),
            "attrs_text": _validate_attr_translation(
                source_block.get("attrs_text", []),
                candidate_block.get("attrs_text", []),
            ),
            "inlines": [
                _validate_fragment(source_inline, candidate_inline)
                for source_inline, candidate_inline in zip(source_inlines, candidate_inlines)
            ],
        })

    return {
        "schema_version": TRANSLATION_PAYLOAD_VERSION,
        "document_id": source_payload.get("document_id", ""),
        "blocks": normalized_blocks,
    }


def apply_transport_payload(document_model, candidate_payload, source_payload):
    validated_payload = validate_transport_payload(source_payload, candidate_payload)
    node_index = _build_node_index(document_model)

    def apply_fragment(source_fragment, transport_fragment):
        source_node = node_index.get(source_fragment["id"])
        if not source_node:
            raise ValueError(f"Не найден узел {source_fragment['id']} в документе.")

        fragment_type = source_fragment.get("type")
        if fragment_type == "text":
            source_node["text"] = transport_fragment.get("x", "")
            return

        if fragment_type != "element":
            return

        source_attrs = source_fragment.get("attrs_text", [])
        if source_attrs:
            source_node["attrs"] = _update_translatable_attrs(
                source_node.get("attrs", []),
                [
                    {"name": attr_item["name"], "value": value}
                    for attr_item, value in zip(source_attrs, transport_fragment.get("a", []))
                ],
            )

        for source_child, transport_child in zip(source_fragment.get("children", []), transport_fragment.get("c", [])):
            apply_fragment(source_child, transport_child)

    for source_block, transport_block in zip(source_payload.get("blocks", []), validated_payload.get("b", [])):
        block_node = node_index.get(source_block["id"])
        if block_node and block_node.get("kind") == "tag" and source_block.get("attrs_text"):
            block_node["attrs"] = _update_translatable_attrs(
                block_node.get("attrs", []),
                [
                    {"name": attr_item["name"], "value": value}
                    for attr_item, value in zip(source_block.get("attrs_text", []), transport_block.get("a", []))
                ],
            )
        for source_inline, transport_inline in zip(source_block.get("inlines", []), transport_block.get("c", [])):
            apply_fragment(source_inline, transport_inline)

    return document_model


def apply_translation_payload(document_model, translated_payload, source_payload=None):
    source_payload = source_payload or build_translation_payload(document_model)
    validated_payload = validate_translation_payload(source_payload, translated_payload)
    node_index = _build_node_index(document_model)

    def apply_fragment(fragment):
        node = node_index.get(fragment["id"])
        if not node:
            raise ValueError(f"Не найден узел {fragment['id']} в документе.")

        if fragment["type"] == "text":
            node["text"] = fragment.get("text", "")
            return

        if fragment["type"] != "element":
            return

        node["attrs"] = _update_translatable_attrs(node.get("attrs", []), fragment.get("attrs_text", []))
        for child_fragment in fragment.get("children", []):
            apply_fragment(child_fragment)

    for block in validated_payload.get("blocks", []):
        block_node = node_index.get(block["id"])
        if block_node and block_node.get("kind") == "tag":
            block_node["attrs"] = _update_translatable_attrs(
                block_node.get("attrs", []),
                block.get("attrs_text", []),
            )
        for fragment in block.get("inlines", []):
            apply_fragment(fragment)

    return document_model


def extract_json_payload(raw_response):
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise ValueError("API вернул пустой ответ.")

    text = raw_response.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    decoder = json.JSONDecoder()
    candidates = [text]
    for start_char in ("{", "["):
        start_pos = text.find(start_char)
        if start_pos != -1:
            candidates.append(text[start_pos:].strip())

    last_error = None
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char not in "{[":
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[index:])
                return parsed
            except JSONDecodeError as exc:
                last_error = exc

    if last_error:
        raise ValueError(f"Не удалось разобрать JSON-ответ: {last_error}") from last_error
    raise ValueError("В ответе не найден JSON.")


def estimate_translation_noise(html_content, translation_payload):
    comparable_payload = translation_payload
    if isinstance(translation_payload, dict):
        documents = translation_payload.get("documents")
        if isinstance(documents, list):
            comparable_payload = {
                **translation_payload,
                "documents": [
                    build_transport_payload(document) if isinstance(document, dict) and "blocks" in document else document
                    for document in documents
                ],
            }
        elif "blocks" in translation_payload and "b" not in translation_payload:
            comparable_payload = build_transport_payload(translation_payload)

    payload_json = json.dumps(comparable_payload, ensure_ascii=False, separators=(",", ":"))
    text_only = BeautifulSoup(html_content or "", "html.parser").get_text()
    html_markup_chars = max(len(html_content or "") - len(text_only), 0)
    json_overhead_chars = max(len(payload_json) - _payload_text_chars(comparable_payload), 0)

    return {
        "html_markup_chars": html_markup_chars,
        "json_overhead_chars": json_overhead_chars,
        "json_is_less_noisy": json_overhead_chars < html_markup_chars,
        "reduction_chars": html_markup_chars - json_overhead_chars,
    }


def _extract_first_heading(document_model):
    first_heading = ""

    def visitor(node):
        nonlocal first_heading
        if first_heading:
            return
        if node.get("kind") == "tag" and node.get("role") == "heading":
            first_heading = BeautifulSoup(render_document_html({
                "has_body_wrapper": False,
                "body": {"children": [copy.deepcopy(node)]},
            }), "html.parser").get_text(" ", strip=True)

    for child in document_model.get("body", {}).get("children", []):
        _walk_nodes(child, visitor)
        if first_heading:
            break
    return first_heading


def epub_to_json_model(epub_path):
    from .epub_tools import get_epub_chapter_order

    chapter_order = get_epub_chapter_order(epub_path)
    chapter_positions = {path: index for index, path in enumerate(chapter_order)}

    entries = []
    chapters = []
    with zipfile.ZipFile(epub_path, "r") as epub_zip:
        for info in epub_zip.infolist():
            metadata = _zip_info_to_metadata(info)
            entry_path = metadata["path"]
            media_type = _guess_media_type(entry_path)
            entry = {
                "path": entry_path,
                "media_type": media_type,
                "zip_metadata": metadata,
            }

            if info.is_dir():
                entry["content_type"] = "directory"
                entries.append(entry)
                continue

            raw_bytes = epub_zip.read(info.filename)
            if _is_html_path(entry_path):
                html_text, detected_encoding = _decode_text_bytes(raw_bytes, is_html=True)
                document_model = build_html_document_model(
                    html_text,
                    document_id=entry_path,
                    source_encoding=detected_encoding,
                )
                entry["content_type"] = "document"
                entry["encoding"] = detected_encoding
                entry["document"] = document_model
                entry["metrics"] = {
                    "body_blocks": len(_build_translation_blocks(document_model)),
                }
                if entry_path in chapter_positions:
                    chapter_title = _extract_first_heading(document_model)
                    chapters.append({
                        "id": f"chapter-{chapter_positions[entry_path] + 1}",
                        "path": entry_path,
                        "order": chapter_positions[entry_path],
                        "title": chapter_title or os.path.basename(entry_path),
                    })
            elif _is_text_path(entry_path, media_type):
                text_content, detected_encoding = _decode_text_bytes(raw_bytes, is_html=False)
                entry["content_type"] = "text"
                entry["encoding"] = detected_encoding
                entry["text"] = text_content
            else:
                entry["content_type"] = "binary"
                entry["data_b64"] = base64.b64encode(raw_bytes).decode("ascii")

            entries.append(entry)

    chapters.sort(key=lambda item: item["order"])
    return {
        "schema_version": BOOK_SCHEMA_VERSION,
        "source_epub_path": str(epub_path),
        "metadata": {
            "epub_name": os.path.basename(str(epub_path)),
            "chapter_count": len(chapters),
            "entry_count": len(entries),
            "chapter_order": [chapter["path"] for chapter in chapters],
        },
        "chapters": chapters,
        "entries": entries,
    }


def json_model_to_epub(book_model, output_path):
    with zipfile.ZipFile(output_path, "w") as output_zip:
        for entry in book_model.get("entries", []):
            metadata = entry.get("zip_metadata", {})
            zip_info = _zip_info_from_metadata(metadata)

            if metadata.get("is_dir"):
                output_zip.writestr(zip_info, b"")
                continue

            content_type = entry.get("content_type")
            if content_type == "document":
                payload_bytes = _encode_text_content(
                    render_document_html(entry["document"]),
                    entry.get("encoding") or entry.get("document", {}).get("source_encoding"),
                )
            elif content_type == "text":
                payload_bytes = _encode_text_content(
                    entry.get("text", ""),
                    entry.get("encoding"),
                )
            elif content_type == "binary":
                payload_bytes = base64.b64decode(entry.get("data_b64", "") or "")
            else:
                payload_bytes = b""

            output_zip.writestr(zip_info, payload_bytes)


def save_json_model(book_model, json_path):
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(book_model, json_file, ensure_ascii=False, indent=2)


def load_json_model(json_path):
    with open(json_path, "r", encoding="utf-8") as json_file:
        return json.load(json_file)
