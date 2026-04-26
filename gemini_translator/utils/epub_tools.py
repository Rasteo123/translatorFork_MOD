# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# Инструменты для работы с EPUB
# ---------------------------------------------------------------------------
# Этот файл содержит классы и функции для создания и анализа EPUB-файлов.
# - EpubCreator: класс для сборки EPUB-файла из HTML-глав.
# - extract_number_from_path: функция для извлечения номера главы из имени файла.
# ---------------------------------------------------------------------------

import os
import re
import uuid
import mimetypes
import zipfile
import html as html_lib
from xml.etree import ElementTree as ET
from ..api import config as api_config

class EpubUpdater:
    """
    Умный обновлятор EPUB. Заменяет файлы глав и интеллектуально обновляет
    все ссылки на них в OPF, NCX, NAV и других HTML/XHTML файлах,
    корректно обрабатывая относительные пути.
    Также умеет обновлять заголовки глав в TOC на основе содержимого <h1>.
    """
    def __init__(self, original_epub_path):
        if not os.path.exists(original_epub_path):
            raise FileNotFoundError(f"Исходный EPUB файл не найден: {original_epub_path}")
        self.original_epub_path = original_epub_path
        self.replacements = {} # {internal_path: disk_path}

    def add_replacement(self, internal_path, new_file_disk_path):
        internal_path_zip_format = internal_path.replace('\\', '/')
        self.replacements[internal_path_zip_format] = new_file_disk_path

    def update_and_save(self, output_path):
        # --- Шаг 1: Подготовка карт замен ---
        filename_replacement_map = {}
        new_files_to_add = {}
        
        # Словарь {original_filename: new_title_from_h1}
        # Будет заполнен при чтении новых файлов
        titles_update_map = {} 

        # BS4 нужен для надежного парсинга заголовков
        try:
            from bs4 import BeautifulSoup
            HAS_BS4 = True
        except ImportError:
            HAS_BS4 = False

        for original_internal_path, new_file_disk_path in self.replacements.items():
            old_filename = os.path.basename(original_internal_path)
            new_filename = os.path.basename(new_file_disk_path)
            
            filename_replacement_map[old_filename] = new_filename
            new_internal_path = os.path.join(os.path.dirname(original_internal_path), new_filename)
            new_files_to_add[new_internal_path] = new_file_disk_path

            # --- Extract Title Logic ---
            if HAS_BS4:
                try:
                    with open(new_file_disk_path, 'r', encoding='utf-8') as f:
                        raw_html = f.read()
                        soup = BeautifulSoup(raw_html, 'html.parser')
                        
                        # 1. Ищем H1 (или H2, если нет H1)
                        header = soup.find('h1')
                        if not header:
                            header = soup.find('h2')
                        
                        if header:
                            new_title_text = header.get_text().strip()
                            if new_title_text:
                                # Сохраняем для обновления TOC.
                                # Ключом делаем старое имя файла, так как TOC ссылается на него (пока мы не обновили ссылки)
                                # Но стоп, ссылки мы обновляем глобально.
                                # В NCX src="chapter1.xhtml". Мы заменим это на src="new_chapter.xhtml".
                                # Поэтому ключом для TOC будет НОВОЕ имя файла.
                                titles_update_map[new_filename] = new_title_text
                except Exception as e:
                    print(f"Warning: could not extract title from {new_filename}: {e}")

        # --- Шаг 2: Чтение и модификация файлов в памяти ---
        modified_files = {}
        
        with zipfile.ZipFile(open(self.original_epub_path, 'rb'), 'r') as original_zip:
            
            # Список всех файлов для поиска TOC
            all_files = original_zip.namelist()
            
            for item in original_zip.infolist():
                if item.is_dir(): continue
                
                # Читаем содержимое
                content_bytes = original_zip.read(item.filename)
                
                # Попытка декодировать как текст
                try:
                    content_str = content_bytes.decode('utf-8')
                    is_modified = False
                    
                    # 2.1 Глобальная замена имен файлов (для ссылок)
                    for old_name, new_name in filename_replacement_map.items():
                        if old_name in content_str:
                            content_str = content_str.replace(old_name, new_name)
                            is_modified = True
                    
                    # 2.2 Обновление TOC (NCX) и NAV (XHTML)
                    # Если этот файл похож на TOC и у нас есть новые заголовки
                    if titles_update_map:
                        basename_lower = os.path.basename(item.filename).lower()
                        
                        # Обработка NCX (navPoint -> navLabel -> text)
                        if basename_lower.endswith('.ncx'):
                            # Простая регулярка безопаснее полного XML парсинга для broken ncx
                            # Ищем <content src="filename.xhtml" ... /> и ближайший <text>Title</text>
                            # Но структура NCX: <navPoint><navLabel><text>...</text></navLabel><content src="..."/></navPoint>
                            # Регуляркой сложно. Попробуем простой replace, если имя файла уникально? Нет.
                            # Используем BS4 для надежности, если есть.
                            if HAS_BS4:
                                soup = BeautifulSoup(content_str, 'xml') # XML parser!
                                changed_ncx = False
                                for new_fname, new_title in titles_update_map.items():
                                    # Ищем content src="...new_fname..."
                                    # Т.к. мы уже сделали replace имен файлов в 2.1, в soup уже новые имена!
                                    content_tag = soup.find('content', src=lambda x: x and new_fname in x)
                                    if content_tag:
                                        nav_point = content_tag.find_parent('navPoint')
                                        if nav_point:
                                            text_tag = nav_point.find('text')
                                            if text_tag:
                                                text_tag.string = new_title
                                                changed_ncx = True
                                if changed_ncx:
                                    content_str = str(soup)
                                    is_modified = True

                        # Обработка NAV (EPUB3) - обычно nav.xhtml
                        elif 'nav' in basename_lower or 'toc' in basename_lower:
                            if HAS_BS4 and '<nav' in content_str:
                                soup = BeautifulSoup(content_str, 'html.parser')
                                changed_nav = False
                                for new_fname, new_title in titles_update_map.items():
                                    # Ищем ссылку <a href="...new_fname...">Old Title</a>
                                    a_tag = soup.find('a', href=lambda x: x and new_fname in x)
                                    if a_tag:
                                        a_tag.string = new_title
                                        changed_nav = True
                                if changed_nav:
                                    content_str = str(soup)
                                    is_modified = True

                    if is_modified:
                        modified_files[item.filename] = content_str.encode('utf-8')
                        
                except (UnicodeDecodeError, TypeError):
                    pass # Бинарный файл

        # --- Шаг 3: Обработка новых файлов (update <title>) ---
        # Мы должны прочитать файлы с диска, обновить в них <title> и положить в zip
        final_new_files = {}
        if HAS_BS4 and titles_update_map:
            for internal_path, disk_path in new_files_to_add.items():
                fname = os.path.basename(internal_path)
                with open(disk_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Если для этого файла есть новый заголовок
                if fname in titles_update_map:
                    new_title = titles_update_map[fname]
                    soup = BeautifulSoup(content, 'html.parser')
                    if soup.title:
                        soup.title.string = new_title
                    else:
                        # Создаем title, если нет
                        if soup.head:
                            new_tag = soup.new_tag("title")
                            new_tag.string = new_title
                            soup.head.append(new_tag)
                    content = str(soup)
                
                final_new_files[internal_path] = content.encode('utf-8')
        else:
            # Если нет BS4, просто читаем байты
            for internal_path, disk_path in new_files_to_add.items():
                with open(disk_path, 'rb') as f:
                    final_new_files[internal_path] = f.read()

        # --- Шаг 4: Сборка ---
        with zipfile.ZipFile(open(self.original_epub_path, 'rb'), 'r') as original_zip:
            with zipfile.ZipFile(open(output_path, 'wb'), 'w', zipfile.ZIP_DEFLATED) as new_zip:
                for item in original_zip.infolist():
                    # Пропускаем файлы, которые мы заменяем полностью
                    if item.filename in self.replacements: # Тут старые пути
                        continue
                        
                    # Пишем модифицированные (toc, opf и т.д.)
                    if item.filename in modified_files:
                        new_zip.writestr(item.filename, modified_files[item.filename])
                    else:
                        new_zip.writestr(item, original_zip.read(item.filename))
                
                # Пишем новые файлы (с обновленными title)
                for internal_path, content_bytes in final_new_files.items():
                    new_zip.writestr(internal_path, content_bytes)

        print(f"Обновленный EPUB сохранен в: {output_path}")


class EpubCreator:
    """Создает EPUB файл версии 2 из HTML глав."""
    def __init__(self, title, author="Unknown", language="ru"):
        self.title = title
        self.author = author
        self.language = language
        self.chapters = []
        self.uuid = str(uuid.uuid4())
        self.cover_image_path = None
        self.cover_mime = None

    def set_cover(self, file_path):
        """Устанавливает изображение обложки."""
        if os.path.exists(file_path):
            self.cover_image_path = file_path
            # Определяем mime type
            mime, _ = mimetypes.guess_type(file_path)
            self.cover_mime = mime or 'image/jpeg'

    def add_chapter(self, filename, content, title):
        """Добавляет главу в книгу."""
        self.chapters.append({
            'filename': filename,
            'content': content,
            'title': title,
            'id': f'chapter{len(self.chapters) + 1}'
        })

    def create_epub(self, output_path):
        """Создает EPUB файл."""
        with zipfile.ZipFile(open(output_path, 'wb'), 'w', zipfile.ZIP_DEFLATED) as epub:
            epub.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            epub.writestr('META-INF/container.xml', self._create_container())
            epub.writestr('OEBPS/content.opf', self._create_opf())
            epub.writestr('OEBPS/toc.ncx', self._create_ncx())
            epub.writestr('OEBPS/styles.css', self._create_styles())

            # Запись глав
            for chapter in self.chapters:
                epub.writestr(f'OEBPS/{chapter["filename"]}', chapter['content'])
            
            # Запись обложки
            if self.cover_image_path:
                ext = os.path.splitext(self.cover_image_path)[1]
                cover_filename = f"cover{ext}"
                with open(self.cover_image_path, 'rb') as f:
                    epub.writestr(f'OEBPS/{cover_filename}', f.read())

    def _xml_escape(self, value):
        return html_lib.escape(str(value or ""), quote=True)

    def _create_container(self):
        return '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>'''

    def _create_opf(self):
        title = self._xml_escape(self.title)
        author = self._xml_escape(self.author)
        language = self._xml_escape(self.language)
        opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookID" version="2.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
        <dc:title>{title}</dc:title>
        <dc:creator>{author}</dc:creator>
        <dc:language>{language}</dc:language>
        <dc:identifier id="BookID">urn:uuid:{self.uuid}</dc:identifier>'''
        
        if self.cover_image_path:
            opf += '\n        <meta name="cover" content="cover-image"/>'
            
        opf += '''
    </metadata>
    <manifest>
        <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
        <item id="styles" href="styles.css" media-type="text/css"/>'''
        
        # Add cover to manifest
        if self.cover_image_path:
            ext = os.path.splitext(self.cover_image_path)[1]
            cover_filename = f"cover{ext}"
            opf += f'\n        <item id="cover-image" href="{cover_filename}" media-type="{self.cover_mime}"/>'

        for chapter in self.chapters:
            opf += f'\n        <item id="{chapter["id"]}" href="{chapter["filename"]}" media-type="application/xhtml+xml"/>'
        
        opf += '\n    </manifest>\n    <spine toc="ncx">'
        for chapter in self.chapters:
            opf += f'\n        <itemref idref="{chapter["id"]}"/>'
        opf += '\n    </spine>\n</package>'
        return opf

    def _create_ncx(self):
        # (Без изменений, но нужно включить в общий класс)
        title = self._xml_escape(self.title)
        ncx = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
    <head>
        <meta name="dtb:uid" content="urn:uuid:{self.uuid}"/>
        <meta name="dtb:depth" content="1"/>
        <meta name="dtb:totalPageCount" content="0"/>
        <meta name="dtb:maxPageNumber" content="0"/>
    </head>
    <docTitle><text>{title}</text></docTitle>
    <navMap>'''
        for i, chapter in enumerate(self.chapters):
            chapter_title = self._xml_escape(chapter["title"])
            ncx += f'''
        <navPoint id="navPoint-{i+1}" playOrder="{i+1}">
            <navLabel><text>{chapter_title}</text></navLabel>
            <content src="{chapter["filename"]}"/>
        </navPoint>'''
        ncx += '\n    </navMap>\n</ncx>'
        return ncx
        
    def _create_styles(self):
        return '''body { font-family: Georgia, serif; } p { text-indent: 1.5em; margin: 0; }'''


def _get_path_from_item(item):
    """Вспомогательная функция для извлечения пути из разных типов объектов."""
    if isinstance(item, str):
        return item
    if hasattr(item, 'internal_path'): # Для SortableChapterItem
        return item.internal_path
    if hasattr(item, 'data'): # Для QListWidgetItem
        user_data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(user_data, str):
            return user_data
    return ""
    
def extract_number_from_path(item):
    """
    Извлекает ПЕРВОЕ число из имени файла для естественной числовой сортировки.
    Универсальная версия: работает со строками и объектами Qt.
    """
    path = _get_path_from_item(item)
    filename = os.path.basename(path)
    match = re.search(r"(\d+)", filename)
    if match:
        try:
            return int(match.group(1))
        except (ValueError, IndexError):
            return float("inf")
    return float("inf")

def extract_number_from_path_reversed(item):
    """
    Извлекает ПОСЛЕДНЕЕ число из имени файла для обратной сортировки.
    Универсальная версия: работает со строками и объектами Qt.
    """
    path = _get_path_from_item(item)
    filename = os.path.basename(path)
    matches = re.findall(r"(\d+)", filename)
    if matches:
        try:
            return int(matches[-1])
        except (ValueError, IndexError):
            return float("inf")
    return float("inf")
    
    
def get_epub_chapter_order(epub_path, return_method=False):
    """
    Анализирует EPUB и возвращает канонический порядок глав и метод сортировки.
    Версия 2.0: Игнорирует служебные навигационные файлы (nav.xhtml).
    """
    try:
        with zipfile.ZipFile(epub_path, 'r') as epub_zip:
            all_html_files = set()
            for name in epub_zip.namelist():
                lower_name = name.lower()
                if lower_name.endswith(('.html', '.xhtml', '.htm')) and not name.startswith('__MACOSX'):
                    # --- НАЧАЛО НОВОЙ ЛОГИКИ ФИЛЬТРАЦИИ ---
                    
                    # 1. Простая проверка по имени
                    basename = os.path.basename(lower_name)
                    if basename in ('nav.xhtml', 'toc.html', 'cover.xhtml'):
                        continue

                    # 2. Проверка по содержимому (более надежная)
                    try:
                        # Читаем только небольшой фрагмент файла для скорости
                        content_sample = epub_zip.read(name)[:1024].decode('utf-8', errors='ignore')
                        if '<nav' in content_sample and 'epub:type="toc"' in content_sample:
                            continue
                    except Exception:
                        # Если не удалось прочитать, на всякий случай пропускаем
                        pass
                    
                    # Если все проверки пройдены, добавляем файл
                    all_html_files.add(name)
                    # --- КОНЕЦ НОВОЙ ЛОГИКИ ФИЛЬТРАЦИИ ---

            spine_order = _get_spine_order_from_zip(epub_zip)
            
            if spine_order:
                # Фильтруем spine_order, чтобы убедиться, что в него не попали служебные файлы, которые мы отсеяли
                filtered_spine = [p for p in spine_order if p in all_html_files]
                
                ordered_chapters = filtered_spine
                unspined_chapters = all_html_files - set(ordered_chapters)
                if unspined_chapters:
                    ordered_chapters.extend(sorted(list(unspined_chapters), key=extract_number_from_path))
                
                return (ordered_chapters, 'spine') if return_method else ordered_chapters
            else:
                sorted_by_name = sorted(list(all_html_files), key=extract_number_from_path)
                return (sorted_by_name, 'filename') if return_method else sorted_by_name
    except Exception as e:
        print(f"[ERROR] Критическая ошибка при чтении порядка глав из {epub_path}: {e}")
        return ([], 'error') if return_method else []

def _get_spine_order_from_zip(epub_zip_file):
    """Внутренняя функция для извлечения порядка из открытого zip-файла."""
    try:
        opf_path = None
        opf_files = [f for f in epub_zip_file.namelist() if f.lower().endswith('.opf')]
        
        if len(opf_files) == 1:
            opf_path = opf_files[0]
        elif len(opf_files) > 1:
            container_content = epub_zip_file.read('META-INF/container.xml')
            root = ET.fromstring(container_content)
            ns = {'cn': 'urn:oasis:names:tc:opendocument:xmlns:container'}
            opf_path = root.find('.//cn:rootfile', ns).attrib['full-path']
        
        if not opf_path:
            raise FileNotFoundError("OPF файл не найден.")

        opf_dir = os.path.dirname(opf_path)
        opf_content = epub_zip_file.read(opf_path)
        opf_root = ET.fromstring(opf_content)
        opf_ns = {'opf': 'http://www.idpf.org/2007/opf'}

        manifest_items = {}
        for item in opf_root.findall('.//opf:manifest/opf:item', opf_ns):
            item_id = item.attrib.get('id')
            href = item.attrib.get('href')
            if item_id and href:
                full_href = os.path.join(opf_dir, href)
                manifest_items[item_id] = full_href

        spine_order = []
        for itemref in opf_root.findall('.//opf:spine/opf:itemref', opf_ns):
            idref = itemref.attrib.get('idref')
            if idref in manifest_items:
                spine_order.append(manifest_items[idref])
        
        return spine_order
    except (KeyError, ET.ParseError, FileNotFoundError, AttributeError) as e:
        print(f"[WARN] Не удалось прочитать spine из EPUB: {e}.")
        return None
    
    
    
    
    
    
def calculate_potential_output_size(html_content, is_cjk):
    """
    Вычисляет потенциальный размер ответа модели на основе содержимого HTML.
    Возвращает кортеж: (общий_потенциальный_размер, размер_тегов).
    """
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        visible_text = soup.get_text()
        
        len_html = len(html_content)
        len_text = len(visible_text)
        len_tags = len_html - len_text
        
        # --- ИЗМЕНЕНИЕ: Используем новые, "чистые" лингвистические константы ---
        multiplier = (
            api_config.CJK_EXPANSION_FACTOR 
            if is_cjk else 
            api_config.ALPHABETIC_EXPANSION_FACTOR
        )
        potential_text_size = len_text * multiplier
        
        total_potential_size = len_tags + potential_text_size
        
        return total_potential_size, len_tags
    except Exception:
        # В случае ошибки возвращаем простой размер и предполагаем, что тегов нет
        return len(html_content) * 2, 0
        
 

def get_epub_chapter_sizes_with_cache(project_manager, epub_path, return_cache_status=False):
    """
    Получает ОБЩИЙ размер глав в символах, используя "ДНК" файла для кэширования.
    Возвращает словарь {internal_path: total_chars}.
    
    Улучшения:
    1. Sanity Check: проверяет реальное количество символов в 3 точках (начало, середина, конец),
       чтобы убедиться, что кэш не врет.
    """
    if not project_manager or not epub_path or not os.path.exists(epub_path):
        return ({}, False) if return_cache_status else {}

    try:
        epub_stat = os.stat(epub_path)
        current_epub_name = os.path.basename(epub_path)
        current_epub_size = epub_stat.st_size
        
        # Получаем список файлов внутри
        with zipfile.ZipFile(open(epub_path, 'rb'), 'r') as zf:
            # Собираем список файлов и их сжатых размеров для контрольной суммы
            chapter_info_list = [
                (info.filename, info.file_size)
                for info in zf.infolist() 
                if info.filename.lower().endswith(('.html', '.xhtml', '.htm'))
            ]
        
        # Считаем чексумму по размерам файлов внутри архива
        current_content_checksum = sum(size for _, size in chapter_info_list)

    except (zipfile.BadZipFile, FileNotFoundError) as e:
        print(f"[ERROR] Не удалось прочитать EPUB для создания отпечатка: {e}")
        return ({}, False) if return_cache_status else {}

    cache_data = project_manager.load_size_cache()
    is_cache_valid = False
    final_sizes = {}

    # --- ЭТАП 1: Проверка метаданных ---
    if cache_data and isinstance(cache_data, dict):
        metadata = cache_data.get('metadata', {})
        if (metadata.get('epub_name') == current_epub_name and
            metadata.get('epub_size') == current_epub_size and
            metadata.get('content_checksum') == current_content_checksum):
            
            cached_sizes = cache_data.get('sizes', {})
            
            # --- ЭТАП 2: SANITY CHECK (Выборочная проверка контента) ---
            # Даже если метаданные совпали, проверим 3 случайных файла (начало, середина, конец),
            # чтобы убедиться, что символы считаются так же.
            try:
                sorted_keys = sorted(cached_sizes.keys())
                if sorted_keys:
                    # Выбираем индексы для проверки: 0, середина, последний
                    indices_to_check = {0, len(sorted_keys) // 2, len(sorted_keys) - 1}
                    
                    with zipfile.ZipFile(open(epub_path, 'rb'), 'r') as zf:
                        all_samples_match = True
                        for idx in indices_to_check:
                            if idx < 0 or idx >= len(sorted_keys): continue
                            
                            check_path = sorted_keys[idx]
                            cached_val = cached_sizes[check_path]
                            
                            # Читаем реально
                            real_content = zf.read(check_path).decode('utf-8', errors='ignore')
                            real_len = len(real_content)
                            
                            if abs(real_len - cached_val) > 5: # Допускаем крошечную погрешность, но лучше точное совпадение
                                print(f"[CACHE] Несовпадение в '{check_path}': кэш={cached_val}, реально={real_len}. Сброс.")
                                all_samples_match = False
                                break
                        
                        if all_samples_match:
                            is_cache_valid = True
                            final_sizes = cached_sizes
            except Exception as e:
                print(f"[CACHE] Ошибка при Sanity Check: {e}. Сброс кэша.")
                is_cache_valid = False

    if is_cache_valid:
        return (final_sizes, True) if return_cache_status else final_sizes

    print("[CACHE] Кэш размеров невалиден или отсутствует, выполняется полный пересчет...")
    final_sizes = {}
    try:
        with zipfile.ZipFile(open(epub_path, 'rb'), 'r') as zf:
            # Перебираем сохраненный ранее список файлов
            for fname, _ in chapter_info_list:
                content_str = zf.read(fname).decode('utf-8', errors='ignore')
                final_sizes[fname] = len(content_str)
    except Exception as e:
        print(f"[ERROR] Не удалось пересчитать размеры глав: {e}")
        return ({}, False) if return_cache_status else {}
        
    new_cache_data = {
        'metadata': {
            'epub_name': current_epub_name,
            'epub_size': current_epub_size,
            'content_checksum': current_content_checksum
        },
        'sizes': final_sizes
    }
    project_manager.save_size_cache(new_cache_data)
    
    return (final_sizes, False) if return_cache_status else final_sizes
    
    
    
# Добавить в gemini_translator/utils/epub_tools.py

def get_chapter_fingerprint(epub_zip, internal_path):
    """
    Извлекает 'отпечаток' главы: заголовок, title и примерную длину текста.
    """
    try:
        from bs4 import BeautifulSoup
        raw_content = epub_zip.read(internal_path).decode('utf-8', errors='ignore')
        soup = BeautifulSoup(raw_content, 'html.parser')
        
        # 1. Заголовок из тега <title>
        title_tag = soup.find('title')
        title_text = title_tag.get_text().strip() if title_tag else ""
        
        # 2. Текст из первого попавшегося H-тега
        h_tag = soup.find(['h1', 'h2', 'h3'])
        h_text = h_tag.get_text().strip() if h_tag else ""
        
        # 3. Чистая длина текста (без тегов)
        clean_text = soup.get_text()
        # Убираем лишние пробелы для более точного сравнения длины
        clean_text = " ".join(clean_text.split())
        
        return {
            'title': title_text,
            'h1': h_text,
            'length': len(clean_text)
        }
    except Exception:
        return None

def compare_epubs_for_swap(old_epub_path, new_epub_path):
    """
    Сравнивает два EPUB файла. 
    Возвращает словарь { internal_path: status }, где status:
    - 'match': Текст совпадает (можно оставить перевод)
    - 'mismatch': Текст изменился (нужно удалить перевод)
    - 'new': Появилась новая глава
    """
    results = {}
    
    try:
        with zipfile.ZipFile(old_epub_path, 'r') as old_zf, \
             zipfile.ZipFile(new_epub_path, 'r') as new_zf:
            
            old_chapters = get_epub_chapter_order(old_epub_path)
            new_chapters = get_epub_chapter_order(new_epub_path)
            
            new_chapters_set = set(new_chapters)
            
            for path in new_chapters:
                if path not in old_chapters:
                    results[path] = 'new'
                    continue
                
                # Если путь совпал, сравниваем содержимое
                old_fp = get_chapter_fingerprint(old_zf, path)
                new_fp = get_chapter_fingerprint(new_zf, path)
                
                if not old_fp or not new_fp:
                    results[path] = 'mismatch'
                    continue
                
                # Критерии сходства:
                # 1. Либо совпадают заголовки (h1 или title)
                # 2. И при этом разница в длине текста не более 50 символов
                titles_match = (old_fp['title'] == new_fp['title'] and old_fp['title'] != "") or \
                               (old_fp['h1'] == new_fp['h1'] and old_fp['h1'] != "")
                
                len_diff = abs(old_fp['length'] - new_fp['length'])
                
                if titles_match and len_diff <= 50:
                    results[path] = 'match'
                else:
                    results[path] = 'mismatch'
                    
    except Exception as e:
        print(f"[COMPARE ERROR] {e}")
        return None
        
    return results


def export_epub_to_json(epub_path, json_path=None):
    """
    Конвертирует EPUB в промежуточную JSON-модель книги.
    Если указан json_path, сохраняет модель на диск.
    """
    from .epub_json import epub_to_json_model, save_json_model

    book_model = epub_to_json_model(epub_path)
    if json_path:
        save_json_model(book_model, json_path)
    return book_model


def build_epub_from_json(book_json, output_path):
    """
    Собирает EPUB обратно из JSON-модели.
    book_json может быть как путем к JSON-файлу, так и уже загруженным словарем.
    """
    from .epub_json import json_model_to_epub, load_json_model

    if isinstance(book_json, (str, os.PathLike)):
        book_model = load_json_model(book_json)
    else:
        book_model = book_json

    json_model_to_epub(book_model, output_path)
    return output_path
