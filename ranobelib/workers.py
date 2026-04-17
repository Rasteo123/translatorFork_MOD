import logging
import os
import re
import tempfile
import time
import traceback
from datetime import datetime, timedelta

from docx import Document
from playwright.sync_api import sync_playwright
from PyQt6.QtCore import QThread, pyqtSignal

from constants import (
    BROWSER_ARGS,
    BROWSER_PROFILE_DIR,
    BROWSER_RULATE_DIR,
    MAX_RETRIES,
    RETRY_DELAY_SEC,
    RUS_MONTHS,
    SELECTORS,
)
from models import ChapterData
from parsers import FileParser
from utils import format_num, format_timedelta

def _has_saved_ranobelib_auth(profile_dir) -> tuple[bool, str | None]:
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                viewport={"width": 1280, "height": 900},
                args=BROWSER_ARGS,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://ranobelib.me", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1200)
                auth_detected = page.evaluate(
                    """() => {
                        try {
                            const raw = localStorage.getItem("auth");
                            if (!raw) {
                                return false;
                            }
                            const parsed = JSON.parse(raw);
                            return !!(parsed && parsed.token && parsed.token.access_token);
                        } catch (error) {
                            return false;
                        }
                    }"""
                )
                return bool(auth_detected), None
            finally:
                context.close()
    except Exception as error:
        return False, str(error)

