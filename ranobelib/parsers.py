import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile

from bs4 import BeautifulSoup
from docx import Document

from models import ChapterData, auto_fill_missing_chapter_numbers
from utils import natural_sort_key, parse_vol_and_chapter

class FileParser:
    """Статические методы для разбора файлов разных форматов."""

    @staticmethod
    def parse_epub(path: str, default_vol: str, log_fn=None) -> list[ChapterData]:
        chapters = []
        try:
            with zipfile.ZipFile(path, "r") as z:
                opf_name = next(
                    (n for n in z.namelist() if n.endswith(".opf")), None
                )
                if not opf_name:
                    raise ValueError("Невалидный EPUB: .opf не найден")

                root = ET.fromstring(z.read(opf_name))
                ns = {"op": "http://www.idpf.org/2007/opf"}
                manifest = {
                    item.get("id"): item.get("href")
                    for item in root.findall(".//op:item", ns)
                }
                spine = [
                    item.get("idref")
                    for item in root.findall(".//op:itemref", ns)
                ]

                opf_dir = os.path.dirname(opf_name)
                cnt = 1
                for item_id in spine:
                    href = manifest.get(item_id)
                    if not href:
                        continue

                    full_path = (
                        os.path.join(opf_dir, href).replace("\\", "/")
                        if opf_dir
                        else href
                    )
                    if full_path not in z.namelist():
                        continue

                    raw_html = z.read(full_path).decode("utf-8", errors="ignore")
                    soup = BeautifulSoup(raw_html, "html.parser")

                    raw_title = ""
                    heading = soup.find(["h1", "h2", "h3"])
                    if heading:
                        raw_title = heading.get_text(strip=True)
                        heading.decompose()

                    text_content = ""
                    for tag in soup.find_all(["p", "div"]):
                        t = tag.get_text(strip=True)
                        if t:
                            text_content += f"<p>{t}</p>"

                    if len(text_content) > 100:
                        vol, c_num, title, _found = parse_vol_and_chapter(
                            raw_title, default_vol, cnt
                        )
                        chapters.append(
                            ChapterData(vol, c_num, title, text_content, _num_found=_found)
                        )
                        cnt += 1

        except Exception as e:
            if log_fn:
                log_fn("ERROR", f"EPUB: {e}")
            raise
        auto_fill_missing_chapter_numbers(chapters)
        return chapters

    @staticmethod
    def parse_zip_docx(zip_path: str, default_vol: str, log_fn=None) -> list[ChapterData]:
        chapters = []
        with zipfile.ZipFile(zip_path, "r") as z:
            file_list = sorted(
                [
                    f
                    for f in z.namelist()
                    if f.endswith(".docx") and not os.path.basename(f).startswith("~")
                ],
                key=natural_sort_key,
            )
            cnt = 1
            all_nums_found = True
            for filename in file_list:
                try:
                    name_no_ext = os.path.splitext(os.path.basename(filename))[0]
                    clean_name = name_no_ext.replace("_", " ")

                    vol, chap_num, title, num_found = parse_vol_and_chapter(
                        clean_name, default_vol, cnt
                    )

                    doc = Document(io.BytesIO(z.read(filename)))
                    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

                    # Удаление рекламных ссылок в конце
                    if paragraphs:
                        last = paragraphs[-1].lower()
                        if any(kw in last for kw in ("rulate", "boosty", "http", "patreon", "t.me")):
                            paragraphs.pop()

                    content = "\n".join(paragraphs)
                    chapters.append(ChapterData(vol, chap_num, title, content,
                                                _parse_index=cnt, _num_found=num_found))
                    all_nums_found = all_nums_found and num_found
                    cnt += 1
                except Exception as e:
                    if log_fn:
                        log_fn("ERROR", f"Файл {filename}: {e}")

        # Сортировка по номеру главы (а не по имени файла),
        # т.к. имена файлов с Rulate могут иметь разный формат
        # и при алфавитной сортировке идут вперемешку.
        # Но сортируем ТОЛЬКО если у всех глав номер был реально найден
        # в имени файла. Если у какой-то главы номер не найден
        # (например, "Пролог", "�?нтерлюдия"), сохраняем исходный
        # порядок файлов, чтобы не сломать последовательность.
        auto_fill_missing_chapter_numbers(chapters)
        if all_nums_found and chapters:
            chapters.sort(key=lambda c: (c.volume, c.number, c._parse_index))
        return chapters

    @staticmethod
    def parse_txt(file_path: str, default_vol: str) -> list[ChapterData]:
        """
        Формат TXT/MD:
        # [Глава 1: Название]
        текст текст текст
        # [Глава 2]
        текст
        """
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

        chunks = re.split(r"^\s*#\s*\[(.*?)\]", text, flags=re.MULTILINE)
        chapters = []
        cnt = 1
        for i in range(1, len(chunks), 2):
            marker = chunks[i].strip()
            content = chunks[i + 1].strip() if (i + 1) < len(chunks) else ""

            vol, c_num, title, _found = parse_vol_and_chapter(marker, default_vol, cnt)
            if not title and not re.fullmatch(r"\d+(?:\.\d+)?", marker):
                title = marker

            chapters.append(ChapterData(vol, c_num, title, content, _num_found=_found))
            cnt += 1
        auto_fill_missing_chapter_numbers(chapters)
        return chapters

    @staticmethod
    def parse_html(file_path: str, default_vol: str) -> list[ChapterData]:
        """
        Один HTML-файл, где каждая глава начинается с <h1> / <h2>.
        Если заголовков нет — весь файл = одна глава.
        """
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()

        soup = BeautifulSoup(html, "html.parser")
        headings = soup.find_all(["h1", "h2"])
        chapters = []

        if not headings:
            # Весь файл — одна глава
            body_text = ""
            for tag in soup.find_all(["p", "div"]):
                t = tag.get_text(strip=True)
                if t:
                    body_text += f"<p>{t}</p>"
            if body_text:
                chapters.append(ChapterData(default_vol, 1, "", body_text))
            return chapters

        cnt = 1
        for idx, heading in enumerate(headings):
            raw_title = heading.get_text(strip=True)

            # Собираем контент до следующего заголовка
            content_parts = []
            for sibling in heading.find_next_siblings():
                if sibling.name in ("h1", "h2"):
                    break
                t = sibling.get_text(strip=True)
                if t:
                    content_parts.append(f"<p>{t}</p>")

            text_content = "".join(content_parts)
            if len(text_content) > 50:
                vol, c_num, title, _found = parse_vol_and_chapter(
                    raw_title, default_vol, cnt
                )
                chapters.append(
                    ChapterData(vol, c_num, title, text_content, _num_found=_found)
                )
                cnt += 1

        auto_fill_missing_chapter_numbers(chapters)
        return chapters


