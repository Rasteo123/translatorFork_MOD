# -*- coding: utf-8 -*-

import copy
import os
import posixpath
import re
import traceback
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import escape
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from .menu_utils import prompt_return_to_menu, return_to_main_menu


RULATE_HEADER_RE = re.compile(
    r"^\s*#\s*\[(.*?)\s*:\|:\s*:\|:\s*([01])\s*:\|:\s*(.*?)\]\s*$",
    re.MULTILINE,
)
BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "blockquote",
    "ul",
    "ol",
    "li",
    "table",
    "tr",
    "td",
    "pre",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}


@dataclass
class SplitSettings:
    split_threshold: int
    target_size: int
    min_part_size: int


@dataclass
class SplitStats:
    split_chapters: int = 0
    unchanged_chapters: int = 0
    output_chapters: int = 0


def append_part_suffix(title, part_number):
    return f"{title} (Часть {part_number})"


def text_length(value):
    return len(value.strip())


def normalize_posix_path(path):
    normalized = posixpath.normpath(path.replace("\\", "/"))
    return normalized.lstrip("./")


def split_plain_text_units(text, target_size):
    stripped = text.strip()
    if not stripped:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", stripped) if part.strip()]
    units = []
    for paragraph in paragraphs:
        units.extend(_explode_text_unit(paragraph, target_size))
    return units


def _explode_text_unit(text, target_size):
    text = text.strip()
    if not text:
        return []
    if len(text) <= max(target_size, 1):
        return [text]

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        result = []
        for line in lines:
            result.extend(_explode_text_unit(line, target_size))
        return result

    sentences = [part.strip() for part in re.split(r"(?<=[.!?…])\s+", text) if part.strip()]
    if len(sentences) > 1:
        result = []
        for sentence in sentences:
            result.extend(_explode_text_unit(sentence, target_size))
        return result

    words = text.split()
    if len(words) <= 1:
        hard_parts = []
        chunk = max(target_size, 1)
        for index in range(0, len(text), chunk):
            hard_parts.append(text[index:index + chunk].strip())
        return [part for part in hard_parts if part]

    chunks = []
    current = []
    current_len = 0
    for word in words:
        add_len = len(word) + (1 if current else 0)
        if current and current_len + add_len > target_size:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += add_len
    if current:
        chunks.append(" ".join(current))
    return chunks


def assemble_parts_from_units(units, target_size, min_part_size, joiner):
    if not units:
        return []

    parts = []
    current = []

    def joined_length(items):
        if not items:
            return 0
        return len(joiner.join(items))

    for index, unit in enumerate(units):
        candidate = current + [unit]
        remaining_units = units[index + 1:]
        remaining_length = joined_length(remaining_units)

        if (
            current
            and joined_length(candidate) > target_size
            and joined_length(current) >= min_part_size
            and (remaining_length == 0 or remaining_length >= min_part_size)
        ):
            parts.append(current)
            current = [unit]
        else:
            current = candidate

    if current:
        parts.append(current)

    if len(parts) > 1 and joined_length(parts[-1]) < min_part_size:
        parts[-2].extend(parts[-1])
        parts.pop()

    return parts


def parse_rulate_markdown(content):
    matches = list(RULATE_HEADER_RE.finditer(content))
    if not matches:
        raise ValueError("Не удалось найти главы в формате Rulate Markdown.")

    chapters = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        chapters.append(
            {
                "title": match.group(1).strip(),
                "payment": match.group(2).strip(),
                "volume": match.group(3).strip(),
                "body": content[body_start:body_end].lstrip("\n").rstrip(),
            }
        )
    return chapters


def render_rulate_markdown(chapters):
    rendered = []
    for chapter in chapters:
        header = f" # [{chapter['title']} :|: :|: {chapter['payment']} :|: {chapter['volume']}]"
        body = chapter["body"].strip()
        rendered.append(f"{header}\n{body}\n")
    return "\n".join(rendered).strip() + "\n"