class RulateDownloadWorker(QThread):
    """
    Открывает Playwright-браузер (с persistent-профилем),
    переходит на страницу книги на rulate,
    выбирает нужные главы (чекбоксы), жмёт «Скачать .docx»,
    обрабатывает скачанный zip и возвращает список ChapterData.
    """
    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int)
    chapters_ready = pyqtSignal(list)       # список ChapterData
    chapter_list_ready = pyqtSignal(list)    # список dict для выбора: [{id, title, number}]
    finished_signal = pyqtSignal()

    def __init__(self, rulate_url: str, default_vol: str,
                 skip_after: int = 0, chapter_ids: list = None):
        """
        rulate_url: ссылка вида https://tl.rulate.ru/book/123870
        default_vol: том по умолчанию
        skip_after: пропустить главы с номером <= skip_after (0 = не пропускать)
        chapter_ids: если задан — скачиваем только эти главы (id из data-id).
                     Если None — сначала эмитим chapter_list_ready для выбора.
        """
        super().__init__()
        self.rulate_url = rulate_url.rstrip("/")
        self.default_vol = default_vol
        self.skip_after = skip_after
        self.chapter_ids = chapter_ids
        self.is_running = True
        self._mode = "download" if chapter_ids else "list"

    def log(self, level: str, msg: str):
        self.log_signal.emit(level, msg)

    def stop(self):
        self.is_running = False

    def run(self):
        try:
            if self._mode == "list":
                self._fetch_chapter_list()
            else:
                self._download_chapters()
        except Exception as e:
            self.log("ERROR", f"Rulate: {e}")
            logging.error(traceback.format_exc())
        finally:
            self.finished_signal.emit()

    def _fetch_chapter_list(self):
        """Получить список глав со страницы книги."""
        self.log("INFO", "Rulate: открываю страницу книги…")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_RULATE_DIR),
                    headless=False,
                    viewport={"width": 1280, "height": 900},
                    args=BROWSER_ARGS,
                )
                page = browser.pages[0]
                page.goto(self.rulate_url, timeout=60000)
                page.wait_for_timeout(3000)

                # Собираем информацию о главах
                chapters_info = page.evaluate("""() => {
                    const rows = document.querySelectorAll('tr.chapter_row');
                    const result = [];
                    for (const row of rows) {
                        const id = row.getAttribute('data-id');
                        const link = row.querySelector('td.t a');
                        const checkbox = row.querySelector('input.download_chapter');
                        if (!link) continue;
                        const title = link.textContent.trim();
                        const hasCheckbox = !!checkbox;
                        // Пробуем извлечь номер из названия:
                        // 1) "Глава/Chapter/Ch N"
                        // 2) просто первое число в заголовке
                        let match = title.match(/(?:Глава|Chapter|Ch)\\s*\\.?\\s*(\\d+(?:\\.\\d+)?)/i);
                        if (!match) {
                            match = title.match(/(\\d+(?:\\.\\d+)?)/);
                        }
                        const num = match ? parseFloat(match[1]) : 0;
                        result.push({
                            id: id,
                            title: title,
                            number: num,
                            downloadable: hasCheckbox,
                        });
                    }
                    return result;
                }""")

                browser.close()

                if not chapters_info:
                    self.log("ERROR", "Rulate: главы не найдены на странице.")
                    return

                self.log("INFO", f"Rulate: найдено {len(chapters_info)} глав "
                         f"({sum(1 for c in chapters_info if c['downloadable'])} доступно для скачивания)")
                self.chapter_list_ready.emit(chapters_info)

        except Exception as e:
            self.log("ERROR", f"Rulate: ошибка при получении списка глав: {e}")
            logging.error(traceback.format_exc())

    def _download_chapters(self):
        """Скачать выбранные главы через форму «Скачать .docx»."""
        if not self.chapter_ids:
            self.log("ERROR", "Rulate: не выбраны главы для скачивания.")
            return

        self.log("INFO", f"Rulate: скачиваю {len(self.chapter_ids)} глав…")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_RULATE_DIR),
                    headless=False,
                    viewport={"width": 1280, "height": 900},
                    args=BROWSER_ARGS,
                )
                page = browser.pages[0]
                page.goto(self.rulate_url, timeout=60000)

                # Ждём загрузки таблицы глав
                page.wait_for_selector('tr.chapter_row', timeout=30000)
                page.wait_for_timeout(2000)

                self.log("INFO", "Rulate: выбираю главы…")
                self.progress_signal.emit(10)

                # Снимаем все чекбоксы, затем ставим нужные
                page.evaluate("""() => {
                    document.querySelectorAll('input.download_chapter').forEach(cb => {
                        cb.checked = false;
                    });
                }""")

                ids_set = set(str(cid) for cid in self.chapter_ids)
                checked_count = page.evaluate("""(ids) => {
                    let count = 0;
                    for (const id of ids) {
                        const row = document.querySelector(`tr[data-id="${id}"]`);
                        if (row) {
                            const cb = row.querySelector('input.download_chapter');
                            if (cb) {
                                cb.checked = true;
                                count++;
                            }
                        }
                    }
                    return count;
                }""", list(ids_set))

                self.log("INFO", f"Rulate: отмечено {checked_count} из {len(ids_set)} глав")
                self.progress_signal.emit(30)

                if checked_count == 0:
                    self.log("ERROR", "Rulate: ни одна глава не была отмечена для скачивания.")
                    browser.close()
                    return

                # Ждём скачивания файла
                self.log("INFO", "Rulate: запускаю скачивание .docx…")

                with page.expect_download(timeout=120000) as download_info:
                    page.click('input[name="download_d"]')

                download = download_info.value
                self.progress_signal.emit(60)
                self.log("INFO", f"Rulate: файл скачивается: {download.suggested_filename}")

                # Сохраняем во временную папку
                tmp_dir = tempfile.mkdtemp(prefix="rulate_")
                zip_path = os.path.join(tmp_dir, download.suggested_filename or "rulate_chapters.zip")
                download.save_as(zip_path)

                self.progress_signal.emit(80)
                self.log("SUCCESS", f"Rulate: файл сохранён ({os.path.getsize(zip_path):,} байт)")

                browser.close()

                # Парсим скачанный zip
                self.log("INFO", "Rulate: разбираю скачанный архив…")

                if zip_path.lower().endswith(".zip"):
                    chapters = FileParser.parse_zip_docx(
                        zip_path, self.default_vol, self.log
                    )
                elif zip_path.lower().endswith(".docx"):
                    # Если скачан одним файлом (без zip)
                    doc = Document(zip_path)
                    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                    content = "\n".join(paragraphs)
                    chapters = [ChapterData(self.default_vol, 1, "", content)]
                else:
                    self.log("ERROR", f"Rulate: неизвестный формат файла: {zip_path}")
                    return

                self.progress_signal.emit(100)

                if chapters:
                    self.log("SUCCESS", f"Rulate: получено {len(chapters)} глав")
                    self.chapters_ready.emit(chapters)
                else:
                    self.log("WARNING", "Rulate: из архива не удалось извлечь ни одной главы")

                # Очистка
                try:
                    os.remove(zip_path)
                    os.rmdir(tmp_dir)
                except OSError:
                    pass

        except Exception as e:
            self.log("ERROR", f"Rulate: ошибка скачивания: {e}")
            logging.error(traceback.format_exc())


