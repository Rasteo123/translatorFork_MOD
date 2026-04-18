import sys
import os
import json
import asyncio
import shutil
import re
import array
import queue
import time
import threading
import traceback
import platform
import subprocess
import io
import logging

# PyQt6 Imports
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QVBoxLayout,
    QHBoxLayout, QWidget, QTextEdit, QPushButton, QLabel,
    QProgressBar, QMessageBox, QInputDialog, QSplitter,
    QListWidget, QListWidgetItem, QToolBar, QSlider,
    QSizePolicy, QCheckBox, QMenu, QComboBox, QSpinBox,
    QDialog, QDialogButtonBox, QScrollArea, QTabWidget, QPlainTextEdit
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QTextCursor, QTextCharFormat, QColor, QAction, QTextBlockFormat, QDragEnterEvent, QDropEvent, QIcon

# Libraries
try:
    import ebooklib
    from ebooklib import epub
except ImportError:
    ebooklib = None
    epub = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import nltk
except ImportError:
    nltk = None

try:
    import pyaudio
except ImportError:
    pyaudio = None

try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None

try:
    import edge_tts  # pip install edge-tts
except ImportError:
    edge_tts = None

# Google SDK
try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None
    genai_types = None

try:
    from loguru import logger as _loguru_logger
except ImportError:
    _loguru_logger = None

if platform.system() == "Windows":
    import subprocess
    # Патч: заставляем все процессы запускаться без окна консоли
    _orig_popen = subprocess.Popen
    def _hidden_popen(*args, **kwargs):
        if 'creationflags' not in kwargs:
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        return _orig_popen(*args, **kwargs)
    subprocess.Popen = _hidden_popen





# --- КОНФИГУРАЦИЯ ---
MODEL_ID = "gemini-3.1-flash-live-preview" # Или gemini-2.0-flash-native-audio-preview-12-2025
AUDIO_RATE = 24000
AUDIO_CHANNELS = 1
WINDOWS_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
READER_SETTINGS_KEY = "gemini_reader_settings"
LEGACY_SETTINGS_FILE = "settings.json"
READER_BOOKS_DIRNAME = "gemini_reader_books"
MAX_PART_SIZE = 1.45 * 1024 * 1024 * 1024 # ~1.5 ГБ порог для склейки

VOICES_MAP = {
    "Puck": "М", "Charon": "М", "Kore": "Ж", "Fenrir": "М", "Aoede": "Ж",
    "Zephyr": "Ж", "Leda": "Ж", "Orus": "М", "Callirrhoe": "Ж", "Autonoe": "Ж",
    "Enceladus": "М", "Iapetus": "М", "Umbriel": "М", "Algieba": "М", "Despina": "Ж",
    "Erinome": "Ж", "Algenib": "М", "Rasalgethi": "М", "Laomedeia": "Ж", "Achernar": "Ж",
    "Alnilam": "М", "Schedar": "М", "Gacrux": "Ж", "Pulcherrima": "Ж", "Achird": "М",
    "Zubenelgenubi": "М", "Vindemiatrix": "Ж", "Sadachbia": "М", "Sadaltager": "М", "Sulafat": "Ж"
}

EDGE_VOICE = "ru-RU-SvetlanaNeural" # Голос для подмены при цензуре

SPEED_PROMPTS = {
    "Very Slow": "Читай очень медленно и размеренно. Делай отчетливые паузы между фразами.",
    "Slow": "Читай медленно, в спокойном и расслабленном темпе.",
    "Normal": "Читай в естественном, нормальном темпе, как при обычном разговоре.",
    "Fast": "Читай в быстром, энергичном темпе.",
    "Very Fast": "Читай очень быстро, максимально динамично."
}

# --- СИСТЕМА ЛОГИРОВАНИЯ В GUI ---
class LogSignal(QObject):
    new_log = pyqtSignal(str, str) # msg, level

log_fifo = LogSignal()

def custom_log_handler(message):
    """Глобальный обработчик для перехвата сообщений из loguru и отправки в GUI"""
    try:
        record = message.record
        msg = record["message"]
        
        # Фильтрация мусора и длинных системных ошибок
        if any(x in msg for x in ["Traceback", "stack trace", "ProactorEventLoop"]):
            return
            
        # Упрощение ошибок сети для читаемости в логе
        if "websockets.exceptions" in msg or "1008" in msg:
            msg = "Сеть: Ошибка протокола (возможно цензура фрагмента). Воркер пробует обход."
        elif "1011" in msg or "timeout" in msg.lower():
            msg = "Сеть: Сервер Gemini временно недоступен или занят."

        level = record["level"].name
        
        # Отправка сигнала в GUI (если объект log_fifo создан)
        if 'log_fifo' in globals():
            log_fifo.new_log.emit(msg, level)
            
    except Exception as e:
        # Если что-то пошло не так в самом обработчике, выводим в стандартную консоль
        print(f"Error in custom_log_handler: {e}")

class _GuiLoggingHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = record.getMessage()
            if any(x in msg for x in ["Traceback", "stack trace", "ProactorEventLoop"]):
                return
            if "websockets.exceptions" in msg or "1008" in msg:
                msg = "Сеть: Ошибка протокола (возможно цензура фрагмента). Воркер пробует обход."
            elif "1011" in msg or "timeout" in msg.lower():
                msg = "Сеть: Сервер Gemini временно недоступен или занят."
            log_fifo.new_log.emit(msg, record.levelname)
        except Exception:
            pass


def _run_subprocess(args, **kwargs):
    if WINDOWS_CREATE_NO_WINDOW:
        kwargs.setdefault("creationflags", WINDOWS_CREATE_NO_WINDOW)
    return subprocess.run(args, **kwargs)


def _runtime_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _resolve_tool_path(tool_name):
    direct_path = shutil.which(tool_name)
    if direct_path:
        return direct_path

    candidates = [tool_name]
    if platform.system() == "Windows" and not tool_name.lower().endswith(".exe"):
        candidates.insert(0, f"{tool_name}.exe")

    search_roots = [_runtime_base_dir(), os.getcwd()]
    for root in search_roots:
        for candidate in candidates:
            full_path = os.path.join(root, candidate)
            if os.path.isfile(full_path):
                return full_path
    return None


def _get_app_settings_manager():
    app = QApplication.instance()
    if app is None or not hasattr(app, "get_settings_manager"):
        return None
    try:
        return app.get_settings_manager()
    except Exception:
        return None


def _reader_books_dir(settings_manager=None):
    if settings_manager is not None and getattr(settings_manager, "config_dir", None):
        return os.path.join(settings_manager.config_dir, READER_BOOKS_DIRNAME)
    return "books"


def _load_legacy_settings():
    if not os.path.exists(LEGACY_SETTINGS_FILE):
        return {}
    try:
        with open(LEGACY_SETTINGS_FILE, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj)
    except Exception:
        return {}


def _sentence_tokenize(text):
    fallback_sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|\n+", text)
        if part.strip()
    ]
    if nltk is None:
        return fallback_sentences

    try:
        return nltk.sent_tokenize(text)
    except LookupError:
        try:
            nltk.download("punkt", quiet=True)
            return nltk.sent_tokenize(text)
        except Exception:
            return fallback_sentences
    except Exception:
        return fallback_sentences


if platform.system() == "Windows" and "_orig_popen" in globals():
    subprocess.Popen = _orig_popen

if _loguru_logger is not None:
    logger = _loguru_logger
    logger.remove()
    logger.add(custom_log_handler, level="INFO")
else:
    logger = logging.getLogger("gemini_reader")
    if not getattr(logger, "_gemini_reader_configured", False):
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(_GuiLoggingHandler())
        logger.propagate = False
        logger._gemini_reader_configured = True