def split_rulate_markdown(content, settings):
    chapters = parse_rulate_markdown(content)
    output = []
    stats = SplitStats()

    for chapter in chapters:
        body = chapter["body"].strip()
        if len(body) < settings.split_threshold:
            output.append(chapter)
            stats.unchanged_chapters += 1
            continue

        units = split_plain_text_units(body, settings.target_size)
        parts = assemble_parts_from_units(
            units,
            settings.target_size,
            settings.min_part_size,
            "\n\n",
        )

        if len(parts) <= 1:
            output.append(chapter)
            stats.unchanged_chapters += 1
            continue

        stats.split_chapters += 1
        for part_index, part_units in enumerate(parts, start=1):
            output.append(
                {
                    "title": append_part_suffix(chapter["title"], part_index),
                    "payment": chapter["payment"],
                    "volume": chapter["volume"],
                    "body": "\n\n".join(part_units).strip(),
                }
            )

    stats.output_chapters = len(output)
    return render_rulate_markdown(output), stats


def significant_children(node):
    result = []
    for child in node.contents:
        if isinstance(child, Tag):
            result.append(child)
        elif isinstance(child, NavigableString) and child.strip():
            result.append(child)
    return result


def choose_split_root(body):
    current = body
    while True:
        children = significant_children(current)
        tag_children = [child for child in children if isinstance(child, Tag)]
        text_children = [child for child in children if isinstance(child, NavigableString) and child.strip()]

        if len(tag_children) == 1 and not text_children:
            nested = significant_children(tag_children[0])
            if len(nested) >= 2:
                return tag_children[0]
            current = tag_children[0]
            continue

        return current


def extract_title_from_html(soup):
    for tag_name in ("h1", "h2", "h3", "title"):
        tag = soup.find(tag_name)
        if tag and tag.get_text(" ", strip=True):
            return tag.get_text(" ", strip=True)
    return "Глава"


def build_html_units(split_root, target_size):
    children = significant_children(split_root)
    if children and isinstance(children[0], Tag) and children[0].name in {"h1", "h2", "h3"}:
        children = children[1:]

    units = []
    for child in children:
        child_html = str(child)
        child_text = BeautifulSoup(child_html, "html.parser").get_text(" ", strip=True)
        if len(child_text) > max(target_size * 2, target_size + 1000):
            tag_name = child.name if isinstance(child, Tag) and child.name in BLOCK_TAGS else "p"
            for text_chunk in _explode_text_unit(child_text, target_size):
                units.append(f"<{tag_name}>{escape(text_chunk)}</{tag_name}>")
        else:
            units.append(child_html)
    return units


def make_unique_internal_path(existing_paths, original_internal_path, part_index):
    base_dir = posixpath.dirname(original_internal_path)
    filename = posixpath.basename(original_internal_path)
    stem, ext = posixpath.splitext(filename)

    candidate = f"{stem}_part{part_index}{ext}"
    counter = part_index
    while normalize_posix_path(posixpath.join(base_dir, candidate)) in existing_paths:
        counter += 1
        candidate = f"{stem}_part{counter}{ext}"

    internal_path = normalize_posix_path(posixpath.join(base_dir, candidate))
    existing_paths.add(internal_path)
    return internal_path


def resolve_relative_reference(base_internal_path, href):
    raw_href = href.split("#", 1)[0]
    if not raw_href:
        return normalize_posix_path(base_internal_path)
    base_dir = posixpath.dirname(base_internal_path)
    return normalize_posix_path(posixpath.join(base_dir, raw_href))


def build_relative_reference(base_internal_path, target_internal_path):
    base_dir = posixpath.dirname(base_internal_path)
    return normalize_posix_path(posixpath.relpath(target_internal_path, base_dir or "."))