# ─── Рабочий поток: определение последней главы на RanobeLib ────────────────

class LastChapterDetector(QThread):
    """
    Определяет номер последней залитой главы на RanobeLib.

    Способ 1 (основной): открыть страницу add-chapter — поле «Глава»
                         содержит предложенный номер (последняя + 1).
    Способ 2 (фолбэк):  открыть ?section=chapters — первая глава в списке
                         (отсортирован новые→старые) и есть последняя.
    """
    log_signal = pyqtSignal(str, str)
    result_signal = pyqtSignal(float, str)  # (номер_главы, описание)
    finished_signal = pyqtSignal()

    def __init__(self, ranobelib_url: str):
        super().__init__()
        self.raw_url = ranobelib_url.strip()
        # URL add-chapter для способа 1
        self.add_chapter_url = self.raw_url
        if not self.add_chapter_url.rstrip("/").endswith("/add-chapter"):
            self.add_chapter_url = self.add_chapter_url.rstrip("/") + "/add-chapter"
        # URL ?section=chapters для способа 2
        self.book_url = re.sub(r"/add-chapter\s*$", "", self.raw_url)
        self.chapters_url = self.book_url.rstrip("/") + "?section=chapters"
        self.is_running = True

    def log(self, level: str, msg: str):
        self.log_signal.emit(level, msg)

    def stop(self):
        self.is_running = False

    def run(self):
        try:
            self._detect()
        except Exception as e:
            self.log("ERROR", f"Детектор: {e}")
            logging.error(traceback.format_exc())
        finally:
            self.finished_signal.emit()

    def _detect(self):
        self.log("INFO", "Определяю последнюю залитую главу на RanobeLib…")
        last_chapter_num = 0.0
        last_chapter_desc = ""

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_PROFILE_DIR),
                    headless=True,
                    viewport={"width": 1280, "height": 900},
                    args=BROWSER_ARGS,
                )
                page = browser.pages[0]

                # ── Способ 1: страница add-chapter ──
                # Поле «Глава» содержит предложенный номер (последняя + 1)
                try:
                    page.goto(self.add_chapter_url, timeout=30000)
                    page.wait_for_selector(
                        SELECTORS["chapter_input"], state="visible", timeout=10000
                    )
                    suggested = page.input_value(SELECTORS["chapter_input"]).strip()
                    if suggested:
                        suggested_num = float(suggested)
                        if suggested_num > 1:
                            last_chapter_num = suggested_num - 1
                            last_chapter_desc = f"на add-chapter предложена {format_num(suggested_num)}"
                        elif suggested_num == 1:
                            # Предложена глава 1 — значит глав ещё нет
                            last_chapter_num = 0
                            last_chapter_desc = "глав ещё нет"
                        self.log("INFO",
                                 f"Способ 1: поле «Глава» = {suggested} → "
                                 f"последняя = {format_num(last_chapter_num)}")
                except Exception as e:
                    self.log("WARNING", f"Способ 1 (add-chapter) не сработал: {e}")

                # ── Способ 2 (фолбэк): ?section=chapters ──
                if last_chapter_num == 0 and last_chapter_desc != "глав ещё нет":
                    try:
                        self.log("INFO", "Пробую способ 2: ?section=chapters…")
                        page.goto(self.chapters_url, timeout=30000)
                        page.wait_for_timeout(4000)

                        result = page.evaluate("""() => {
                            const body = document.body.innerText;

                            // �?щем все упоминания "Глава X" на странице
                            const matches = [...body.matchAll(
                                /(?:Том\\s*\\d+\\s+)?Глава\\s+(\\d+(?:\\.\\d+)?)/gi
                            )];
                            let maxNum = 0;
                            let maxMatch = '';
                            for (const m of matches) {
                                const num = parseFloat(m[1]);
                                if (num > maxNum) {
                                    maxNum = num;
                                    maxMatch = m[0];
                                }
                            }
                            return { number: maxNum, description: maxMatch };
                        }""")

                        num = result.get("number", 0)
                        if num > 0:
                            last_chapter_num = num
                            last_chapter_desc = result.get("description", "")
                            self.log("INFO",
                                     f"Способ 2: найдена последняя глава {format_num(num)} ({last_chapter_desc})")
                    except Exception as e:
                        self.log("WARNING", f"Способ 2 (?section=chapters) не сработал: {e}")

                browser.close()

                if last_chapter_num > 0:
                    self.log("SUCCESS",
                             f"Последняя глава на RanobeLib: {format_num(last_chapter_num)} ({last_chapter_desc})")
                elif last_chapter_desc == "глав ещё нет":
                    self.log("INFO", "На RanobeLib ещё нет глав. Пропуск не требуется.")
                else:
                    self.log("WARNING", "Не удалось определить последнюю главу. "
                             "Возможно, требуется авторизация на RanobeLib.")

                self.result_signal.emit(last_chapter_num, last_chapter_desc)

        except Exception as e:
            self.log("ERROR", f"Детектор: ошибка: {e}")
            self.result_signal.emit(0.0, "Ошибка")


