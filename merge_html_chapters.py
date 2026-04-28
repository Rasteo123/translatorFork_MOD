#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import os
import re
import sys
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

try:
    from bs4 import BeautifulSoup, NavigableString, UnicodeDammit
except ImportError:  # pragma: no cover - fallback for bare Python installs
    BeautifulSoup = None
    NavigableString = None
    UnicodeDammit = None


HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}
SKIP_FILENAMES = {
    "combined.html",
    "combined.xhtml",
    "index.html",
    "nav.xhtml",
    "toc.html",
}


@dataclass
class Chapter:
    path: Path
    title: str
    blocks: list[tuple[str, str]]
    body_html: str

    @property
    def char_count(self) -> int:
        return sum(len(text) for _, text in self.blocks)


def natural_key(path: Path) -> tuple:
    parts: list[object] = []
    for chunk in re.split(r"(\d+)", path.name.lower()):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            parts.append(chunk)
    return tuple(parts)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if UnicodeDammit is not None:
        decoded = UnicodeDammit(raw, is_html=True)
        if decoded.unicode_markup is not None:
            return decoded.unicode_markup

    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def title_from_filename(path: Path) -> str:
    stem = path.stem
    stem = re.sub(
        r"(_translated(?:_[a-z0-9]+)?|translated_[a-z0-9]+|_validated|_dry_run)$",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    stem = re.sub(r"^(?:\d+[\s_.-]+)+", "", stem)
    stem = stem.replace("_", " ")
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or path.stem


def parse_with_bs4(raw_html: str, path: Path) -> Chapter:
    soup = BeautifulSoup(raw_html, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    root = soup.body or soup
    heading = root.find(["h1", "h2", "h3"])
    title = heading.get_text(" ", strip=True) if heading else title_from_filename(path)

    body_parts: list[str] = []
    for child in root.children:
        if isinstance(child, NavigableString):
            if str(child).strip():
                body_parts.append(f"<p>{html.escape(str(child).strip())}</p>")
        else:
            body_parts.append(str(child))
    body_html = "\n".join(part for part in body_parts if part.strip())

    block_tags = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "div"}
    blocks: list[tuple[str, str]] = []
    for tag in root.find_all(block_tags):
        name = str(tag.name).lower()
        if name == "div" and tag.find(block_tags - {"div"}):
            continue
        if name != "div" and tag.find_parent(block_tags):
            parent = tag.find_parent(block_tags)
            if parent and str(parent.name).lower() != "div":
                continue
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        if not blocks and name.startswith("h") and normalize_title(text) == normalize_title(title):
            continue
        blocks.append((name, text))

    if not blocks:
        text = root.get_text("\n", strip=True)
        blocks = [("p", line.strip()) for line in text.splitlines() if line.strip()]

    return Chapter(path=path, title=title, blocks=blocks, body_html=body_html)


def parse_without_bs4(raw_html: str, path: Path) -> Chapter:
    title_match = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", raw_html, flags=re.I | re.S)
    title = title_from_filename(path)
    if title_match:
        title_text = strip_tags(title_match.group(1)).strip()
        if title_text:
            title = title_text

    body_match = re.search(r"<body[^>]*>(.*?)</body>", raw_html, flags=re.I | re.S)
    body_html = body_match.group(1).strip() if body_match else raw_html.strip()
    text = html.unescape(strip_tags(body_html))
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    blocks = [("p", line) for line in lines if line]
    return Chapter(path=path, title=title, blocks=blocks, body_html=body_html)


def strip_tags(value: str) -> str:
    value = re.sub(r"<(script|style)\b.*?</\1>", "", value, flags=re.I | re.S)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</(p|div|h[1-6]|li|blockquote|pre)>", "\n", value, flags=re.I)
    return re.sub(r"<[^>]+>", "", value)


def normalize_title(value: str) -> str:
    return re.sub(r"\W+", "", (value or "").casefold())


def parse_chapter(path: Path) -> Chapter:
    raw = read_text(path)
    if BeautifulSoup is not None:
        return parse_with_bs4(raw, path)
    return parse_without_bs4(raw, path)


def find_html_files(input_dir: Path, pattern: str, recursive: bool, output_paths: set[Path]) -> list[Path]:
    iterator = input_dir.rglob(pattern) if recursive else input_dir.glob(pattern)
    files: list[Path] = []
    for path in iterator:
        if not path.is_file():
            continue
        if path.suffix.lower() not in HTML_EXTENSIONS:
            continue
        resolved = path.resolve()
        if resolved in output_paths:
            continue
        if path.name.lower() in SKIP_FILENAMES:
            continue
        files.append(path)
    return sorted(files, key=natural_key)


def block_to_xhtml(tag_name: str, text: str) -> str:
    escaped = html.escape(text, quote=False)
    if re.fullmatch(r"h[1-6]", tag_name):
        return f"<h3>{escaped}</h3>"
    if tag_name == "li":
        return f"<p>- {escaped}</p>"
    if tag_name == "blockquote":
        return f'<p class="quote">{escaped}</p>'
    if tag_name == "pre":
        return f"<pre>{html.escape(text)}</pre>"
    return f"<p>{escaped}</p>"


def build_safe_body(chapters: list[Chapter]) -> str:
    parts: list[str] = []
    for index, chapter in enumerate(chapters, start=1):
        parts.append(f'<div class="chapter" id="ch{index:04d}">')
        parts.append(f"<h2>{html.escape(chapter.title, quote=False)}</h2>")
        if chapter.blocks:
            parts.extend(block_to_xhtml(tag_name, text) for tag_name, text in chapter.blocks)
        else:
            parts.append('<p class="empty">[empty source chapter]</p>')
        parts.append("</div>")
    return "\n".join(parts)


def build_kept_body(chapters: list[Chapter]) -> str:
    parts: list[str] = []
    for index, chapter in enumerate(chapters, start=1):
        parts.append(f'<section class="chapter" id="ch{index:04d}">')
        if f'id="ch{index:04d}"' not in chapter.body_html:
            parts.append(f"<h1>{html.escape(chapter.title, quote=False)}</h1>")
        parts.append(chapter.body_html)
        parts.append("</section>")
    return "\n".join(parts)


def html_document(title: str, body: str) -> str:
    safe_title = html.escape(title, quote=False)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{safe_title}</title>
  <style>
    body {{ max-width: 820px; margin: 2rem auto; padding: 0 1rem; font-family: Georgia, serif; line-height: 1.6; }}
    .chapter {{ break-before: page; page-break-before: always; margin: 0 0 3rem; }}
    .chapter:first-child {{ break-before: auto; page-break-before: auto; }}
    h1, h2, h3 {{ line-height: 1.25; }}
    p {{ margin: 0 0 0.85rem; }}
    .quote {{ margin-left: 1.5rem; font-style: italic; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def xhtml_document(title: str, body: str) -> str:
    safe_title = html.escape(title, quote=False)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>{safe_title}</title>
  <link rel="stylesheet" type="text/css" href="../styles.css" />
</head>
<body>
{body}
</body>
</html>
"""


def create_epub(epub_path: Path, title: str, author: str, chapters: list[Chapter]) -> None:
    book_id = str(uuid.uuid4())
    body = build_safe_body(chapters)
    combined_xhtml = xhtml_document(title, body)
    safe_title = html.escape(title, quote=True)
    safe_author = html.escape(author, quote=True)

    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    content_opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookID" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{safe_title}</dc:title>
    <dc:creator>{safe_author}</dc:creator>
    <dc:language>ru</dc:language>
    <dc:identifier id="BookID">urn:uuid:{book_id}</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="styles" href="styles.css" media-type="text/css"/>
    <item id="combined" href="text/combined.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="combined"/>
  </spine>
</package>
"""
    nav_points = []
    for index, chapter in enumerate(chapters, start=1):
        chapter_title = html.escape(chapter.title, quote=True)
        nav_points.append(
            f"""    <navPoint id="navPoint-{index}" playOrder="{index}">
      <navLabel><text>{chapter_title}</text></navLabel>
      <content src="text/combined.xhtml#ch{index:04d}"/>
    </navPoint>"""
        )
    toc_ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:{book_id}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{safe_title}</text></docTitle>
  <navMap>
{chr(10).join(nav_points)}
  </navMap>
</ncx>
"""
    styles = """body { font-family: Georgia, serif; line-height: 1.45; }
.chapter { page-break-before: always; margin-bottom: 2em; }
.chapter:first-child { page-break-before: auto; }
h2, h3 { line-height: 1.25; }
p { margin: 0 0 0.85em 0; }
.quote { margin-left: 1.5em; font-style: italic; }
"""

    epub_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(epub_path, "w") as epub:
        mimetype_info = zipfile.ZipInfo("mimetype")
        mimetype_info.compress_type = zipfile.ZIP_STORED
        epub.writestr(mimetype_info, "application/epub+zip")
        epub.writestr("META-INF/container.xml", container_xml, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr("OEBPS/content.opf", content_opf, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr("OEBPS/toc.ncx", toc_ncx, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr("OEBPS/styles.css", styles, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr("OEBPS/text/combined.xhtml", combined_xhtml, compress_type=zipfile.ZIP_DEFLATED)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge translated HTML chapter files into one HTML file and optionally one-file EPUB."
    )
    parser.add_argument("input_dir", help="Folder with translated .html/.htm/.xhtml chapter files.")
    parser.add_argument("--pattern", default="*.htm*", help='File glob, for example "*_translated_gemini.html".')
    parser.add_argument("--recursive", action="store_true", help="Search subfolders too.")
    parser.add_argument("--out", help="Output combined HTML path. Default: <input_dir>/combined.html.")
    parser.add_argument("--epub", help="Optional output EPUB path.")
    parser.add_argument("--title", help="Book title. Default: input folder name.")
    parser.add_argument("--author", default="Unknown", help="EPUB author metadata.")
    parser.add_argument(
        "--keep-html",
        action="store_true",
        help="Keep source body markup in combined HTML. EPUB output always uses safe plain XHTML.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        print(f"Input folder not found: {input_dir}", file=sys.stderr)
        return 2

    out_path = Path(args.out).expanduser().resolve() if args.out else input_dir / "combined.html"
    epub_path = Path(args.epub).expanduser().resolve() if args.epub else None
    output_paths = {out_path}
    if epub_path:
        output_paths.add(epub_path)

    files = find_html_files(input_dir, args.pattern, args.recursive, output_paths)
    if not files:
        print(f"No HTML files found in {input_dir} with pattern {args.pattern!r}.", file=sys.stderr)
        return 1

    chapters = [parse_chapter(path) for path in files]
    title = args.title or input_dir.name
    body = build_kept_body(chapters) if args.keep_html else build_safe_body(chapters)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_document(title, body), encoding="utf-8")

    if epub_path:
        create_epub(epub_path, title, args.author, chapters)

    total_chars = sum(chapter.char_count for chapter in chapters)
    empty = [chapter.path.name for chapter in chapters if chapter.char_count == 0]
    print(f"Merged chapters: {len(chapters)}")
    print(f"Total text chars: {total_chars}")
    print(f"HTML: {out_path}")
    if epub_path:
        print(f"EPUB: {epub_path}")
    if empty:
        print("Warning: chapters with no extracted text:")
        for name in empty:
            print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