def split_epub_html_document(html_text, settings, used_paths, original_internal_path):
    xml_decl_match = re.match(r"\s*(<\?xml[^>]*\?>)?", html_text, flags=re.IGNORECASE)
    xml_decl = xml_decl_match.group(1) if xml_decl_match else ""
    doctype_match = re.search(r"(<!DOCTYPE[^>]*>)", html_text, flags=re.IGNORECASE)
    doctype = doctype_match.group(1) if doctype_match else ""

    soup = BeautifulSoup(html_text, "html.parser")
    body = soup.find("body")
    if not body:
        return None

    original_title = extract_title_from_html(soup)
    full_text_length = len(body.get_text(" ", strip=True))
    if full_text_length < settings.split_threshold:
        return None

    split_root = choose_split_root(body)
    split_root["data-codex-split-root"] = "1"
    heading_tag_name = "h1"
    first_heading = split_root.find(["h1", "h2", "h3"], recursive=False)
    if first_heading:
        heading_tag_name = first_heading.name

    units = build_html_units(split_root, settings.target_size)
    parts = assemble_parts_from_units(units, settings.target_size, settings.min_part_size, "\n\n")
    if len(parts) <= 1:
        del split_root["data-codex-split-root"]
        return None

    documents = []
    for part_index, part_units in enumerate(parts, start=1):
        part_title = append_part_suffix(original_title, part_index)
        clone = copy.deepcopy(soup)
        clone_root = clone.find(attrs={"data-codex-split-root": "1"})
        if not clone_root:
            clone_root = clone.find("body")
        elif clone_root.has_attr("data-codex-split-root"):
            del clone_root["data-codex-split-root"]

        clone_root.clear()
        heading_tag = clone.new_tag(heading_tag_name)
        heading_tag.string = part_title
        clone_root.append(heading_tag)

        fragment_soup = BeautifulSoup("\n".join(part_units), "html.parser")
        for child in list(fragment_soup.contents):
            clone_root.append(child)

        title_tag = clone.find("title")
        if title_tag:
            title_tag.string = part_title

        serialized = str(clone)
        prefix = ""
        if xml_decl:
            prefix += xml_decl + "\n"
        if doctype:
            prefix += doctype + "\n"

        if part_index == 1:
            internal_path = normalize_posix_path(original_internal_path)
        else:
            internal_path = make_unique_internal_path(used_paths, original_internal_path, part_index)

        documents.append(
            {
                "internal_path": internal_path,
                "title": part_title,
                "content": (prefix + serialized).encode("utf-8"),
            }
        )

    del split_root["data-codex-split-root"]
    return documents


def _get_package_namespace(root):
    if root.tag.startswith("{"):
        return root.tag.split("}", 1)[0][1:]
    return "http://www.idpf.org/2007/opf"


def _find_opf_path(epub_zip):
    try:
        container_root = ET.fromstring(epub_zip.read("META-INF/container.xml"))
        for elem in container_root.iter():
            if elem.tag.endswith("rootfile"):
                return elem.attrib.get("full-path")
    except Exception:
        pass

    for name in epub_zip.namelist():
        if name.lower().endswith(".opf"):
            return name
    raise FileNotFoundError("Не удалось найти content.opf внутри EPUB.")