# ─── Рабочий поток: загрузка глав ───────────────────────────────────────────

class UploadWorker(QThread):
    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int)
    stats_signal = pyqtSignal(int, int, int)  # ok, errors, skipped
    eta_signal = pyqtSignal(str)               # "~12мин 30сек"
    finished_signal = pyqtSignal()
    chapter_done_signal = pyqtSignal(int)      # Feature 2: индекс завершённой главы

    def __init__(
        self,
        url: str,
        chapters_list: list[ChapterData],
        schedule_enabled: bool,
        start_time: datetime,
        interval_minutes: int,
        paid_enabled: bool,
        price: int,
        force_num: bool,
    ):
        super().__init__()
        self.url = url
        self.chapters_list = chapters_list
        self.schedule_enabled = schedule_enabled
        self.current_publish_time = start_time
        self.interval_minutes = interval_minutes
        self.paid_enabled = paid_enabled
        self.price = price
        self.force_num = force_num
        self.is_running = True
        self.limit_date = datetime.now() + timedelta(days=60)

        # Статистика
        self._ok = 0
        self._errors = 0
        self._skipped = 0
        self._times: list[float] = []  # время загрузки каждой главы

    def log(self, level: str, msg: str):
        self.log_signal.emit(level, msg)

    # ── Настройка времени в поповере ──

    def _adjust_time_column(self, page, popover, col_index: int, target_val: str):
        try:
            btn_up = popover.locator(SELECTORS["arrow_up"]).nth(col_index)
            btn_down = popover.locator(SELECTORS["arrow_down"]).nth(col_index)
            container = btn_up.locator("xpath=../..")
            target_int = int(target_val)

            for _ in range(60):
                if not self.is_running:
                    return
                raw = container.inner_text()
                m = re.search(r"\d+", raw)
                if not m:
                    page.wait_for_timeout(100)
                    continue
                curr = int(m.group(0))
                if curr == target_int:
                    return

                diff = target_int - curr
                # Выбираем кратчайший путь (для часов 0–23)
                go_up = diff > 0 if abs(diff) < 30 else diff <= 0
                (btn_up if go_up else btn_down).click()
                page.wait_for_timeout(70)

        except Exception as e:
            self.log("WARNING", f"Сбой настройки времени (колонка {col_index}): {e}")

    def _set_calendar_date(self, page, popover, target_dt: datetime):
        try:
            target_year = target_dt.year
            target_month = target_dt.month

            for _ in range(24):
                page.wait_for_timeout(200)
                header = popover.text_content().lower()

                found_year = -1
                found_month = -1
                ym = re.search(r"20\d{2}", header)
                if ym:
                    found_year = int(ym.group(0))
                for name, num in RUS_MONTHS.items():
                    if name in header:
                        found_month = num
                        break

                if found_month == -1 or found_year == -1:
                    popover.locator(SELECTORS["month_arrow_right"]).click()
                    page.wait_for_timeout(500)
                    continue

                if found_year == target_year and found_month == target_month:
                    break

                curr_val = found_year * 12 + found_month
                tgt_val = target_year * 12 + target_month
                arrow = (
                    SELECTORS["month_arrow_right"]
                    if curr_val < tgt_val
                    else SELECTORS["month_arrow_left"]
                )
                popover.locator(arrow).click()
                page.wait_for_timeout(600)

            # Выбор дня
            day_int = target_dt.day
            day_str = str(day_int)
            page.wait_for_timeout(300)

            candidates = popover.get_by_text(day_str, exact=True).all()
            real_days = [
                el
                for el in candidates
                if el.locator("xpath=..").locator("svg").count() == 0
            ]

            day_el = None
            if not real_days:
                self.log("WARNING", f"День {day_str} не найден, пробуем первый кандидат")
                if candidates:
                    day_el = candidates[0]
            elif len(real_days) > 1:
                day_el = real_days[0] if day_int <= 15 else real_days[-1]
            else:
                day_el = real_days[0]

            if day_el:
                for attempt in range(3):
                    try:
                        day_el.click(force=True, timeout=1000)
                        break
                    except Exception:
                        page.wait_for_timeout(200)

            page.wait_for_timeout(500)

            # Часы и минуты
            self._adjust_time_column(page, popover, 0, f"{target_dt.hour:02d}")
            self._adjust_time_column(page, popover, 1, f"{target_dt.minute:02d}")

        except Exception as e:
            self.log("ERROR", f"Ошибка календаря: {e}")

    def _normalize_num_text(self, raw: str) -> str:
        text = (raw or "").strip().replace(",", ".")
        if not text:
            return ""
        try:
            return format_num(float(text))
        except Exception:
            return text

    def _read_suggested_number(self, page) -> str:
        try:
            page.wait_for_selector(SELECTORS["chapter_input"], state="visible", timeout=10000)
            raw = page.input_value(SELECTORS["chapter_input"])
            return self._normalize_num_text(raw)
        except Exception:
            return ""

    def _assert_next_number_changed(self, page, before_num: str):
        """
        Верификация успешного сохранения:
        если после сохранения предложенный номер на add-chapter не изменился,
        считаем, что главу нужно перезалить.
        """
        if not before_num:
            return

        page.goto(self.url, timeout=30000)
        page.wait_for_selector(SELECTORS["volume_input"], state="visible", timeout=15000)
        after_num = self._read_suggested_number(page)
        if after_num and after_num == before_num:
            raise RuntimeError(
                f"После сохранения номер следующей главы не изменился ({after_num})"
            )

    # ── Загрузка одной главы (с retry) ──

    def _upload_chapter(self, page, chapter: ChapterData) -> bool:
        """
        Загрузить одну главу. Возвращает True при успехе.
        """
        for attempt in range(1, MAX_RETRIES + 1):
            if not self.is_running:
                return False
            attempt_publish_time = self.current_publish_time
            try:
                # Навигация
                try:
                    page.goto(self.url, timeout=30000)
                    page.wait_for_selector(
                        SELECTORS["volume_input"], state="visible", timeout=15000
                    )
                except Exception:
                    self.log("WARNING", f"Релоад страницы (попытка {attempt})...")
                    page.reload()
                    page.wait_for_selector(SELECTORS["volume_input"], timeout=20000)

                suggested_before = self._read_suggested_number(page)

                # Заполнение полей
                page.fill(SELECTORS["volume_input"], str(chapter.volume))

                if self.force_num:
                    try:
                        page.fill(
                            SELECTORS["chapter_input"], format_num(chapter.number)
                        )
                    except Exception as e:
                        self.log("WARNING", f"Не удалось вписать номер главы: {e}")

                if chapter.title:
                    page.fill(SELECTORS["title_input"], chapter.title)

                # Вставка контента в редактор
                page.click(SELECTORS["editor_area"])
                content = chapter.content
                is_html = content.strip().startswith("<")

                if is_html and len(content) > 5000:
                    # HTML-контент (из epub) — быстрая вставка через innerHTML
                    page.evaluate(
                        """(text) => {
                            const editor = document.querySelector('.ProseMirror');
                            if (editor) { editor.innerHTML = text; }
                        }""",
                        content,
                    )
                    page.wait_for_timeout(300)
                elif not is_html and len(content) > 5000:
                    # Plain-text (из docx/txt) — оборачиваем абзацы в <p>
                    html_content = "".join(
                        f"<p>{line}</p>" for line in content.split("\n") if line.strip()
                    )
                    page.evaluate(
                        """(text) => {
                            const editor = document.querySelector('.ProseMirror');
                            if (editor) { editor.innerHTML = text; }
                        }""",
                        html_content,
                    )
                    page.wait_for_timeout(300)
                else:
                    # Короткий контент — insert_text сохраняет переносы
                    page.keyboard.insert_text(content)

                # Платный доступ + расписание
                if self.paid_enabled:
                    self._configure_paid(page, chapter)
                elif self.schedule_enabled:
                    self._configure_schedule(page)

                # Сохранение
                save_btn = page.locator(SELECTORS["submit_btn"])
                if save_btn.is_visible():
                    save_btn.click()
                else:
                    page.get_by_text("Создать", exact=True).click()

                # Ждём подтверждения (проверяем, что страница сменилась или появилось сообщение)
                page.wait_for_timeout(2000)
                self._assert_next_number_changed(page, suggested_before)

                self.log(
                    "SUCCESS",
                    f"Глава {format_num(chapter.number)} сохранена"
                    + (f" (попытка {attempt})" if attempt > 1 else ""),
                )
                return True

            except Exception as e:
                if self.schedule_enabled:
                    # При ретрае не сдвигаем расписание для этой же главы
                    self.current_publish_time = attempt_publish_time
                self.log(
                    "WARNING",
                    f"Попытка {attempt}/{MAX_RETRIES} для Гл.{format_num(chapter.number)}: {e}",
                )
                if attempt < MAX_RETRIES:
                    page.wait_for_timeout(RETRY_DELAY_SEC * 1000)
                else:
                    self.log(
                        "ERROR",
                        f"Глава {format_num(chapter.number)} — все попытки исчерпаны.",
                    )
                    return False

        return False

    def _configure_paid(self, page, chapter: ChapterData):
        try:
            page.click(SELECTORS["gear_btn"])
            popover = page.locator(SELECTORS["popover"]).first
            popover.wait_for(state="visible", timeout=3000)

            paid_chk = popover.locator('input[type="checkbox"]').first
            if not paid_chk.is_checked():
                paid_chk.click(force=True)
            page.wait_for_timeout(300)

            price_field = popover.locator(SELECTORS["price_input"])
            if price_field.is_visible():
                price_field.fill(str(self.price))

            if self.schedule_enabled:
                self._set_calendar_date(page, popover, self.current_publish_time)
                self.current_publish_time += timedelta(minutes=self.interval_minutes)

            page.mouse.click(0, 0)
            page.wait_for_timeout(300)
        except Exception as e:
            self.log("WARNING", f"Сбой платных настроек: {e}")

    def _configure_schedule(self, page):
        try:
            page.click(SELECTORS["clock_btn"])
            popover = page.locator(SELECTORS["popover"]).first
            popover.wait_for(state="visible", timeout=3000)

            sched_chk = popover.locator('input[type="checkbox"]').first
            if not sched_chk.is_checked():
                sched_chk.click(force=True)
            page.wait_for_timeout(300)

            self._set_calendar_date(page, popover, self.current_publish_time)
            page.mouse.click(0, 0)
            self.current_publish_time += timedelta(minutes=self.interval_minutes)
        except Exception as e:
            self.log("WARNING", f"Сбой отложки: {e}")

    # ── Основной цикл ──

    def run(self):
        self.log("INFO", "Запуск браузера Chrome...")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_PROFILE_DIR),
                    headless=False,
                    viewport={"width": 1280, "height": 900},
                    args=BROWSER_ARGS,
                )
                page = browser.pages[0]
                total = len(self.chapters_list)

                for index, chapter in enumerate(self.chapters_list):
                    if not self.is_running:
                        self._skipped += total - index
                        break

                    if (
                        self.schedule_enabled
                        and self.current_publish_time > self.limit_date
                    ):
                        self.log("ERROR", "ПРЕДЕЛ В 60 ДНЕЙ. Остановка.")
                        self._skipped += total - index
                        break

                    # Лог статуса
                    parts = []
                    if self.schedule_enabled:
                        parts.append(
                            self.current_publish_time.strftime("%d.%m %H:%M")
                        )
                    else:
                        parts.append("Сразу")
                    if self.paid_enabled:
                        parts.append(f"Плат: {self.price}₽")
                    self.log(
                        "INFO",
                        f"[{index + 1}/{total}] Т.{chapter.volume} "
                        f"Гл.{format_num(chapter.number)} ({', '.join(parts)})",
                    )

                    t0 = time.monotonic()
                    ok = self._upload_chapter(page, chapter)
                    elapsed = time.monotonic() - t0
                    self._times.append(elapsed)

                    if ok:
                        self._ok += 1
                        self.chapter_done_signal.emit(index)
                    else:
                        self._errors += 1

                    progress = int(((index + 1) / total) * 100)
                    self.progress_signal.emit(progress)
                    self.stats_signal.emit(self._ok, self._errors, self._skipped)

                    # ETA
                    remaining = total - (index + 1)
                    if remaining > 0 and self._times:
                        avg = sum(self._times) / len(self._times)
                        eta = timedelta(seconds=avg * remaining)
                        self.eta_signal.emit(f"~{format_timedelta(eta)}")
                    else:
                        self.eta_signal.emit("—")

                    page.wait_for_timeout(1000)

                browser.close()

        except Exception as e:
            self.log("ERROR", f"Критическая ошибка браузера: {e}")
            logging.error(traceback.format_exc())

        self.stats_signal.emit(self._ok, self._errors, self._skipped)
        self.finished_signal.emit()

    def stop(self):
        self.is_running = False