# --- ПОДГОТОВКА ТЕКСТА ---
class Chapter:
    def __init__(self, title, content):
        self.title = title
        self.flat_sentences = []
        self.raw_text = ""
        self._parse_content(content)

    def _parse_content(self, content):
        if BeautifulSoup is None:
            raise RuntimeError("Для чтения EPUB требуется пакет beautifulsoup4.")
        soup = BeautifulSoup(content, 'html.parser')
        for s in soup(["script", "style", "title"]):
            s.extract()

        lines =[]
        for el in soup.find_all(['p', 'h1', 'h2', 'h3', 'div', 'li']):
            t = el.get_text().strip()
            if t:
                lines.append(t)

        self.raw_text = "\n".join(lines)
        full_text = re.sub(r'[^\x20-\x7E\u0400-\u04FF\s\.,!\?\-:;\"\'«»—]', '', self.raw_text)

        raw_sentences = _sentence_tokenize(full_text)
            
        merged_sentences =[]
        current_sentence = ""
        
        for s in raw_sentences:
            s = s.strip()
            if not s:
                continue
                
            # Добавляем к текущему буферу
            if current_sentence:
                current_sentence += " " + s
            else:
                current_sentence = s
                
            # Считаем только реальные буквы/цифры (без пробелов, тире, кавычек и точек)
            alnum_count = sum(c.isalnum() for c in current_sentence)
            
            # Если набралось 10 или больше символов — фиксируем как готовое предложение
            if alnum_count >= 10:
                merged_sentences.append(current_sentence)
                current_sentence = ""
                
        # Если в самом конце остался короткий "хвост" (меньше 10 символов)
        if current_sentence:
            if merged_sentences:
                # Приклеиваем его к последнему нормальному предложению
                merged_sentences[-1] += " " + current_sentence
            else:
                # Если во всей главе вообще меньше 10 символов
                merged_sentences.append(current_sentence)
                
        self.flat_sentences = merged_sentences


class BookManager:
    def __init__(self, filepath=None, base_dir=None):
        self.base_dir = base_dir or "books"
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
        self.chapters =[]
        self.title = "Unknown"
        self.book_dir = ""
        self.lock = threading.Lock()
        if filepath:
            self._import_book(filepath)

    def _import_book(self, filepath):
        if epub is None or ebooklib is None:
            raise RuntimeError("Для импорта EPUB требуется пакет ebooklib.")
        filename = os.path.basename(filepath)
        self.title = "".join([c for c in os.path.splitext(filename)[0] if c.isalnum() or c in (' ', '-', '_')]).strip()
        self.book_dir = os.path.join(self.base_dir, self.title)
        
        if not os.path.exists(self.book_dir):
            os.makedirs(self.book_dir)
        
        try:
            shutil.copy(filepath, os.path.join(self.book_dir, filename))
        except Exception as e:
            logger.error(f"Ошибка при копировании файла: {e}")

        try:
            book = epub.read_epub(filepath)
            for item_ref in book.spine:
                item = book.get_item_with_id(item_ref[0])
                # ИЗМЕНЕНИЕ: Улучшенная поддержка файлов .html и .htm, если они не помечены как DOCUMENT
                if item and (item.get_type() == ebooklib.ITEM_DOCUMENT or item.get_name().lower().endswith(('.xhtml', '.html', '.htm'))):
                    chap = Chapter(f"Chapter {len(self.chapters) + 1}", item.get_content())
                    if chap.flat_sentences:
                        self.chapters.append(chap)
        except Exception as e:
            logger.error(f"Epub Error: {e}")

    def get_paths(self):
        return os.path.join(self.book_dir, "progress_v19.json")

    def save_progress(self, c, s):
        with self.lock:
            try:
                with open(self.get_paths(), 'w') as f:
                    json.dump({"chapter": c, "sentence": s}, f)
            except Exception as e:
                print(f"Save progress error: {e}")

    def load_progress(self):
        with self.lock:
            try:
                with open(self.get_paths(), 'r') as f:
                    d = json.load(f)
                    return d.get("chapter", 0), d.get("sentence", 0)
            except:
                return 0, 0

    def is_chapter_done(self, c_idx):
        return os.path.exists(os.path.join(self.book_dir, f"Ch{c_idx + 1}.done"))

    def mark_chapter_done(self, c_idx):
        path = os.path.join(self.book_dir, f"Ch{c_idx + 1}.done")
        with open(path, 'w') as f:
            f.write("done")
            
        # Удаляем маркер переозвучки, если он был, чтобы статус вернулся на "Готово"
        revoice_path = os.path.join(self.book_dir, f"Ch{c_idx + 1}.revoice")
        if os.path.exists(revoice_path):
            os.remove(revoice_path)


    def is_chapter_skipped(self, c_idx):
        return os.path.exists(os.path.join(self.book_dir, f"Ch{c_idx + 1}.skip"))

    def toggle_chapter_skip(self, c_idx):
        path = os.path.join(self.book_dir, f"Ch{c_idx + 1}.skip")
        if os.path.exists(path):
            os.remove(path)
        else:
            with open(path, 'w') as f:
                f.write("skip")




    def get_mp3_path(self, c):
        return os.path.join(self.book_dir, f"Ch{c + 1}.mp3")


# --- УМНЫЙ КОМБАЙН (ПО 1.5 ГБ) ---
class AudioCombinerWorker(QThread):
    finished_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(str)

    def __init__(self, book_manager, voice_name="Unknown", ffmpeg_path=None, ffprobe_path=None):
        super().__init__()
        self.bm = book_manager
        self.voice_name = voice_name
        self.ffmpeg_path = ffmpeg_path or _resolve_tool_path("ffmpeg")
        self.ffprobe_path = ffprobe_path or _resolve_tool_path("ffprobe")

    def run(self):
        try:
            self.progress_signal.emit("Сканирование файлов...")
            files = sorted([f for f in os.listdir(self.bm.book_dir) if f.startswith("Ch") and f.endswith(".mp3") and "Part" not in f],
                           key=lambda x: int(re.search(r'\d+', x).group()))
            
            if not files:
                self.finished_signal.emit("Ошибка: Нет файлов Ch*.mp3")
                return

            parts = []
            current_part_files =[]
            current_size = 0

            for f in files:
                f_path = os.path.join(self.bm.book_dir, f)
                f_size = os.path.getsize(f_path)
                if current_size + f_size > MAX_PART_SIZE and current_part_files:
                    parts.append(current_part_files)
                    current_part_files =[]
                    current_size = 0
                current_part_files.append(f)
                current_size += f_size
            
            if current_part_files:
                parts.append(current_part_files)

            for idx, part_files in enumerate(parts):
                # Добавляем имя голоса в конец файла
                part_name = f"{self.bm.title}_Part_{idx+1}_{self.voice_name}.mp3"
                list_txt_path = os.path.join(self.bm.book_dir, f"concat_list_{idx}.txt")
                meta_txt_path = os.path.join(self.bm.book_dir, f"metadata_{idx}.txt")
                
                # Заготовка для файла метаданных с оглавлением (тоже добавим голос для красоты)
                meta_lines =[";FFMETADATA1", f"title={self.bm.title} - Часть {idx+1} ({self.voice_name})", ""]
                current_time_ms = 0

                with open(list_txt_path, 'w', encoding='utf-8') as lf:
                    for f in part_files:
                        lf.write(f"file '{f}'\n")
                        
                        # --- Узнаем длину файла через ffprobe для ОГЛАВЛЕНИЯ ---
                        f_path = os.path.join(self.bm.book_dir, f)
                        dur_cmd =[self.ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", f_path]
                        dur_sec = 0.0
                        try:
                            res = _run_subprocess(dur_cmd, capture_output=True, text=True)
                            dur_sec = float(res.stdout.strip())
                        except Exception as e:
                            logger.error(f"Не удалось получить длину {f}: {e}")
                            
                        dur_ms = int(dur_sec * 1000)
                        
                        # Достаем номер главы из имени файла
                        ch_match = re.search(r'Ch(\d+)\.mp3', f)
                        ch_num = ch_match.group(1) if ch_match else "?"
                        
                        meta_lines.append("[CHAPTER]")
                        meta_lines.append("TIMEBASE=1/1000")
                        meta_lines.append(f"START={current_time_ms}")
                        meta_lines.append(f"END={current_time_ms + dur_ms}")
                        meta_lines.append(f"title=Глава {ch_num}")
                        meta_lines.append("")
                        
                        current_time_ms += dur_ms

                # Сохраняем файл метаданных
                with open(meta_txt_path, 'w', encoding='utf-8') as mf:
                    mf.write("\n".join(meta_lines))

                self.progress_signal.emit(f"Склейка части {idx+1} из {len(parts)}...")
                
                # Добавляем инжект метаданных (-i metadata.txt -map_metadata 1)
                cmd =[self.ffmpeg_path, "-y", "-f", "concat", "-safe", "0", "-i", f"concat_list_{idx}.txt", "-i", f"metadata_{idx}.txt", "-map_metadata", "1", "-c", "copy", part_name]
                
                process = _run_subprocess(cmd, cwd=self.bm.book_dir, capture_output=True, text=True)
                
                if os.path.exists(list_txt_path):
                    os.remove(list_txt_path)
                if os.path.exists(meta_txt_path):
                    os.remove(meta_txt_path)
                    
                if process.returncode != 0:
                    logger.error(f"FFMPEG Error Part {idx+1}: {process.stderr}")

            self.finished_signal.emit(f"Готово! Создано частей: {len(parts)}")

        except Exception as e:
            logger.exception(f"Критическая ошибка при объединении аудио: {e}")
            self.finished_signal.emit(f"Ошибка: {str(e)}")


# --- АУДИО ПЛЕЕР ---
class AudioPlayer(QThread):
    def __init__(self, audio_queue, vol=80):
        super().__init__()
        if pyaudio is None:
            raise RuntimeError("Для live-воспроизведения требуется PyAudio.")
        self.audio_queue = audio_queue
        self.is_running = True
        self.vol = float(vol) / 100.0
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            output=True
        )

    def set_volume(self, v):
        self.vol = float(v) / 100.0

    def run(self):
        while self.is_running:
            try:
                try:
                    item = self.audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is None:
                    break
                data, c, s, ui = item
                if data:
                    arr = array.array('h', data)
                    if self.vol < 1.0:
                        for i in range(len(arr)):
                            arr[i] = max(min(int(arr[i] * self.vol), 32767), -32768)
                    self.stream.write(arr.tobytes())
                self.audio_queue.task_done()
            except Exception as e:
                continue

    def stop(self):
        self.is_running = False
        try:
            self.stream.stop_stream()
            self.stream.close()
            self.p.terminate()
        except:
            pass