def split_epub_file(input_path, output_path, settings, log_callback=None, progress_callback=None):
    def log(message):
        if log_callback:
            log_callback(message)

    stats = SplitStats()

    with zipfile.ZipFile(input_path, "r") as zin:
        opf_path = _find_opf_path(zin)
        opf_dir = posixpath.dirname(opf_path)
        opf_root = ET.fromstring(zin.read(opf_path))
        ns_uri = _get_package_namespace(opf_root)
        ns = {"opf": ns_uri}

        manifest = opf_root.find("opf:manifest", ns)
        spine = opf_root.find("opf:spine", ns)
        if manifest is None or spine is None:
            raise ValueError("В EPUB не найден manifest или spine.")

        manifest_map = {}
        item_id_order = []
        for item in manifest.findall("opf:item", ns):
            item_id = item.attrib.get("id")
            href = item.attrib.get("href")
            if not item_id or not href:
                continue
            internal_path = normalize_posix_path(posixpath.join(opf_dir, href))
            manifest_map[item_id] = {
                "element": item,
                "href": href,
                "internal_path": internal_path,
                "media_type": item.attrib.get("media-type", "application/xhtml+xml"),
                "properties": item.attrib.get("properties"),
            }
            item_id_order.append(item_id)

        used_paths = {normalize_posix_path(name) for name in zin.namelist()}
        split_map = {}
        new_files = {}
        updated_files = {}
        extra_manifest_ids = {}

        spine_refs = spine.findall("opf:itemref", ns)
        total_refs = max(len(spine_refs), 1)

        for index, itemref in enumerate(spine_refs, start=1):
            idref = itemref.attrib.get("idref")
            manifest_info = manifest_map.get(idref)
            if not manifest_info:
                continue

            internal_path = manifest_info["internal_path"]
            media_type = manifest_info["media_type"]
            if "html" not in media_type and "xhtml" not in media_type:
                continue

            html_text = zin.read(internal_path).decode("utf-8", errors="ignore")
            documents = split_epub_html_document(html_text, settings, used_paths, internal_path)
            if not documents:
                stats.unchanged_chapters += 1
                if progress_callback:
                    progress_callback(int(index / total_refs * 100))
                continue

            stats.split_chapters += 1
            stats.output_chapters += len(documents)
            updated_files[internal_path] = documents[0]["content"]
            split_map[internal_path] = documents

            log(
                f"✂ {posixpath.basename(internal_path)} -> {len(documents)} частей"
            )

            generated_ids = []
            for part_index, document in enumerate(documents[1:], start=2):
                new_files[document["internal_path"]] = document["content"]
                new_id = f"{idref}_part{part_index}"
                generated_ids.append(new_id)

                new_item = ET.Element(f"{{{ns_uri}}}item")
                new_item.set("id", new_id)
                new_item.set(
                    "href",
                    build_relative_reference(opf_path, document["internal_path"]),
                )
                new_item.set("media-type", media_type)
                if manifest_info["properties"]:
                    new_item.set("properties", manifest_info["properties"])
                manifest.append(new_item)

            extra_manifest_ids[idref] = generated_ids

            if progress_callback:
                progress_callback(int(index / total_refs * 100))

        if not split_map:
            raise ValueError("Подходящих для разбиения глав не найдено.")

        updated_spine_children = []
        spine_attributes = dict(spine.attrib)
        for itemref in spine_refs:
            updated_spine_children.append(itemref)
            idref = itemref.attrib.get("idref")
            for new_id in extra_manifest_ids.get(idref, []):
                new_itemref = ET.Element(f"{{{ns_uri}}}itemref")
                new_itemref.set("idref", new_id)
                if itemref.attrib.get("linear"):
                    new_itemref.set("linear", itemref.attrib["linear"])
                updated_spine_children.append(new_itemref)

        for child in list(spine):
            spine.remove(child)
        spine.attrib.clear()
        spine.attrib.update(spine_attributes)
        for child in updated_spine_children:
            spine.append(child)

        for ncx_item in manifest.findall("opf:item", ns):
            media_type = ncx_item.attrib.get("media-type", "")
            properties = ncx_item.attrib.get("properties", "")
            href = ncx_item.attrib.get("href", "")
            internal_path = normalize_posix_path(posixpath.join(opf_dir, href))

            if media_type == "application/x-dtbncx+xml":
                soup = BeautifulSoup(zin.read(internal_path).decode("utf-8", errors="ignore"), "xml")
                changed = False
                for nav_point in soup.find_all("navPoint"):
                    content_tag = nav_point.find("content", src=True)
                    if not content_tag:
                        continue
                    target_internal = resolve_relative_reference(internal_path, content_tag["src"])
                    documents = split_map.get(target_internal)
                    if not documents:
                        continue

                    first_text = nav_point.find("text")
                    if first_text:
                        first_text.string = documents[0]["title"]
                    content_tag["src"] = build_relative_reference(internal_path, documents[0]["internal_path"])
                    changed = True

                    insertion_point = nav_point
                    for part_index, document in enumerate(documents[1:], start=2):
                        new_nav_point = copy.deepcopy(nav_point)
                        new_nav_point["id"] = f"{nav_point.get('id', 'navPoint')}_part{part_index}"
                        new_content = new_nav_point.find("content", src=True)
                        new_text = new_nav_point.find("text")
                        if new_content:
                            new_content["src"] = build_relative_reference(
                                internal_path,
                                document["internal_path"],
                            )
                        if new_text:
                            new_text.string = document["title"]
                        insertion_point.insert_after(new_nav_point)
                        insertion_point = new_nav_point

                if changed:
                    for order, nav_point in enumerate(soup.find_all("navPoint"), start=1):
                        nav_point["playOrder"] = str(order)
                    updated_files[internal_path] = str(soup).encode("utf-8")

            if "nav" in properties or posixpath.basename(href).lower() == "nav.xhtml":
                soup = BeautifulSoup(zin.read(internal_path).decode("utf-8", errors="ignore"), "html.parser")
                changed = False
                for anchor in soup.find_all("a", href=True):
                    target_internal = resolve_relative_reference(internal_path, anchor["href"])
                    documents = split_map.get(target_internal)
                    if not documents:
                        continue

                    anchor.string = documents[0]["title"]
                    changed = True
                    li_parent = anchor.find_parent("li")
                    insertion_point = li_parent if li_parent else anchor

                    for document in documents[1:]:
                        new_anchor = soup.new_tag(
                            "a",
                            href=build_relative_reference(internal_path, document["internal_path"]),
                        )
                        new_anchor.string = document["title"]
                        if li_parent:
                            new_li = soup.new_tag("li")
                            new_li.append(new_anchor)
                            insertion_point.insert_after(new_li)
                            insertion_point = new_li
                        else:
                            insertion_point.insert_after(new_anchor)
                            insertion_point = new_anchor

                if changed:
                    updated_files[internal_path] = str(soup).encode("utf-8")

        updated_files[opf_path] = ET.tostring(
            opf_root,
            encoding="utf-8",
            xml_declaration=True,
        )

        with zipfile.ZipFile(output_path, "w") as zout:
            for info in zin.infolist():
                payload = updated_files.get(info.filename)
                if payload is None:
                    payload = zin.read(info.filename)
                zout.writestr(info, payload)

            for internal_path, payload in new_files.items():
                zout.writestr(internal_path, payload)

    stats.output_chapters += stats.unchanged_chapters
    return stats


