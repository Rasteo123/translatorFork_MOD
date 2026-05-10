import os
import copy

from bs4 import BeautifulSoup

from gemini_translator.utils.epub_json import (
    TRANSLATION_PAYLOAD_VERSION,
    apply_transport_payload,
    apply_translation_payload,
    build_translation_payload,
    extract_json_payload,
    render_document_html,
)
from gemini_translator.utils.batch_markers import find_boundary_markers
from gemini_translator.utils.text import (
    prettify_html,
    clean_html_content,
    coerce_translated_body_block,
)

class ResponseParser:
    def __init__(self, worker, log_callback, project_manager=None, task_manager=None, validator_func=None, prompt_builder=None):
        self.worker = worker # <-- ГЛАВНОЕ ИЗМЕНЕНИЕ
        self.log = log_callback
        self.project_manager = project_manager
        self.task_manager = task_manager
        self.validator_func = validator_func
        self.prompt_builder = prompt_builder # <-- Сохраняем ссылку
    
    def _get_text_length_before(self, element, soup):
        length = 0
        for prev_element in element.previous_elements:
            if isinstance(prev_element, str):
                length += len(prev_element)
        return length

    def _restore_media_from_placeholders(self, translated_content, original_content_for_map_building):
        if not self.prompt_builder:
            return translated_content
            
        media_map, link_map = self.prompt_builder._replace_media_with_placeholders(original_content_for_map_building, return_maps=True)
        restored_content = translated_content
        
        # === ЭТАП 1: МЕДИА ===
        if media_map:
            lost_media = []
            for placeholder, data in media_map.items():
                if placeholder in restored_content:
                    restored_content = restored_content.replace(placeholder, data['tag_str'])
                else:
                    lost_media.append((placeholder, data))
            
            if lost_media:
                # (Здесь можно применить ту же логику снайпера, что и для ссылок ниже.
                # Для краткости я опускаю дублирование кода, так как принцип идентичен)
                self.log(f"[WARN] Обнаружено {len(lost_media)} потерянных медиа.")

        # === ЭТАП 2: ССЫЛКИ (СНАЙПЕРСКИЙ РЕЖИМ) ===
        if not link_map:
            return restored_content

        soup = BeautifulSoup(restored_content, 'html.parser')
        restored_ids = set()

        # 2.1 Штатное восстановление по ID (если AI сохранил тег)
        placeholder_links = soup.find_all('a', id=lambda x: x and x.startswith('link_'))
        for tag in placeholder_links:
            pid = tag.get('id')
            if pid in link_map:
                data = link_map[pid]
                new_soup = BeautifulSoup(data['tag_str'], 'html.parser')
                if new_soup.a:
                    # ВАЖНОЕ ИСПРАВЛЕНИЕ:
                    # Ранее .string = tag.get_text() уничтожало вложенные теги (например, img),
                    # так как у картинок нет текста. Теперь мы переносим ВСЁ содержимое (контент),
                    # которое уже содержит восстановленные на Этапе 1 медиа-объекты.
                    if tag.contents:
                        new_soup.a.clear()
                        # Копируем список содержимого, чтобы безопасно перенести элементы
                        new_soup.a.extend([child for child in tag.contents])
                    
                    tag.replace_with(new_soup.a)
                    restored_ids.add(pid)

        # 2.2 Снайперское восстановление (если AI удалил тег)
        lost_link_ids = set(link_map.keys()) - restored_ids
        
        if lost_link_ids:
            # Важно: обрабатываем в обратном порядке ID, чтобы при вставке в начало (START)
            # порядок 0, 1 сохранялся (вставляем 1, потом 0 перед ним -> 0, 1).
            lost_ordered = [(pid, link_map[pid]) for pid in sorted(list(lost_link_ids), key=lambda k: int(k.split('_')[-1]), reverse=True)]
            
            # Расчет общей длины для навигации
            orig_with_ph = self.prompt_builder._replace_media_with_placeholders(original_content_for_map_building)
            soup_orig = BeautifulSoup(orig_with_ph, 'html.parser')
            total_orig_len = len(soup_orig.get_text())
            
            all_trans_text_nodes = soup.find_all(string=True)
            total_trans_len = sum(len(t) for t in all_trans_text_nodes)

            if total_orig_len > 0 and total_trans_len > 0:
                for pid, data in lost_ordered:
                    # --- А. Находим примерное место в тексте ---
                    orig_tag = soup_orig.find('a', id=pid)
                    pos_ratio = 0
                    if orig_tag:
                        pos_ratio = self._get_text_length_before(orig_tag, soup_orig) / total_orig_len
                    
                    target_char = int(total_trans_len * pos_ratio)
                    
                    # Находим текстовый узел, соответствующий этой позиции
                    curr_len = 0
                    target_node = None
                    for node in all_trans_text_nodes:
                        if curr_len + len(node) >= target_char:
                            target_node = node
                            break
                        curr_len += len(node)
                    
                    if not target_node: continue

                    # --- Б. Ищем контекстного родителя (Магнит) ---
                    expected_parent = data['parent_tag'] # h1, p, div...
                    rel_pos = data['rel_pos'] # START, END, MIDDLE
                    
                    current_block = target_node.find_parent(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
                    best_anchor = current_block
                    
                    # Если мы попали не в тот тип блока (например, в p вместо h1), ищем соседей
                    if current_block and current_block.name != expected_parent:
                        prev_sib = current_block.find_previous_sibling()
                        next_sib = current_block.find_next_sibling()
                        
                        if prev_sib and prev_sib.name == expected_parent:
                            best_anchor = prev_sib
                        elif next_sib and next_sib.name == expected_parent:
                            best_anchor = next_sib
                    
                    # --- В. Вставляем с учетом локальной позиции ---
                    if best_anchor:
                        restored_tag = BeautifulSoup(data['tag_str'], 'html.parser').a
                        
                        if rel_pos == 'START' or rel_pos == 'SOLO':
                            best_anchor.insert(0, restored_tag)
                            self.log(f"  -> Ссылка восстановлена в НАЧАЛО <{best_anchor.name}>.")
                        
                        elif rel_pos == 'END':
                            best_anchor.append(restored_tag)
                            self.log(f"  -> Ссылка восстановлена в КОНЕЦ <{best_anchor.name}>.")
                        
                        else: # MIDDLE
                            # Если мы в правильном блоке, вставляем перед найденным текстом
                            if best_anchor == current_block:
                                # target_node - это NavigableString. Вставляем перед ним.
                                # Если target_node внутри span/em, вставляем перед родителем.
                                insert_point = target_node
                                if target_node.parent != best_anchor:
                                    insert_point = target_node.parent
                                
                                try:
                                    insert_point.insert_before(restored_tag)
                                    self.log(f"  -> Ссылка восстановлена ВНУТРИ <{best_anchor.name}>.")
                                except ValueError:
                                    best_anchor.append(restored_tag)
                            else:
                                # Если мы примагнитились к соседу, безопаснее вставить в конец
                                best_anchor.append(restored_tag)

        return str(soup)

    def parse_json_translation_response(self, raw_response, document_model, source_payload=None):
        parsed_payload = raw_response if isinstance(raw_response, dict) else extract_json_payload(raw_response)
        if not isinstance(parsed_payload, dict):
            raise ValueError("JSON-ответ должен быть объектом.")

        working_document = copy.deepcopy(document_model)
        source_payload = source_payload or build_translation_payload(working_document)
        if "b" in parsed_payload:
            apply_transport_payload(working_document, parsed_payload, source_payload)
            return render_document_html(working_document)
        normalized_payload = {
            "schema_version": parsed_payload.get("schema_version", TRANSLATION_PAYLOAD_VERSION),
            "document_id": parsed_payload.get("document_id", source_payload.get("document_id", "")),
            "blocks": parsed_payload.get("blocks", []),
        }
        apply_translation_payload(working_document, normalized_payload, source_payload)
        return render_document_html(working_document)

    def parse_json_batch_response(self, raw_response, source_documents):
        parsed_payload = extract_json_payload(raw_response)
        if not isinstance(parsed_payload, dict):
            raise ValueError("JSON batch-ответ должен быть объектом.")

        documents = parsed_payload.get("documents")
        if not isinstance(documents, list):
            raise ValueError("В JSON batch-ответе отсутствует список documents.")
        if len(documents) != len(source_documents):
            raise ValueError("Количество документов в batch-ответе не совпало с запросом.")

        report = {'successful': [], 'failed': []}
        for source_info, candidate_payload in zip(source_documents, documents):
            chapter_path = source_info['chapter_path']
            try:
                translated_html = self.parse_json_translation_response(
                    raw_response=candidate_payload,
                    document_model=source_info['document_model'],
                    source_payload=source_info['payload'],
                )
                report['successful'].append({
                    'original_path': chapter_path,
                    'final_html': translated_html
                })
            except Exception as exc:
                report['failed'].append((chapter_path, f"JSON pipeline: {exc}"))
        return report

    def unpack_and_validate_batch(self, translated_full_text, chapter_list, original_contents):
        """
        Анализирует ответ API.
        Версия с ИСПРАВЛЕННЫМ ПОРЯДКОМ ОПЕРАЦИЙ.
        """
        report = {'successful': [], 'failed': []}
        try:
            markers_map = self._find_boundary_markers(
                translated_full_text,
                chapter_count=len(chapter_list),
            )

            for i, chapter_path in enumerate(chapter_list):
                try:
                    start_marker_info = markers_map.get(i)
                    end_marker_info = markers_map.get(i + 1)
                    
                    if start_marker_info is None or end_marker_info is None:
                        report['failed'].append((chapter_path, "Маркеры не найдены"))
                        continue

                    start_pos, end_pos = start_marker_info[1], end_marker_info[0]
                    extracted_block_raw = translated_full_text[start_pos:end_pos].strip()
                    
                    # 1. Получаем "сырой" контент от AI
                    extracted_body_only = clean_html_content(extracted_block_raw, is_html=True)
                    if not extracted_body_only:
                        report['failed'].append((chapter_path, "Пустой контент (body)"))
                        continue
                    
                    raw_body_from_ai = clean_html_content(extracted_block_raw, is_html=True)
                    if not raw_body_from_ai:
                        report['failed'].append((chapter_path, "Пустой контент (body)"))
                        continue
                    
                    # 2. Получаем "чистый" оригинал
                    original_content = original_contents[chapter_path]
                    original_body = clean_html_content(original_content, is_html=True)
                    
                    # 3. [ГЛАВНЫЙ ФИКС] Создаем "эталон" для валидации
                    #    Это версия ОРИГИНАЛА, но с плейсхолдерами - точная копия того, что ушло в AI.
                    original_with_placeholders = self.prompt_builder._replace_media_with_placeholders(original_body)
                    raw_body_from_ai = coerce_translated_body_block(original_with_placeholders, raw_body_from_ai)

                    # 4. ВАЛИДИРУЕМ "яблоки с яблоками"
                    if self.validator_func:
                        # Сравниваем ответ AI с тем, что мы ему отправляли
                        # Теперь принимаем ТРИ значения: успех, причину и (возможно исправленный) HTML
                        is_valid, reason, validated_html = self.validator_func(original_with_placeholders, raw_body_from_ai)
                        
                        if not is_valid:
                            report['failed'].append((chapter_path, f"Валидация: {reason}"))
                            continue
                        
                        # Если валидация прошла (возможно, после авто-лечения),
                        # обновляем контент на исправленную версию перед восстановлением медиа
                        raw_body_from_ai = validated_html
                    
                    # 5. Только ПОСЛЕ успеха восстанавливаем медиа
                    restored_body_str = self._restore_media_from_placeholders(raw_body_from_ai, original_content)
                    restored_body_str = coerce_translated_body_block(original_body, restored_body_str)
                    
                    # 6. Собираем финальный файл
                    final_html = ""
                    soup_original_full = BeautifulSoup(original_content, 'html.parser')

                    if soup_original_full.body:
                        restored_body_soup = BeautifulSoup(restored_body_str, 'html.parser').body
                        if not restored_body_soup:
                            report['failed'].append((chapter_path, "Ошибка парсинга body"))
                            continue
                        soup_original_full.body.replace_with(restored_body_soup)
                        final_html = str(soup_original_full)
                    else:
                        final_html = restored_body_str
                    
                    report['successful'].append({
                        'original_path': chapter_path,
                        'final_html': final_html
                    })
                except Exception as e:
                    report['failed'].append((chapter_path, f"Ошибка обработки: {str(e)}"))
        except Exception as es:
            for ch in chapter_list:
                report['failed'].append((ch, "Критическая ошибка разбора пакета"))
            report['successful'].clear()
            
        return report
        
    def process_and_save_single_file(self, translated_body_content, original_full_content, prefix_html, suffix_html, output_path, original_internal_path, version_suffix):
        """
        Собирает финальный файл и сохраняет результат.
        НЕ занимается восстановлением медиа (оно происходит раньше).
        """
        
        # 1. Собираем финальный HTML для записи в файл
        translated_body_content = coerce_translated_body_block(original_full_content, translated_body_content)
        final_html_to_write = prefix_html + translated_body_content + suffix_html
        use_prettify = getattr(self.worker, "use_prettify", False)
        if use_prettify:
            final_html_to_write = prettify_html(final_html_to_write)
        
        # 2. Записываем файл на диск
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(final_html_to_write)
            
        # 3. Регистрируем в карте проекта
        if self.project_manager:
            relative_path = os.path.relpath(output_path, self.project_manager.project_folder)
            self.project_manager.register_translation(
                original_internal_path=original_internal_path,
                version_suffix=version_suffix,
                translated_relative_path=relative_path
            )
    
    def _find_boundary_markers(self, text, chapter_count=None):
        return find_boundary_markers(text, chapter_count=chapter_count)
