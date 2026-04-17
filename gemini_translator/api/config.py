# -*- coding: utf-8 -*-

import json
import importlib.util
from pathlib import Path
import sys
import shutil
import os # <-- Добавляем импорт os


# [ARCH] URI для общей базы данных в оперативной памяти.
# mode=memory: данные живут только в RAM.
# cache=shared: позволяет разным потокам видеть одну и ту же базу данных.
SESSION_ID = os.path.basename(os.getcwd()).replace(" ", "_").replace(".", "_")
SHARED_DB_URI = f'file:{SESSION_ID}_vfm_session?mode=memory&cache=shared'
# --- ЭТАП 1: УНИВЕРСАЛЬНЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С ПУТЯМИ ---

def get_executable_dir() -> Path | None:
    """Возвращает путь к папке с .exe файлом, если приложение скомпилировано."""
    if getattr(sys, 'frozen', False):
        return Path(os.path.dirname(sys.executable))
    return None

def get_internal_resource_dir() -> Path | None:
    """Возвращает путь к временной папке _MEIPASS, если это one-file сборка."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    return None

def get_dev_project_root() -> Path:
    """Возвращает корень проекта в режиме разработки."""
    return Path(__file__).resolve().parents[2]

def get_resource_path(relative_path: str) -> Path:
    """
    Универсальная функция поиска ресурсов с приоритетом для гибридной сборки.
    1. Ищет ресурс рядом с .exe.
    2. Если не находит, ищет внутри .exe (во временной папке).
    3. Если это режим разработки, ищет относительно корня проекта.
    """
    # Сценарий 1: Приложение скомпилировано
    if getattr(sys, 'frozen', False):
        executable_dir = get_executable_dir()
        
        # Приоритет №1: Внешний файл (для гибридного режима)
        external_path = executable_dir / relative_path
        if external_path.exists():
            return external_path
            
        # Приоритет №2: Внутренний файл (для портативного режима)
        internal_dir = get_internal_resource_dir()
        if internal_dir:
            internal_path = internal_dir / relative_path
            if internal_path.exists():
                return internal_path
        
        # Если ничего не найдено, все равно возвращаем путь к внешнему файлу.
        # Вызывающий код должен будет обработать ошибку FileNotFoundError.
        return external_path

    # Сценарий 2: Режим разработки
    else:
        project_root = get_dev_project_root()
        return project_root / relative_path

_PROVIDERS_FILE = get_resource_path("config/api_providers.json")
_PROMPT_FILE = get_resource_path("config/default_prompt.txt")
_GLOSSARY_PROMPT_FILE = get_resource_path("config/default_glossary_prompt.txt")
_GENRE_GLOSSARY_PROMPT_FILE = get_resource_path("config/default_genre_promt.txt")
_CORRECTION_PROMPT_FILE = get_resource_path("config/default_correction_prompt.txt")
_UNTRANSLATED_PROMPT_FILE = get_resource_path("config/default_untranslated_prompt.txt")
_MANUAL_TRANSLATION_PROMPT_FILE = get_resource_path("config/default_manual_translation_prompt.txt")
_WORD_EXCEPTIONS_FILE = get_resource_path("config/default_word_exceptions.txt")
_INTERNAL_PROMPTS_FILE = get_resource_path("config/internal_prompts.json")

_BASE_GLOSSARY_FILES = {
    "xianxia": "config/base_glossaries/xianxia.json",
}
_BASE_GLOSSARY_DISPLAY_NAMES = {
    "xianxia": "Базовый глоссарий сянься",
}

# Резервные встроенные конфиги на случай, если файлы не найдены
_DEFAULT_API_PROVIDERS_CONFIG = {
    "gemini": {
        "display_name": "Google Gemini (default)",
        "handler_class": "GeminiApiHandler",
        "is_async": False,
        "needs_warmup": False,
        "file_suffix": "_translated.html",
        "reset_policy": {"type": "daily", "timezone": "America/Los_Angeles", "reset_hour": 0, "reset_minute": 1},
        "models": {"Gemini 2.5 Flash Preview": {"id": "gemini-2.5-flash", "rpm": 10, "needs_chunking": True}}
    }
}
_DEFAULT_PROMPT_TEXT = """**I. РОЛЬ И ГЛАВНАЯ ЦЕЛЬ** (встроенный промпт) …"""
_DEFAULT_GLOSSARY_PROMPT_TEXT = """Проанализируй весь предоставленный текст …"""
_DEFAULT_WORD_EXCEPTIONS_TEXT = "# Пустой список исключений по умолчанию"
_DEFAULT_CORRECTION_PROMPT_TEXT = """Проанализируй представленный глоссарий…"""
_DEFAULT_MANUAL_TRANSLATION_PROMPT_TEXT = (
    "Переведи следующий текст на русский язык.\n"
    "Верни только чистый готовый перевод без HTML-тегов, без пояснений и без комментариев.\n\n"
    "{text}"
)

def _load_providers_config():
    if _PROVIDERS_FILE.exists():
        try:
            with open(_PROVIDERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return _DEFAULT_API_PROVIDERS_CONFIG
    return _DEFAULT_API_PROVIDERS_CONFIG

def _load_default_prompt():
    if _PROMPT_FILE.exists():
        try:
            with open(_PROMPT_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception:
            return _DEFAULT_PROMPT_TEXT
    return _DEFAULT_PROMPT_TEXT

def _load_default_glossary_prompt():
    if _GLOSSARY_PROMPT_FILE.exists():
        try:
            with open(_GLOSSARY_PROMPT_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception:
            return _DEFAULT_GLOSSARY_PROMPT_TEXT
    return _DEFAULT_GLOSSARY_PROMPT_TEXT

def _load_default_correction_prompt():
    if _CORRECTION_PROMPT_FILE.exists():
        try:
            with open(_CORRECTION_PROMPT_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception:
            return _DEFAULT_CORRECTION_PROMPT_TEXT
    return _DEFAULT_CORRECTION_PROMPT_TEXT

def _load_default_untranslated_prompt():
    if _UNTRANSLATED_PROMPT_FILE.exists():
        try:
            with open(_UNTRANSLATED_PROMPT_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception:
            return "Переведи следующий текст:"
    return "Переведи следующий текст:"
    
def _load_default_manual_translation_prompt():
    if _MANUAL_TRANSLATION_PROMPT_FILE.exists():
        try:
            with open(_MANUAL_TRANSLATION_PROMPT_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception:
            return _DEFAULT_MANUAL_TRANSLATION_PROMPT_TEXT
    return _DEFAULT_MANUAL_TRANSLATION_PROMPT_TEXT

def _load_default_word_exceptions():
    if _WORD_EXCEPTIONS_FILE.exists():
        try:
            with open(_WORD_EXCEPTIONS_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception:
            return _DEFAULT_WORD_EXCEPTIONS_TEXT
    return _DEFAULT_WORD_EXCEPTIONS_TEXT

def _load_internal_prompts():
    """
    Загружает скрытые промпты из JSON или возвращает дефолты.
    Поддерживает многострочность через списки строк в JSON.
    """
    defaults = {
        "glossary_context_simple": "--- КОНТЕКСТ ---\n",
        "glossary_context_full": "--- КОНТЕКСТ ---\n",
        "batch_instruction": "\n\n### ИНСТРУКЦИЯ\n Keep all `<!-- i -->` including the last one. ###\n```html\n{full_text_for_api}\n```\n",
        "glossary_output_examples": {"base": ["  \"Arthur\": { \"rus\": \"Артур\", \"note\": \"Персонаж; Мужчина; Имя склоняется (позвал Артура)\" }"]},
        "glossary_tag_explanation": {
            "_INTRO_TEXT_": "GLOSSARY GUIDE\nThe `i` (info) field contains critical commands. Decode them as follows:",
	        "HOMONYM/ОМОНИМ": "Context Switch. Choose the translation that matches the current condition.",
	        "GENDER INTRIGUE/ГЕНДЕРНАЯ ИНТРИГА": "Complex Gender Protocol. Follow sub-tags based on chapter context."
        },
        "translation_output_examples": {
            "base": [
                "Src: <p>\"Hello,\" he said.</p>\nTgt: <p>─ Привет, ─ сказал он.</p>",
                "Src: <p>'Thinking,' he thought.</p>\nTgt: <p>«Мысли», – подумал он.</p>",
                "Src: <p>[System: Alert]</p>\nTgt: <p>[Система: Тревога]</p>"
            ]
        },
        "completion_instruction": (
            "\n---\n"
            "### ЗАДАЧА: ДОПЕРЕВОД ПРЕРВАННОГО ОТВЕТА ###\n"
            "Верни только недостающую часть перевода.\n"
            "Не повторяй уже переведенный фрагмент и не начинай заново с начала чанка.\n"
            "Продолжай с первого незавершенного безопасного места, сохраняя HTML-структуру, теги и служебные маркеры.\n"
            "Если последний фрагмент оборван внутри предложения или блока, начни с ближайшего естественного продолжения, не дублируя хвост.\n"
            "\n--- УЖЕ ПЕРЕВЕДЕНО:\n"
            "```html\n"
            "{partial_translation}\n"
            "```\n"
            "---\n"
            "Верни только продолжение, без пояснений и без повтора уже готового текста.\n"
        ),
        "correction_prompts": {
            "intro": "Данные в блоках:",
            "block_descriptions": {
                "context": "Контекст.",
                "conflicts": "Конфликты.",
                "overlaps": "Наложения.",
                "patterns": "Паттерны.",
                "hidden": "Скрытые."
            },
            "format_instructions": {
                "json_intro": "Формат JSON:",
                "schemas": {
                    "input_with_notes": "`\"Оригинал\": { \"rus\": \"Перевод\", \"note\": \"Примечание\" }`",
                    "output_with_notes": "`{ \"Оригинал\": { \"rus\": \"Исправленный Перевод\", \"note\": \"Исправленное Примечание\" } }`",
                    "input_simple": "`\"Оригинал\": { \"rus\": \"Перевод\" }`",
                    "output_simple": "`{ \"Оригинал\": { \"rus\": \"Исправленный Перевод\" } }`"
                },
                "warning_hint": "Исправь WARNING.",
                "note_policy": "Сохраняй грамматику примечаний",
                "task_goal": "Верни JSON:"
            },
            "examples": {
                "with_notes": "{\"A\": {\"rus\": \"B\", \"note\": \"C\"}}",
                "simple": "{\"A\": {\"rus\": \"B\"}}"
            }
        }
    }
    
    loaded_data = {}
    if _INTERNAL_PROMPTS_FILE.exists():
        try:
            with open(_INTERNAL_PROMPTS_FILE, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
        except Exception as e:
            print(f"####################\n####################\n[CONFIG ERROR] НЕ УДАЛОСЬ ЗАГРУЗИТЬ internal_prompts.json: {e}\n####################\n####################")
    # Объединяем загруженные данные с дефолтными
    for key, value in loaded_data.items():
        if key in defaults and isinstance(defaults[key], dict) and isinstance(value, dict):
             defaults[key].update(value)
        else:
             defaults[key] = value
    
    # --- МАГИЯ СКЛЕИВАНИЯ ---
    # Если значение — это список, превращаем его в строку
    for key, value in defaults.items():
        if isinstance(value, list):
            defaults[key] = "\n".join(value) # <--- разделитель \n
            
    return defaults


# --- ЭТАП 2: ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ-ХРАНИЛИЩА ---
_API_PROVIDERS = {}
_DEFAULT_PROMPT = ""
_DEFAULT_GLOSSARY_PROMPT = ""
_DEFAULT_WORD_EXCEPTIONS = ""
_DEFAULT_CORRECTION_PROMPT = ""
_DEFAULT_UNTRANSLATED_PROMPT = ""
_DEFAULT_MANUAL_TRANSLATION_PROMPT = ""
_INTERNAL_PROMPTS = {}
_ALL_MODELS = {}
_PROVIDER_DISPLAY_MAP = {}
_ALL_TRANSLATED_SUFFIXES = []

# --- ПАРАМЕТРЫ РАСЧЕТА ТОКЕНОВ И РАЗМЕРОВ ---
CHARS_PER_ASCII_TOKEN = 4.0
CHARS_PER_CYRILLIC_TOKEN = 2.2
UNIFIED_INPUT_CHARS_PER_TOKEN = CHARS_PER_ASCII_TOKEN
MODEL_OUTPUT_SAFETY_MARGIN = 0.95
ALPHABETIC_EXPANSION_FACTOR = 1.6
CJK_EXPANSION_FACTOR = 3.5

# --- ЭТАП 3: ГЛАВНАЯ ФУНКЦИЯ-ИНИЦИАЛИЗАТОР ---
def initialize_configs():
    global _API_PROVIDERS, _DEFAULT_PROMPT, _DEFAULT_GLOSSARY_PROMPT, _DEFAULT_CORRECTION_PROMPT, _DEFAULT_UNTRANSLATED_PROMPT, _DEFAULT_MANUAL_TRANSLATION_PROMPT, _DEFAULT_WORD_EXCEPTIONS, _ALL_MODELS, _PROVIDER_DISPLAY_MAP, _ALL_TRANSLATED_SUFFIXES, _INTERNAL_PROMPTS
    
    print("[CONFIG INFO] Централизованная инициализация конфигураций…")
    _API_PROVIDERS = _load_providers_config()
    _DEFAULT_PROMPT = _load_default_prompt()
    _DEFAULT_GLOSSARY_PROMPT = _load_default_glossary_prompt()
    _DEFAULT_WORD_EXCEPTIONS = _load_default_word_exceptions()
    _DEFAULT_CORRECTION_PROMPT = _load_default_correction_prompt()
    _DEFAULT_UNTRANSLATED_PROMPT = _load_default_untranslated_prompt()
    _DEFAULT_MANUAL_TRANSLATION_PROMPT = _load_default_manual_translation_prompt()
    _INTERNAL_PROMPTS = _load_internal_prompts()

    _API_PROVIDERS['dry_run'] = {
        "display_name": "Пробный запуск",
        "visible": False,
        "handler_class": "DryRunApiHandler",
        "file_suffix": "_dry_run.html",
        "reset_policy": {"type": "rolling", "duration_hours": 999},
        "models": {"dry-run-model": {"id": "dry-run-model", "rpm": 1000}}
    }
    
    _ALL_MODELS = {
        model_name: {**model_config, 'provider': provider_id}
        for provider_id, provider_data in _API_PROVIDERS.items()
        for model_name, model_config in provider_data.get("models", {}).items()
    }
    _PROVIDER_DISPLAY_MAP = {
        p_data["display_name"]: p_id for p_id, p_data in _API_PROVIDERS.items()
    }
    _ALL_TRANSLATED_SUFFIXES = list(set(
        p.get("file_suffix", "_translated.html") for p in _API_PROVIDERS.values()
    ))
    print("[CONFIG INFO] Глобальные конфигурации успешно инициализированы.")

# --- ЭТАП 4: ПУБЛИЧНЫЕ ФУНКЦИИ-ГЕТТЕРЫ (стабильный API) ---
def _ensure_configs_initialized():
    if not _API_PROVIDERS:
        initialize_configs()

def api_providers():
    _ensure_configs_initialized()
    return _API_PROVIDERS

def default_prompt():
    _ensure_configs_initialized()
    return _DEFAULT_PROMPT
def default_glossary_prompt():
    _ensure_configs_initialized()
    return _DEFAULT_GLOSSARY_PROMPT
def builtin_glossary_prompt_variants():
    variants = {}
    try:
        if _GENRE_GLOSSARY_PROMPT_FILE.exists():
            text = _GENRE_GLOSSARY_PROMPT_FILE.read_text(encoding='utf-8').strip()
            if text:
                variants["builtin:genre_cultivation"] = {
                    "label": "Культивация / жанровый канонизатор",
                    "text": text,
                }
    except Exception:
        pass
    return variants
def default_correction_prompt():
    _ensure_configs_initialized()
    return _DEFAULT_CORRECTION_PROMPT

def default_untranslated_prompt():
    _ensure_configs_initialized()
    return _DEFAULT_UNTRANSLATED_PROMPT

def default_manual_translation_prompt():
    _ensure_configs_initialized()
    return _DEFAULT_MANUAL_TRANSLATION_PROMPT

def internal_prompts():
    _ensure_configs_initialized()
    return _INTERNAL_PROMPTS
def all_models():
    _ensure_configs_initialized()
    return _ALL_MODELS

def provider_display_map():
    _ensure_configs_initialized()
    return _PROVIDER_DISPLAY_MAP

def all_translated_suffixes():
    _ensure_configs_initialized()
    return _ALL_TRANSLATED_SUFFIXES
def default_word_exceptions():
    _ensure_configs_initialized()
    return _DEFAULT_WORD_EXCEPTIONS

def provider_requires_api_key(provider_id: str | None) -> bool:
    _ensure_configs_initialized()
    if not provider_id:
        return True
    provider_cfg = _API_PROVIDERS.get(provider_id, {})
    return provider_cfg.get("requires_api_key", True)

def provider_max_instances(provider_id: str | None) -> int | None:
    _ensure_configs_initialized()
    if not provider_id:
        return None
    provider_cfg = _API_PROVIDERS.get(provider_id, {})
    raw_value = provider_cfg.get("max_instances")
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None

def provider_placeholder_api_key(provider_id: str | None) -> str:
    _ensure_configs_initialized()
    normalized_provider = str(provider_id or "default").strip() or "default"
    provider_cfg = _API_PROVIDERS.get(normalized_provider, {})
    configured_value = str(provider_cfg.get("placeholder_api_key", "")).strip()
    if configured_value:
        return configured_value
    return f"__virtual_session__:{normalized_provider}"

def _deduplicate_paths(candidates: list[Path | None]) -> list[Path]:
    unique_paths = []
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        try:
            normalized = candidate.resolve(strict=False)
        except Exception:
            normalized = Path(candidate)
        key = str(normalized).lower()
        if key in seen:
            continue
        seen.add(key)
        unique_paths.append(normalized)
    return unique_paths

def find_workascii_root() -> Path | None:
    dev_root = get_dev_project_root()
    executable_dir = get_executable_dir()
    internal_dir = get_internal_resource_dir()

    candidates = []
    for base in [executable_dir, internal_dir, dev_root, dev_root.parent]:
        if not base:
            continue
        candidates.extend([
            base / "work_ascii",
            base.parent / "work_ascii",
        ])

    for candidate in _deduplicate_paths(candidates):
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None

def default_workascii_runtime_root() -> Path:
    candidates = [
        get_executable_dir(),
        get_internal_resource_dir(),
        get_dev_project_root(),
        find_workascii_root(),
    ]
    for candidate in _deduplicate_paths(candidates):
        if candidate.exists() and candidate.is_dir():
            return candidate
    return get_dev_project_root()

def default_workascii_profile_dir(workascii_root: str | Path | None = None) -> Path | None:
    root = Path(workascii_root) if workascii_root else default_workascii_runtime_root()
    if not root:
        return None

    candidates = [
        root / "chatgpt-profile-run",
        root / "output" / "chatgpt" / "profile",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]

def _python_playwright_driver_dir() -> Path | None:
    try:
        spec = importlib.util.find_spec("playwright")
    except Exception:
        spec = None
    if not spec or not spec.origin:
        return None
    driver_dir = Path(spec.origin).parent / "driver"
    if driver_dir.exists() and driver_dir.is_dir():
        return driver_dir
    return None

def find_playwright_package_root(workascii_root: str | Path | None = None) -> Path | None:
    root = Path(workascii_root) if workascii_root else None
    executable_dir = get_executable_dir()
    internal_dir = get_internal_resource_dir()
    dev_root = get_dev_project_root()
    driver_dir = _python_playwright_driver_dir()
    env_root = os.environ.get("PLAYWRIGHT_PACKAGE_ROOT")

    candidates = [
        (root / "playwright_runtime" / "package") if root else None,
        (executable_dir / "playwright_runtime" / "package") if executable_dir else None,
        (internal_dir / "playwright_runtime" / "package") if internal_dir else None,
        (dev_root / "playwright_runtime" / "package") if dev_root else None,
        (driver_dir / "package") if driver_dir else None,
        Path(env_root) if env_root else None,
        (root / "node_modules" / "playwright") if root else None,
    ]
    for candidate in _deduplicate_paths(candidates):
        if candidate.exists() and candidate.is_dir() and (candidate / "package.json").exists():
            return candidate
    return None

def find_playwright_browsers_path(workascii_root: str | Path | None = None) -> Path | None:
    root = Path(workascii_root) if workascii_root else None
    executable_dir = get_executable_dir()
    internal_dir = get_internal_resource_dir()
    dev_root = get_dev_project_root()
    env_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    localappdata = os.environ.get("LOCALAPPDATA")
    try:
        home_dir = Path.home()
    except RuntimeError:
        home_dir = None

    candidates = [
        (root / "playwright_runtime" / "ms-playwright") if root else None,
        (root / "ms-playwright") if root else None,
        (executable_dir / "playwright_runtime" / "ms-playwright") if executable_dir else None,
        (internal_dir / "playwright_runtime" / "ms-playwright") if internal_dir else None,
        (dev_root / "playwright_runtime" / "ms-playwright") if dev_root else None,
        Path(env_root) if env_root else None,
        (Path(localappdata) / "ms-playwright") if localappdata else None,
        (home_dir / ".cache" / "ms-playwright") if home_dir else None,
        (home_dir / "Library" / "Caches" / "ms-playwright") if home_dir else None,
    ]
    for candidate in _deduplicate_paths(candidates):
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None

def find_node_executable(workascii_root: str | Path | None = None) -> Path | None:
    root = Path(workascii_root) if workascii_root else None
    which_node = shutil.which("node")
    env_node = os.environ.get("PLAYWRIGHT_NODEJS_PATH")
    executable_dir = get_executable_dir()
    internal_dir = get_internal_resource_dir()
    dev_root = get_dev_project_root()
    driver_dir = _python_playwright_driver_dir()

    candidates = []
    for executable_name in ("node.exe", "node"):
        candidates.extend([
            (root / "playwright_runtime" / executable_name) if root else None,
            (root / executable_name) if root else None,
            (executable_dir / "playwright_runtime" / executable_name) if executable_dir else None,
            (executable_dir / executable_name) if executable_dir else None,
            (internal_dir / "playwright_runtime" / executable_name) if internal_dir else None,
            (dev_root / "playwright_runtime" / executable_name) if dev_root else None,
            (dev_root / executable_name) if dev_root else None,
            (driver_dir / executable_name) if driver_dir else None,
        ])

    candidates.extend([
        (root / "node_modules" / ".bin" / "node") if root else None,
        Path(env_node) if env_node else None,
        Path(which_node) if which_node else None,
    ])

    for candidate in _deduplicate_paths(candidates):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None

def _discover_base_glossary_ids() -> list:
    glossary_dir = get_resource_path("config/base_glossaries")
    discovered = set(_BASE_GLOSSARY_FILES.keys())
    try:
        if glossary_dir.exists() and glossary_dir.is_dir():
            discovered.update(path.stem for path in glossary_dir.glob("*.json"))
    except Exception as e:
        print(f"[CONFIG WARN] Не удалось просканировать базовые глоссарии: {e}")
    return sorted(discovered)

def base_glossary_names():
    return {
        glossary_id: _BASE_GLOSSARY_DISPLAY_NAMES.get(
            glossary_id,
            glossary_id.replace("_", " ").replace("-", " ").title()
        )
        for glossary_id in _discover_base_glossary_ids()
    }

def load_base_glossary(name: str) -> list:
    relative_path = _BASE_GLOSSARY_FILES.get(name)
    if not relative_path:
        safe_name = Path(str(name)).name
        if not safe_name or safe_name != str(name):
            return []
        relative_path = f"config/base_glossaries/{safe_name}.json"

    glossary_path = get_resource_path(relative_path)
    if not glossary_path.exists():
        return []

    try:
        with open(glossary_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"[CONFIG ERROR] Не удалось загрузить базовый глоссарий '{name}': {e}")
        return []

    normalized = []
    if isinstance(data, dict):
        iterable = data.items()
        for original, value in iterable:
            if not isinstance(value, dict):
                continue
            rus = value.get('rus') or value.get('translation') or ""
            if not original or not rus:
                continue
            normalized.append({
                "original": str(original),
                "rus": str(rus),
                "note": str(value.get('note', "")),
            })
    elif isinstance(data, list):
        for value in data:
            if not isinstance(value, dict):
                continue
            original = value.get('original')
            rus = value.get('rus') or value.get('translation') or ""
            if not original or not rus:
                continue
            normalized.append({
                "original": str(original),
                "rus": str(rus),
                "note": str(value.get('note', "")),
            })

    return normalized

# --- ГЕТТЕРЫ ДЛЯ СТАТИЧЕСКИХ КОНСТАНТ ---
def default_reset_policy(): return {"type": "rolling", "duration_hours": 24}
def default_model_name(): return "Gemini 2.5 Flash Preview"
def max_retries(): return 1
def retry_delay_seconds(): return 25
def rate_limit_delay_seconds(): return 60
def api_timeout_seconds(): return 600
def default_max_output_tokens(): return 8192
def chunk_target_size(): return 30000
def input_character_limit_for_chunk(): return 900_000
def chunk_search_window(): return 500
def min_chunk_size(): return 500
def min_forced_chunk_size(): return 250
def chunk_html_source(): return True