class ChapterSplitterThread(QThread):
    progress = pyqtSignal(int)
    log_message = pyqtSignal(str)
    finished_processing = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, input_path, output_path, settings):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.settings = settings

    def run(self):
        try:
            suffix = Path(self.input_path).suffix.lower()
            if suffix == ".md":
                self._process_markdown()
            elif suffix == ".epub":
                self._process_epub()
            else:
                raise ValueError("Поддерживаются только файлы .epub и .md")
        except Exception:
            self.error.emit(traceback.format_exc())

    def _process_markdown(self):
        self.log_message.emit("Читаю Rulate Markdown...")
        with open(self.input_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.progress.emit(20)
        output_text, stats = split_rulate_markdown(content, self.settings)
        self.progress.emit(80)

        output_dir = os.path.dirname(self.output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as f:
            f.write(output_text)

        self.progress.emit(100)
        self.finished_processing.emit(
            {
                "format": "md",
                "split_chapters": stats.split_chapters,
                "unchanged_chapters": stats.unchanged_chapters,
                "output_chapters": stats.output_chapters,
                "output_path": self.output_path,
            }
        )

    def _process_epub(self):
        self.log_message.emit("Читаю EPUB и анализирую spine...")
        output_dir = os.path.dirname(self.output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        stats = split_epub_file(
            self.input_path,
            self.output_path,
            self.settings,
            log_callback=self.log_message.emit,
            progress_callback=self.progress.emit,
        )
        self.progress.emit(100)
        self.finished_processing.emit(
            {
                "format": "epub",
                "split_chapters": stats.split_chapters,
                "unchanged_chapters": stats.unchanged_chapters,
                "output_chapters": stats.output_chapters,
                "output_path": self.output_path,
            }
        )


class ChapterSplitterWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chapter Splitter")
        self.setMinimumSize(860, 680)
        self._returning_to_main_menu = False

        from gemini_translator.ui.pages.chapter_splitter_page import ChapterSplitterPage

        self.page = ChapterSplitterPage(self)
        self.setCentralWidget(self.page)

        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        act_menu = QAction("В меню", self)
        act_menu.triggered.connect(self._return_to_menu)
        toolbar.addAction(act_menu)

    @property
    def worker(self):
        return self.page.worker

    def _return_to_menu(self):
        if self.page.worker and self.page.worker.isRunning():
            QMessageBox.warning(self, "Подождите", "Сначала дождитесь завершения обработки.")
            return
        self._returning_to_main_menu = True
        self.close()

    def closeEvent(self, event):
        if self.page.worker and self.page.worker.isRunning():
            QMessageBox.warning(self, "Подождите", "Сначала дождитесь завершения обработки.")
            event.ignore()
            return
        if self._returning_to_main_menu:
            return_to_main_menu()
            event.accept()
            return
        action = prompt_return_to_menu(self)
        if action == "cancel":
            event.ignore()
            return
        if action == "menu":
            return_to_main_menu()
        event.accept()