# --- ВОРКЕР (С ПОДМЕНОЙ EDGE TTS) ---
class GeminiWorker(QThread):
    change_chapter_signal = pyqtSignal(int)
    worker_progress = pyqtSignal(int, int, int, int) 
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)
    error_signal = pyqtSignal(int, str)
    chapter_done_ui_signal = pyqtSignal(int)

    def __init__(self, worker_id, api_key, bm, audio_queue, model_id, voice, style_prompt, speed, record, fast, chunk, manager_chapter_queue):
        super().__init__()
        self.worker_id = worker_id
        self.api_key = api_key
        self.bm = bm
        self.audio_queue = audio_queue
        self.model_id = model_id  # СОХРАНЯЕМ ВЫБРАННУЮ МОДЕЛЬ
        self.voice = voice
        self.style_prompt = style_prompt
        self.speed = speed
        self.record = record
        self.fast = fast
        self.chunk = chunk
        self.manager_chapter_queue = manager_chapter_queue
        self.c_idx = -1
        self.s_idx = 0
        self._is_running = True
        self._is_paused = False
        self.audio_chunks = []
        self.buffer_lock = threading.Lock()

    def update_chunk_size(self, v): self.chunk = v

    def run(self):
        try:
            asyncio.run(self.main_loop())
        except Exception as e:
            self.error_signal.emit(self.worker_id, f"CRASH: {str(e)}")

    async def get_edge_tts_fallback(self, text):
        """Озвучка забаненного текста через Edge TTS с умным выбором пола"""
        import random
        if edge_tts is None or AudioSegment is None:
            logger.warning(f"[W{self.worker_id}] Edge TTS fallback недоступен: нет edge-tts или pydub.")
            return None
        # Разгружаем сервера: небольшая случайная пауза перед запросом
        await asyncio.sleep(random.uniform(0.5, 2.0))
        
        # --- УМНЫЙ ВЫБОР ГОЛОСА EDGE TTS ПО ПОЛУ ---
        gender = VOICES_MAP.get(self.voice, "Ж")
        edge_voice = "ru-RU-DmitryNeural" if gender == "М" else "ru-RU-SvetlanaNeural"
        
        try:
            # 1. Мягкая очистка (заменяем тире и многоточия, которые ломают серверы Microsoft)
            clean_text = text.replace("…", ".").replace("—", "").replace("«", "").replace("»", "")
            clean_text = re.sub(r'[\n\r\t]', ' ', clean_text).strip()
            
            if not clean_text:
                return None

            # Обязательно добавляем точку в конце, чтобы генератор плавно завершил звук
            clean_text += " ."

            data_out = b""
            
            # Делаем 3 попытки, если Microsoft банит по лимитам запросов
            for attempt in range(3):
                data_out = b""
                try:
                    communicate = edge_tts.Communicate(clean_text, edge_voice)
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            data_out += chunk["data"]
                            
                    if len(data_out) > 100:
                        break # Успех! Выходим из цикла попыток
                except Exception as stream_err:
                    logger.warning(f"[W{self.worker_id}] Ошибка потока Edge TTS (попытка {attempt+1}): {stream_err}")
                
                # Если ответ пустой, ждем и делаем более агрессивную очистку
                if len(data_out) <= 100:
                    logger.warning(f"[W{self.worker_id}] Edge TTS вернул пустой звук. Повтор через {attempt+2} сек...")
                    await asyncio.sleep(attempt + 2)
                    # 2. Агрессивная очистка: оставляем только буквы, цифры и базовую пунктуацию
                    clean_text = re.sub(r'[^\w\sа-яА-ЯёЁ.,?!]', ' ', text).strip() + " ."

            # Если после всех попыток данных нет
            if not data_out or len(data_out) < 100:
                logger.error(f"[W{self.worker_id}] Edge TTS не выдал звук. Возможно, жесткий фильтр Microsoft.")
                return None

            def _decode_silently():
                import subprocess
                startupinfo = None
                # Скрываем всплывающие окна консоли на Windows
                if platform.system() == "Windows":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 0 # SW_HIDE

                fp = io.BytesIO(data_out)
                try:
                    # Конвертируем ответ в нужный нам формат
                    segment = AudioSegment.from_file(fp, format="mp3")
                    segment = segment.set_frame_rate(AUDIO_RATE).set_channels(AUDIO_CHANNELS).set_sample_width(2)
                    return segment.raw_data
                except Exception as e:
                    logger.error(f"[W{self.worker_id}] Ошибка pydub при декодировании Edge TTS: {e}")
                    return None

            return await asyncio.to_thread(_decode_silently)

        except Exception as e:
            logger.error(f"[W{self.worker_id}] Критическая ошибка Edge TTS: {e}")
            return None

    async def main_loop(self):
        
        # Обновленный клиент (новый SDK обрабатывает Live API без костылей v1alpha)
        if genai is None or genai_types is None:
            raise RuntimeError("Для озвучки Gemini Reader требуется пакет google-genai.")
        client = genai.Client(api_key=self.api_key)
        
        speed_instr = SPEED_PROMPTS.get(self.speed, SPEED_PROMPTS["Normal"])
        
        # --- УМНЫЙ ПРОМПТ В ЗАВИСИМОСТИ ОТ ПОЛА ---
        gender = VOICES_MAP.get(self.voice, "Ж")
        dictor_type = "диктор-мужчина" if gender == "М" else "диктор-женщина"
        
        sys_instr = (
            f"Ты профессиональный {dictor_type}. {self.style_prompt} "
            f"{speed_instr} "
            f"Твоя задача — озвучить предоставленный текст СЛОВО В СЛОВО. "
            f"ОБЯЗАТЕЛЬНО дочитывай текст до самой последней точки, не глотай окончания и последние слова. "
            f"Выдай полный аудиоответ. Язык: Русский."
        )
        
        config = genai_types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=genai_types.SpeechConfig(
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(voice_name=self.voice)
                )
            ),
            system_instruction=sys_instr
        )

        # Используем выбранную в UI модель
        logger.info(f"Воркер {self.worker_id} запущен (Модель: {self.model_id}, Голос: {self.voice} [{gender}]).")
        await asyncio.sleep(self.worker_id * 1.5)

        total_sent = 0
        single_sentence_mode_remaining = 0
        fail_count = 0 
        gemini_retry_count = 0 # Счетчик для повторных попыток пробиться к Gemini (одиночные)
        batch_retry_count = 0  # НОВЫЙ СЧЕТЧИК: для повторных попыток целого батча

        while self._is_running:
            if self.c_idx == -1:
                try:
                    self.c_idx = self.manager_chapter_queue.get_nowait()
                    self.s_idx = 0 
                    single_sentence_mode_remaining = 0
                    fail_count = 0
                    gemini_retry_count = 0
                    batch_retry_count = 0
                    total_sent = len(self.bm.chapters[self.c_idx].flat_sentences)
                    logger.info(f"Воркер {self.worker_id} взял Главу {self.c_idx + 1} ({total_sent} предл.)")
                except queue.Empty:
                    self.finished_signal.emit(self.worker_id)
                    break
            else:
                total_sent = len(self.bm.chapters[self.c_idx].flat_sentences)

            if self.bm.is_chapter_done(self.c_idx):
                self.chapter_done_ui_signal.emit(self.c_idx)
                self.c_idx = -1
                continue

            if self.s_idx >= total_sent:
                logger.info(f"Воркер {self.worker_id} сохраняет и завершает Главу {self.c_idx + 1}")
                await self.save_file(final=True)
                self.bm.mark_chapter_done(self.c_idx)
                self.chapter_done_ui_signal.emit(self.c_idx)
                if self.worker_id == 0: 
                    self.change_chapter_signal.emit(self.c_idx)
                self.c_idx = -1
                continue

            if total_sent > 0:
                self.worker_progress.emit(self.worker_id, self.c_idx, self.s_idx, total_sent)

            current_chunk_size = 1 if single_sentence_mode_remaining > 0 else self.chunk
            
            text_to_send = ""
            actual_count = 0
            end_range = min(self.s_idx + current_chunk_size, total_sent)
            
            for i in range(self.s_idx, end_range):
                text_to_send += self.bm.chapters[self.c_idx].flat_sentences[i] + " "
                actual_count += 1
            
            text_to_send = text_to_send.strip()
            if not text_to_send:
                self.s_idx += 1
                if single_sentence_mode_remaining > 0:
                    single_sentence_mode_remaining -= 1
                continue

            data_received = False
            try:
                # ПОДКЛЮЧАЕМСЯ К ВЫБРАННОЙ МОДЕЛИ
                async with client.aio.live.connect(model=self.model_id, config=config) as session:
                    await session.send_realtime_input(text=text_to_send)
                    
                    receive_iterator = session.receive().__aiter__()
                    current_timeout = 30.0 # ИЗМЕНЕНО: Даем больше времени на генерацию первого куска
                    
                    while self._is_running:
                        try:
                            response = await asyncio.wait_for(receive_iterator.__anext__(), timeout=current_timeout)
                            current_timeout = 15.0 # ИЗМЕНЕНО: Увеличено с 4.0 до 15.0 сек, чтобы не обрывало на паузах в речи
                            
                            if response.server_content:
                                if response.server_content.model_turn:
                                    for part in response.server_content.model_turn.parts:
                                        if part.inline_data:
                                            data = part.inline_data.data
                                            data_received = True
                                            if self.record:
                                                with self.buffer_lock: 
                                                    self.audio_chunks.append(data)
                                            if self.audio_queue and not self.fast:
                                                self.audio_queue.put((data, self.c_idx, self.s_idx, False))
                                                
                                # ИЗМЕНЕНО: Проверка официального флага завершения ответа от серверов Gemini
                                if getattr(response.server_content, "turn_complete", False):
                                    break
                                            
                        except asyncio.TimeoutError:
                            break
                        except StopAsyncIteration:
                            break
            except Exception as e:
                # Ошибки сети или внезапные разрывы логируем, чтобы не было "тихих" провалов
                logger.debug(f"[W{self.worker_id}] Внутренняя ошибка сессии Gemini: {e}")

            if not data_received and self._is_running:
                if current_chunk_size > 1:
                    # ДАЕМ БАТЧУ 3 ПОПЫТКИ ПЕРЕД ДРОБЛЕНИЕМ НА ОДИНОЧНЫЕ ПРЕДЛОЖЕНИЯ
                    if batch_retry_count < 3:
                        batch_retry_count += 1
                        logger.warning(f"[W{self.worker_id}] Ошибка API Gemini (батч). Попытка {batch_retry_count}/3 для батча: '{text_to_send[:30]}...'")
                        await asyncio.sleep(2)
                        continue # Возвращаемся в начало и пробуем этот же батч целиком
                    else:
                        logger.warning(f"[W{self.worker_id}] Батч не прошел после 3 попыток! Дробим {actual_count} предл. по одному...")
                        single_sentence_mode_remaining = actual_count
                        fail_count = 0 
                        gemini_retry_count = 0 
                        batch_retry_count = 0
                        continue
                else:
                    # ДАЕМ ОДИНОЧНОМУ ПРЕДЛОЖЕНИЮ 3 ПОПЫТКИ ПЕРЕД EDGE TTS
                    if gemini_retry_count < 3:
                        gemini_retry_count += 1
                        logger.warning(f"[W{self.worker_id}] Ошибка API Gemini (одиночное). Попытка {gemini_retry_count}/3 для: '{text_to_send[:30]}...'")
                        await asyncio.sleep(2)
                        continue
                    else:
                        logger.warning(f"[W{self.worker_id}] Gemini сдался после 3 попыток. Озвучка Edge TTS: '{text_to_send[:30]}...'")
                        fallback_data = await self.get_edge_tts_fallback(text_to_send)
                        if fallback_data:
                            if self.record:
                                with self.buffer_lock: 
                                    self.audio_chunks.append(fallback_data)
                            if self.audio_queue and not self.fast:
                                self.audio_queue.put((fallback_data, self.c_idx, self.s_idx, False))
                            data_received = True

            # УСПЕХ ИЛИ ПРОПУСК
            if data_received:
                fail_count = 0 
                gemini_retry_count = 0 
                batch_retry_count = 0 # Сбрасываем все счетчики ошибок при успехе
                self.s_idx = min(self.s_idx + actual_count, total_sent)
                
                if single_sentence_mode_remaining > 0:
                    single_sentence_mode_remaining -= actual_count
                    
                self.worker_progress.emit(self.worker_id, self.c_idx, self.s_idx, total_sent)
                
                if self.worker_id == 0: 
                    self.bm.save_progress(self.c_idx, self.s_idx)
                
                if self.record and self.s_idx % 5 == 0: 
                    await self.save_file()
            else:
                # Сюда программа дойдет только если даже Edge TTS не смог сгенерировать звук
                fail_count += 1
                if fail_count >= 3:
                    logger.error(f"[W{self.worker_id}] Пропуск предложения после неудач Edge TTS: '{text_to_send[:30]}...'")
                    self.s_idx = min(self.s_idx + actual_count, total_sent)
                    fail_count = 0
                    gemini_retry_count = 0
                    batch_retry_count = 0
                    if single_sentence_mode_remaining > 0:
                        single_sentence_mode_remaining -= actual_count
                else:
                    logger.error(f"[W{self.worker_id}] Ошибка Edge TTS. Попытка {fail_count}/3. Пауза 5 сек...")
                    await asyncio.sleep(5)




    async def save_file(self, final=False):
        path = self.bm.get_mp3_path(self.c_idx)
        with self.buffer_lock:
            if not self.audio_chunks: return
            combined_data = b"".join(self.audio_chunks)
            if final: self.audio_chunks =[]
        
        def _exp():
            if AudioSegment is None:
                logger.error(f"[W{self.worker_id}] Невозможно сохранить MP3: не найден pydub.")
                return
            try:
                snap = AudioSegment(data=combined_data, sample_width=2, frame_rate=AUDIO_RATE, channels=AUDIO_CHANNELS)
                snap.export(path, format="mp3")
            except: pass
        await asyncio.to_thread(_exp)

    def pause(self): self._is_paused = not self._is_paused
    def stop(self): self._is_running = False


# --- UI КОМПОНЕНТЫ ---
# --- UI КОМПОНЕНТЫ ---
class DashboardRow(QWidget):
    def __init__(self, w_id):
        super().__init__()
        self.w_id = w_id
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        self.lbl_info = QLabel(f"[W{w_id}] Ожидание...")
        self.lbl_info.setFixedWidth(160)
        self.lbl_info.setStyleSheet("font-weight: bold; font-size: 14px;")
        
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 100)
        self.pbar.setValue(0)
        # ОТКЛЮЧАЕМ текст внутри самой полоски, чтобы небыло "96% 96%"
        self.pbar.setTextVisible(False) 
        self.pbar.setFixedHeight(20)
        
        self.lbl_status = QLabel("--")
        self.lbl_status.setFixedWidth(100)
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_status.setStyleSheet("font-size: 14px;")
        
        layout.addWidget(self.lbl_info)
        layout.addWidget(self.pbar)
        layout.addWidget(self.lbl_status)

    def update_progress(self, w_id, c_idx, s_idx, total_s):
        """Обновление прогресс-бара с правильным приемом аргументов от сигнала"""
        if total_s <= 0:
            pct_chap = 0
        else:
            # Ограничиваем s_idx, чтобы он не превышал total_s (защита от >100%)
            current_s = min(s_idx, total_s)
            pct_chap = int((current_s / total_s) * 100)
        
        self.lbl_info.setText(f"[W{w_id}] Глава {c_idx+1}")
        self.lbl_status.setText(f"{pct_chap}%")
        self.lbl_status.setStyleSheet("font-size: 14px; font-weight: bold;")
        
        # Устанавливаем значение прогресс-бара
        self.pbar.setValue(pct_chap)

    def set_finished(self):
        self.lbl_status.setText("ГОТОВО")
        self.pbar.setValue(100)
        self.hide()

class ApiKeysDialog(QDialog):
    def __init__(self, keys_str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API Ключи")
        self.resize(400, 300)
        layout = QVBoxLayout(self)
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(keys_str)
        layout.addWidget(QLabel("Введите ключи (каждый с новой строки):"))
        layout.addWidget(self.text_edit)
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn.accepted.connect(self.accept)
        btn.rejected.connect(self.reject)
        layout.addWidget(btn)
    def get_keys(self):
        return [k.strip() for k in self.text_edit.toPlainText().split('\n') if k.strip()]


# --- ТЕСТОВЫЙ ВОСПРОИЗВОДИТЕЛЬ ГОЛОСА ---
class VoiceSampleWorker(QThread):
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, api_key, model_id, voice):
        super().__init__()
        self.api_key = api_key
        self.model_id = model_id
        self.voice = voice
        # Сложное предложение с шипящими для проверки интонации и дикции голоса
        self.test_text = "Проверка голоса Озвучь сделующую скороговорку. Саша шустро сушила сушки на шоссе, а жужжащая жужелица жадно жевала жёлтый жёлудь. Раз, два, три."

    def run(self):
        try:
            asyncio.run(self.get_and_play())
        except Exception as e:
            self.error_signal.emit(str(e))
        finally:
            self.finished_signal.emit()

    async def get_and_play(self):
        if genai is None or genai_types is None:
            raise RuntimeError("Для теста голоса требуется пакет google-genai.")

        client = genai.Client(api_key=self.api_key)
        
        config = genai_types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=genai_types.SpeechConfig(
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(voice_name=self.voice)
                )
            ),
            system_instruction="Ты диктор."
        )

        audio_data = b""
        
        async with client.aio.live.connect(model=self.model_id, config=config) as session:
            await session.send_realtime_input(text=self.test_text)
            receive_iterator = session.receive().__aiter__()
            
            current_timeout = 20.0 # Время на первый ответ
            
            while True:
                try:
                    response = await asyncio.wait_for(receive_iterator.__anext__(), timeout=current_timeout)
                    current_timeout = 10.0 # Безопасное время между чанками аудио
                    
                    if response.server_content:
                        if response.server_content.model_turn:
                            for part in response.server_content.model_turn.parts:
                                if part.inline_data:
                                    audio_data += part.inline_data.data
                                    
                        # Правильное завершение по сигналу от сервера
                        if getattr(response.server_content, "turn_complete", False):
                            break
                            
                except asyncio.TimeoutError:
                    break
                except StopAsyncIteration:
                    break
                    
        if audio_data:
            self.play_audio(audio_data)

    def play_audio(self, data):
        if pyaudio is None:
            raise RuntimeError("Для воспроизведения теста голоса требуется PyAudio.")
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            output=True
        )
        
        arr = array.array('h', data)
        vol = 0.5 # Жестко фиксируем громкость на 50%
        for i in range(len(arr)):
            arr[i] = max(min(int(arr[i] * vol), 32767), -32768)
            
        stream.write(arr.tobytes())
        stream.stop_stream()
        stream.close()
        p.terminate()




# --- ОСНОВНОЕ ОКНО ---
class MainWindow(QMainWindow):
    def __init__(self, settings_manager=None):
        super().__init__()
        self.setWindowTitle("Gemini Reader v21 + Edge Fallback")
        self.resize(1150, 850)
        self.setAcceptDrops(True)
        self.settings_manager = settings_manager or _get_app_settings_manager()
        self.reader_books_dir = _reader_books_dir(self.settings_manager)
        self.bm = None
        self.workers = []
        self.api_keys = []
        self.audio_queue = queue.Queue(maxsize=100)
        self.player = None
        self.combiner = None
        self.tester_worker = None
        self.worker_widgets = {}
        self._return_to_menu_handler = None
        self._returning_to_main_menu = False
        self.init_ui()
        self.load_settings()
        self.apply_runtime_capabilities()
        log_fifo.new_log.connect(self.add_log_to_ui)

    def init_ui(self):
        # Toolbar
        toolbar = QToolBar()
        self.addToolBar(toolbar)
        self.act_return_to_menu = QAction("← В меню", self)
        self.act_return_to_menu.triggered.connect(self._return_to_menu)
        toolbar.addAction(self.act_return_to_menu)
        toolbar.addSeparator()
        act_key = QAction("🔑 Ключи API", self)
        act_key.triggered.connect(self.set_api_keys)
        toolbar.addAction(act_key)
        
        act_open = QAction("📂 Открыть EPUB", self)
        act_open.triggered.connect(self.open_book_dialog)
        toolbar.addAction(act_open)
        
        self.lbl_info = QLabel("Перетащите EPUB файл")
        toolbar.addSeparator()
        toolbar.addWidget(self.lbl_info)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Лево: Список глав
        self.list_chapters = QListWidget()
        self.list_chapters.setFixedWidth(250)
        
        # Включаем множественное выделение (через Shift и Ctrl)
        self.list_chapters.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_chapters.itemClicked.connect(self.on_chapter_clicked)
        
        # --- ВКЛЮЧАЕМ КОНТЕКСТНОЕ МЕНЮ (ПРАВАЯ КНОПКА МЫШИ) ---
        self.list_chapters.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_chapters.customContextMenuRequested.connect(self.show_chapter_context_menu)
        
        splitter.addWidget(self.list_chapters)

        # Право: Табы
        self.tabs = QTabWidget()
        
        # Таб 1: Текст
        self.txt_view = QTextEdit()
        self.txt_view.setReadOnly(True)
        self.tabs.addTab(self.txt_view, "📖 Текст")

        # Таб 2: Дашборд
        self.scroll_dash = QScrollArea()
        self.scroll_dash.setWidgetResizable(True)
        self.dash_content = QWidget()
        self.dash_layout = QVBoxLayout(self.dash_content)
        self.dash_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_dash.setWidget(self.dash_content)
        self.tabs.addTab(self.scroll_dash, "📊 Воркеры")

        # Таб 3: Лог
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("background-color: #1e1e1e; color: #ececec; font-family: Consolas; font-size: 13px;")
        self.tabs.addTab(self.log_view, "📋 Лог")

        splitter.addWidget(self.tabs)
        main_layout.addWidget(splitter)

        # Контролы
        controls = QHBoxLayout()
        
        self.models_map = {
            "Gemini 3.1 Flash (Новая)": "gemini-3.1-flash-live-preview",
            "Gemini 2.5 Flash (Старая)": "gemini-2.5-flash-native-audio-preview-12-2025"
        }
        self.combo_model = QComboBox()
        self.combo_model.addItems(list(self.models_map.keys()))
        self.combo_model.currentTextChanged.connect(self.save_settings)
        controls.addWidget(QLabel("Модель:"))
        controls.addWidget(self.combo_model)

        # ИЗМЕНЕНИЕ: Заполняем голоса с привязкой "Отображаемое имя -> Скрытый ID"
        self.combo_voices = QComboBox()
        for voice_id, gender in VOICES_MAP.items():
            self.combo_voices.addItem(f"{voice_id} ({gender})", voice_id)
            
        self.combo_voices.currentIndexChanged.connect(self.save_settings)
        controls.addWidget(QLabel("Голос:"))
        controls.addWidget(self.combo_voices)
        
        self.btn_test_voice = QPushButton("🔊 Плей")
        self.btn_test_voice.setFixedSize(80, 25)
        self.btn_test_voice.clicked.connect(self.test_voice_sample)
        controls.addWidget(self.btn_test_voice)
        
        self.combo_speed = QComboBox()
        self.combo_speed.addItems(list(SPEED_PROMPTS.keys()))
        self.combo_speed.setCurrentText("Normal")
        self.combo_speed.currentTextChanged.connect(self.save_settings)
        controls.addWidget(QLabel("Скорость:"))
        controls.addWidget(self.combo_speed)
        
        self.spin_chunk = QSpinBox()
        self.spin_chunk.setRange(1, 50)
        self.spin_chunk.setValue(2)
        self.spin_chunk.valueChanged.connect(self.save_settings)
        controls.addWidget(QLabel("Батч:"))
        controls.addWidget(self.spin_chunk)

        self.btn_play = QPushButton("▶ СТАРТ")
        self.btn_play.setFixedSize(90, 40)
        self.btn_play.clicked.connect(self.toggle_play)
        controls.addWidget(self.btn_play)

        self.btn_stop = QPushButton("⏹ СТОП")
        self.btn_stop.setFixedSize(90, 40)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.force_stop)
        controls.addWidget(self.btn_stop)

        opts = QVBoxLayout()
        self.chk_mp3 = QCheckBox("Запись MP3")
        self.chk_mp3.setChecked(True)
        self.chk_mp3.stateChanged.connect(self.save_settings)
        self.chk_fast = QCheckBox("Только экспорт")
        self.chk_fast.stateChanged.connect(self.save_settings)
        opts.addWidget(self.chk_mp3)
        opts.addWidget(self.chk_fast)
        controls.addLayout(opts)

        # Секция утилит
        utils_layout = QVBoxLayout()
        self.btn_clean_stuck = QPushButton("🧹 Очистить зависшие")
        self.btn_clean_stuck.setFixedSize(140, 30)
        self.btn_clean_stuck.setStyleSheet("background-color: #ffe0b2;")
        self.btn_clean_stuck.clicked.connect(self.clean_stuck_files)
        utils_layout.addWidget(self.btn_clean_stuck)

        self.btn_combine = QPushButton("🧩 Склеить MP3")
        self.btn_combine.setFixedSize(140, 30)
        self.btn_combine.clicked.connect(self.run_combine)
        utils_layout.addWidget(self.btn_combine)
        controls.addLayout(utils_layout)

        main_layout.addLayout(controls)

    def _running_tasks_exist(self):
        active_workers = any(getattr(worker, "isRunning", lambda: False)() for worker in self.workers)
        combiner_running = bool(self.combiner and self.combiner.isRunning())
        tester_running = bool(self.tester_worker and self.tester_worker.isRunning())
        return active_workers or combiner_running or tester_running

    def _set_reading_controls_running(self, running):
        if running:
            self.btn_play.setText("⏳ ИДЁТ")
            self.btn_play.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.btn_clean_stuck.setEnabled(False)
            return

        self.btn_play.setText("▶ СТАРТ")
        self.btn_play.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_clean_stuck.setEnabled(True)

    def set_return_to_menu_handler(self, handler):
        self._return_to_menu_handler = handler

    def _return_to_menu(self):
        self.save_settings()
        self._returning_to_main_menu = True
        self.close()

    def apply_runtime_capabilities(self):
        runtime_notes = []

        if genai is None or genai_types is None:
            self.btn_play.setEnabled(False)
            self.btn_test_voice.setEnabled(False)
            runtime_notes.append("нет google-genai: озвучка Gemini недоступна")

        if pyaudio is None:
            self.btn_test_voice.setEnabled(False)
            runtime_notes.append("нет PyAudio: live-воспроизведение отключено")

        if AudioSegment is None:
            self.chk_mp3.setChecked(False)
            self.chk_mp3.setEnabled(False)
            self.btn_combine.setEnabled(False)
            runtime_notes.append("нет pydub: MP3-экспорт отключён")

        if _resolve_tool_path("ffmpeg") is None or _resolve_tool_path("ffprobe") is None:
            self.btn_combine.setEnabled(False)
            runtime_notes.append("нет ffmpeg/ffprobe: склейка MP3 недоступна")

        if runtime_notes:
            self.statusBar().showMessage(" | ".join(runtime_notes))

    def _reader_ui_settings_payload(self):
        return {
            "model": self.combo_model.currentText(),
            "voice": self.combo_voices.currentData(),
            "speed": self.combo_speed.currentText(),
            "chunk": self.spin_chunk.value(),
            "record": self.chk_mp3.isChecked(),
            "fast": self.chk_fast.isChecked(),
        }

    def closeEvent(self, event):
        if self._running_tasks_exist():
            QMessageBox.warning(self, "Подождите", "Сначала остановите генерацию или дождитесь завершения фоновых задач.")
            event.ignore()
            return

        self.save_settings()

        if self._returning_to_main_menu:
            if callable(self._return_to_menu_handler):
                self._return_to_menu_handler()
            event.accept()
            return

        try:
            from gemini_translator.ui.dialogs.menu_utils import prompt_return_to_menu, return_to_main_menu
        except Exception:
            event.accept()
            return

        action = prompt_return_to_menu(self)
        if action == "cancel":
            event.ignore()
            return
        if action == "menu":
            if callable(self._return_to_menu_handler):
                self._return_to_menu_handler()
            else:
                return_to_main_menu()
        event.accept()



    def test_voice_sample(self):
        if genai is None or genai_types is None:
            QMessageBox.warning(self, "Зависимости", "Для теста голоса требуется пакет google-genai.")
            return
        if pyaudio is None:
            QMessageBox.warning(self, "Зависимости", "Для теста голоса требуется PyAudio.")
            return
        if not self.api_keys:
            self.set_api_keys()
            if not self.api_keys: return
            
        self.btn_test_voice.setEnabled(False)
        self.btn_test_voice.setText("⏳ Загрузка...")
        
        api_key = self.api_keys[0]
        selected_model_name = self.combo_model.currentText()
        actual_model_id = self.models_map.get(selected_model_name, "gemini-3.1-flash-live-preview")
        voice = self.combo_voices.currentData() # Берем чистый ID голоса
        
        # Создаем и запускаем независимый мини-воркер
        self.tester_worker = VoiceSampleWorker(api_key, actual_model_id, voice)
        
        def on_test_finish():
            self.btn_test_voice.setEnabled(True)
            self.btn_test_voice.setText("🔊 Плей")
            
        def on_test_error(err):
            QMessageBox.warning(self, "Ошибка теста", f"Не удалось получить голос от серверов Google:\n{err}")
            self.btn_test_voice.setEnabled(True)
            self.btn_test_voice.setText("🔊 Плей")
            
        self.tester_worker.finished_signal.connect(on_test_finish)
        self.tester_worker.error_signal.connect(on_test_error)
        self.tester_worker.start()



    def revoice_selected_chapters(self, selected_items):
        if not self.bm:
            return
            
        for item in selected_items:
            idx = item.data(Qt.ItemDataRole.UserRole)
            
            # Удаляем маркер "Готово"
            done_path = os.path.join(self.bm.book_dir, f"Ch{idx + 1}.done")
            if os.path.exists(done_path):
                os.remove(done_path)
                
            # Удаляем маркер "Пропуск"
            skip_path = os.path.join(self.bm.book_dir, f"Ch{idx + 1}.skip")
            if os.path.exists(skip_path):
                os.remove(skip_path)
                
            # Ставим системную метку утилизации/переозвучки
            revoice_path = os.path.join(self.bm.book_dir, f"Ch{idx + 1}.revoice")
            with open(revoice_path, 'w') as f:
                f.write("revoice")
                
        self.refresh_chapters_list()





    def clean_stuck_files(self):
        if not self.bm:
            QMessageBox.warning(self, "Внимание", "Сначала откройте книгу!")
            return
        if self.workers:
            QMessageBox.warning(self, "Внимание", "Остановите процесс генерации перед очисткой!")
            return

        deleted_count = 0
        for i in range(len(self.bm.chapters)):
            mp3_p = self.bm.get_mp3_path(i)
            done_p = os.path.join(self.bm.book_dir, f"Ch{i + 1}.done")
            
            # Если есть MP3, но нет .done -> файл недописан (завис)
            if os.path.exists(mp3_p) and not os.path.exists(done_p):
                try:
                    os.remove(mp3_p)
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"Не удалось удалить {mp3_p}: {e}")
                    
        QMessageBox.information(self, "Очистка", f"Очистка завершена.\nУдалено незаконченных файлов: {deleted_count}")
        self.refresh_chapters_list()




    def add_log_to_ui(self, msg, level):
        """Добавляет запись в текстовое поле лога с цветовым выделением"""
        if not hasattr(self, 'log_view'):
            return

        # Цвета для разных уровней лога
        colors = {
            "DEBUG": "#757575",    # Серый
            "INFO": "#ffffff",     # Белый
            "SUCCESS": "#a5d6a7",  # Светло-зеленый
            "WARNING": "#ffcc80",  # Оранжевый
            "ERROR": "#ef9a9a",    # Красный
            "CRITICAL": "#ff5252"  # Ярко-красный
        }
        color = colors.get(level, "#ececec")
        
        # Очистка сообщения от символов, которые могут мешать HTML-разметке
        import html
        safe_msg = html.escape(msg)
        
        # Формирование строки лога
        timestamp = time.strftime("%H:%M:%S")
        log_html = f"<div style='margin-bottom: 2px;'><span style='color: #888888;'>[{timestamp}]</span> " \
                   f"<b style='color: {color};'>[{level}]</b> {safe_msg}</div>"
        
        # Вставка в конец
        self.log_view.appendHtml(log_html)
        
        # Автопрокрутка вниз
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.accept()
        else: e.ignore()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith('.epub'):
                self.load_book(path)
                break

    def load_book(self, path):
        try:
            self.bm = BookManager(path, base_dir=self.reader_books_dir)
            self.lbl_info.setText(f"📘 {self.bm.title}")
            self.refresh_chapters_list()
            self.tabs.setCurrentIndex(0)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def refresh_chapters_list(self):
        self.list_chapters.clear()
        for i, chap in enumerate(self.bm.chapters):
            item = QListWidgetItem(chap.title)
            item.setData(Qt.ItemDataRole.UserRole, i)
            
            # Если глава пропущена
            if self.bm.is_chapter_skipped(i):
                item.setText(f"❌ {chap.title} (Пропуск)")
                item.setForeground(QColor("#B0BEC5")) # Серый цвет
                font = item.font()
                font.setStrikeOut(True) # Зачеркивание
                item.setFont(font)
            # Если глава уже озвучена
            elif self.bm.is_chapter_done(i):
                item.setText(f"✅ {chap.title}")
                item.setForeground(QColor("#4CAF50"))
                
            self.list_chapters.addItem(item)

    def show_chapter_context_menu(self, pos):
        if not self.bm:
            return
            
        selected_items = self.list_chapters.selectedItems()
        item_at_pos = self.list_chapters.itemAt(pos)
        
        # Умная обработка клика (как в Windows Explorer)
        if not selected_items:
            if not item_at_pos:
                return
            selected_items = [item_at_pos]
            item_at_pos.setSelected(True)
        elif item_at_pos and item_at_pos not in selected_items:
            self.list_chapters.clearSelection()
            selected_items =[item_at_pos]
            item_at_pos.setSelected(True)
            
        menu = QMenu(self)
        
        # Действие: Переозвучить группу
        revoice_action = QAction("♻️ Переозвучить выбранные", self)
        revoice_action.triggered.connect(lambda: self.revoice_selected_chapters(selected_items))
        menu.addAction(revoice_action)
        
        menu.addSeparator()
        
        # Действие: Пропустить группу
        skip_action = QAction("❌ Пропустить / Включить выбранные", self)
        skip_action.triggered.connect(lambda: self.toggle_skip_chapter(selected_items))
        menu.addAction(skip_action)
        
        menu.exec(self.list_chapters.mapToGlobal(pos))

    def toggle_skip_chapter(self, selected_items):
        if not self.bm:
            return
            
        for item in selected_items:
            idx = item.data(Qt.ItemDataRole.UserRole)
            self.bm.toggle_chapter_skip(idx)
            
            # Если главу пропустили, снимаем маркер переозвучки во избежание конфликтов интерфейса
            revoice_path = os.path.join(self.bm.book_dir, f"Ch{idx + 1}.revoice")
            if self.bm.is_chapter_skipped(idx) and os.path.exists(revoice_path):
                os.remove(revoice_path)
                
        self.refresh_chapters_list()




    def on_chapter_clicked(self, item):
        idx = item.data(Qt.ItemDataRole.UserRole)
        if self.bm:
            self.txt_view.setPlainText(self.bm.chapters[idx].raw_text)

    def on_chapter_done_ui(self, idx):
        item = self.list_chapters.item(idx)
        if item:
            item.setText(f"✅ {self.bm.chapters[idx].title}")
            item.setForeground(QColor("#4CAF50"))

    def toggle_play(self):
        if genai is None or genai_types is None:
            QMessageBox.warning(self, "Зависимости", "Для озвучки Gemini Reader требуется пакет google-genai.")
            return
        if not self.api_keys:
            self.set_api_keys()
            return
        
        if not self.workers:
            if not self.bm:
                QMessageBox.warning(self, "Внимание", "Сначала откройте книгу!")
                return

            # Очистка дашборда
            for i in reversed(range(self.dash_layout.count())):
                widget = self.dash_layout.itemAt(i).widget()
                if widget: widget.setParent(None)
            self.worker_widgets = {}

            q = queue.Queue()
            for i in range(len(self.bm.chapters)):
                if not self.bm.is_chapter_done(i) and not self.bm.is_chapter_skipped(i):
                    q.put(i)
            
            if q.empty():
                QMessageBox.information(self, "Готово", "Все главы для озвучки уже завершены или пропущены!")
                return

            num_workers = min(len(self.api_keys), q.qsize())
            if num_workers == 0: num_workers = 1

            multi = len(self.api_keys) > 1
            live_playback = not self.chk_fast.isChecked() and not multi and pyaudio is not None
            if not live_playback and not self.chk_fast.isChecked() and not multi and pyaudio is None:
                self.statusBar().showMessage("PyAudio не найден: live-воспроизведение отключено, используется только экспорт.")
            if not live_playback and not self.chk_mp3.isChecked():
                QMessageBox.warning(self, "Режим вывода", "Отключены и live-воспроизведение, и запись MP3. Включите запись MP3 или установите PyAudio.")
                return

            if live_playback:
                self.player = AudioPlayer(self.audio_queue, 80)
                self.player.start()

            for i in range(num_workers):
                row = DashboardRow(i)
                self.dash_layout.addWidget(row)
                self.worker_widgets[i] = row
                
                selected_model_name = self.combo_model.currentText()
                actual_model_id = self.models_map.get(selected_model_name, "gemini-3.1-flash-live-preview")
                
                w = GeminiWorker(i, self.api_keys[i], self.bm, 
                                 self.audio_queue if live_playback else None,
                                 actual_model_id, 
                                 self.combo_voices.currentData(), # Передаем чистый ID голоса
                                 "Ты диктор.", self.combo_speed.currentText(),
                                 self.chk_mp3.isChecked(), self.chk_fast.isChecked(),
                                 self.spin_chunk.value(), q)
                
                w.worker_progress.connect(row.update_progress)
                
                def on_finished(wid):
                    row_widget = self.worker_widgets.pop(wid, None)
                    if row_widget:
                        row_widget.setParent(None)

                    self.workers = [worker for worker in self.workers if getattr(worker, "worker_id", None) != wid]
                    if not self.workers:
                        if self.player:
                            self.player.stop()
                            self.player = None
                        self._set_reading_controls_running(False)
                        self.statusBar().showMessage("Озвучка завершена.")
                
                w.finished_signal.connect(on_finished)
                w.chapter_done_ui_signal.connect(self.on_chapter_done_ui)
                self.workers.append(w)
                w.start()
            
            self._set_reading_controls_running(True)
            self.tabs.setCurrentIndex(1)
        else:
            self.force_stop()




    def force_stop(self):
        for w in self.workers:
            w.stop()
        self.workers = []
        
        if self.player:
            self.player.stop()
            self.player = None
            
        self._set_reading_controls_running(False)
        self.statusBar().showMessage("Процесс остановлен.")

    def run_combine(self):
        if not self.bm:
            return
        ffmpeg_path = _resolve_tool_path("ffmpeg")
        ffprobe_path = _resolve_tool_path("ffprobe")
        if ffmpeg_path is None or ffprobe_path is None:
            QMessageBox.warning(self, "Зависимости", "Для склейки MP3 нужны ffmpeg и ffprobe рядом с приложением или в PATH.")
            return
        # Передаем текущий выбранный чистый ID голоса в воркер склейки
        self.combiner = AudioCombinerWorker(self.bm, self.combo_voices.currentData(), ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path)
        self.combiner.progress_signal.connect(lambda m: self.statusBar().showMessage(m))
        
        def on_combine_finished(m):
            self.statusBar().showMessage("✅ Склейка завершена!")
            QApplication.processEvents() # Принудительно обновляем нижнюю панель до появления окна
            QMessageBox.information(self, "Склейка", m)
            
        self.combiner.finished_signal.connect(on_combine_finished)
        self.combiner.start()

    def on_vol(self, v):
        if self.player: self.player.set_volume(v)

    def set_api_keys(self):
        dlg = ApiKeysDialog("\n".join(self.api_keys), self)
        if dlg.exec():
            self.api_keys = dlg.get_keys()
            if self.settings_manager is not None:
                self.settings_manager.save_api_keys(self.api_keys)
            else:
                legacy_data = _load_legacy_settings()
                legacy_data["api_keys"] = self.api_keys
                with open(LEGACY_SETTINGS_FILE, "w", encoding="utf-8") as file_obj:
                    json.dump(legacy_data, file_obj, ensure_ascii=False, indent=2)



    def save_settings(self):
        data = self._reader_ui_settings_payload()
        if self.settings_manager is not None:
            self.settings_manager.save_ui_state({READER_SETTINGS_KEY: data})
            return

        legacy_data = _load_legacy_settings()
        legacy_data.update(data)
        legacy_data["api_keys"] = self.api_keys
        with open(LEGACY_SETTINGS_FILE, "w", encoding="utf-8") as file_obj:
            json.dump(legacy_data, file_obj, ensure_ascii=False, indent=2)
        return

        data = {
            "api_keys": self.api_keys,
            "model": self.combo_model.currentText(),
            "voice": self.combo_voices.currentData(), # Сохраняем чистый ID голоса
            "speed": self.combo_speed.currentText(),
            "chunk": self.spin_chunk.value(),
            "record": self.chk_mp3.isChecked(),
            "fast": self.chk_fast.isChecked()
        }
        with open("settings.json", "w") as f:
            json.dump(data, f)




    def load_settings(self):
        legacy_data = _load_legacy_settings()
        data = dict(legacy_data)

        if self.settings_manager is not None:
            try:
                all_settings = self.settings_manager.load_settings()
            except Exception:
                all_settings = {}
            data.update(all_settings.get(READER_SETTINGS_KEY, {}))
            try:
                self.api_keys = self.settings_manager.get_api_keys() or legacy_data.get("api_keys", [])
            except Exception:
                self.api_keys = legacy_data.get("api_keys", [])
        else:
            self.api_keys = legacy_data.get("api_keys", [])

        m = data.get("model")
        if m and m in [self.combo_model.itemText(i) for i in range(self.combo_model.count())]:
            self.combo_model.setCurrentText(m)

        v = data.get("voice")
        if v and v in VOICES_MAP:
            idx = self.combo_voices.findData(v)
            if idx >= 0:
                self.combo_voices.setCurrentIndex(idx)

        s = data.get("speed")
        if s in SPEED_PROMPTS:
            self.combo_speed.setCurrentText(s)

        self.spin_chunk.setValue(data.get("chunk", 2))
        self.chk_mp3.setChecked(data.get("record", True))
        self.chk_fast.setChecked(data.get("fast", False))
        return

        if os.path.exists("settings.json"):
            try:
                with open("settings.json", "r") as f:
                    data = json.load(f)
                    self.api_keys = data.get("api_keys",[])
                    
                    # Восстанавливаем UI
                    m = data.get("model")
                    if m and m in[self.combo_model.itemText(i) for i in range(self.combo_model.count())]:
                        self.combo_model.setCurrentText(m)
                        
                    # Загрузка голоса по его скрытому ID
                    v = data.get("voice")
                    if v and v in VOICES_MAP:
                        idx = self.combo_voices.findData(v)
                        if idx >= 0:
                            self.combo_voices.setCurrentIndex(idx)
                    
                    s = data.get("speed")
                    if s in SPEED_PROMPTS: self.combo_speed.setCurrentText(s)
                    
                    self.spin_chunk.setValue(data.get("chunk", 2))
                    self.chk_mp3.setChecked(data.get("record", True))
                    self.chk_fast.setChecked(data.get("fast", False))
            except: 
                pass

    def open_book_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Открыть EPUB", "", "*.epub")
        if p: self.load_book(p)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