# ─── Рабочий поток: авторизация ─────────────────────────────────────────────

class LoginWorker(QThread):
    log_signal = pyqtSignal(str, str)
    finished_signal = pyqtSignal()

    def __init__(self, site="ranobelib"):
        super().__init__()
        self._browser = None
        self._site = site  # "ranobelib" или "rulate"

    def run(self):
        try:
            profile_dir = BROWSER_PROFILE_DIR if self._site == "ranobelib" else BROWSER_RULATE_DIR
            start_url = "https://ranobelib.me" if self._site == "ranobelib" else "https://tl.rulate.ru"
            site_label = "RanobeLib" if self._site == "ranobelib" else "Rulate"

            with sync_playwright() as p:
                self._browser = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=False,
                    args=BROWSER_ARGS,
                )
                page = self._browser.pages[0]
                try:
                    page.goto(start_url, timeout=60000)
                except Exception:
                    pass
                self.log_signal.emit(
                    "WARNING", f">>> ВОЙДИТЕ В АККАУНТ {site_label} И ЗАКРОЙТЕ БРАУЗЕР <<<"
                )
                try:
                    while True:
                        page.wait_for_timeout(1000)
                except Exception:
                    pass
                self.log_signal.emit("SUCCESS", f"Браузер {site_label} закрыт. Куки сохранены.")
        except Exception as e:
            self.log_signal.emit("ERROR", f"Ошибка авторизации: {e}")
        finally:
            self.finished_signal.emit()

    def stop(self):
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass


