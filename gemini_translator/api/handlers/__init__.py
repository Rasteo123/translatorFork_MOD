# -----------------------------------------------------------------------------
# AUTO-GENERATED IMPORTS - DO NOT EDIT THIS SECTION MANUALLY
# Run this file as a script to update imports: python __init__.py
# -----------------------------------------------------------------------------

if __name__ != "__main__":
    # Ленивая загрузка (PEP 562): модуль хендлера импортируется при первом
    # обращении к классу — import пакета не тянет curl_cffi/playwright/flask
    # и прочие тяжёлые зависимости (~79МБ на старте GUI). PyInstaller ленивые
    # импорты не видит: модули продублированы в HIDDEN_IMPORTS_BLOCK
    # build_master.py.
    _LAZY_HANDLER_MODULES = {
        "BrowserApiHandler": ".browser",
        "DryRunApiHandler": ".dry_run",
        "GeminiApiHandler": ".gemini",
        "HuggingFaceApiHandler": ".huggingface",
        "DeepseekApiHandler": ".deepseek",
        "NvidiaApiHandler": ".nvidia",
        "OpenModelApiHandler": ".openmodel",
        "LocalApiHandler": ".local",
        "McpApiHandler": ".mcp",
        "OpenRouterApiHandler": ".openrouter",
        "QoderApiHandler": ".qoder",
        "WorkAsciiChatGptApiHandler": ".workascii_chatgpt",
    }

    __all__ = list(_LAZY_HANDLER_MODULES)

    # Общее тело PEP 562 __getattr__ и self-maintenance скрипта вынесено в
    # lazy_module.py — оно было продублировано дословно с servers/__init__.py.
    from gemini_translator.api import lazy_module

    def __getattr__(name):
        value = lazy_module.lazy_attr(name, _LAZY_HANDLER_MODULES, __name__)
        globals()[name] = value  # кэш: дальше атрибут отдаётся без __getattr__
        return value

# =============================================================================
#  SELF-MAINTENANCE SCRIPT (AUTOMATION LOGIC)
# =============================================================================
if __name__ == "__main__":
    import os
    import sys

    # Бутстрап sys.path: при запуске `python __init__.py` sys.path[0] — это
    # сама папка handlers/, а не корень репозитория, и пакет
    # gemini_translator не установлен как дистрибутив в окружении. Без этого
    # следующий импорт падает с ModuleNotFoundError.
    _repo_root = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
    )
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

    from gemini_translator.api import lazy_module

    def find_handlers(directory):
        """Сканирует папку и ищет классы, заканчивающиеся на 'ApiHandler'."""
        return lazy_module.find_classes(
            directory,
            class_suffix="ApiHandler",
            base_class_name="BaseApiHandler",
        )

    def _on_missing_separator():
        print("❌ ОШИБКА: Не найден разделитель секций в файле __init__.py!")

    def regenerate_self(handlers):
        """Читает себя, сохраняет нижнюю часть и генерирует новую верхнюю."""
        lazy_module.regenerate_self(
            current_file=os.path.abspath(__file__),
            classes=handlers,
            registry_name="_LAZY_HANDLER_MODULES",
            on_missing_separator=_on_missing_separator,
        )

    # --- ЗАПУСК ---
    current_dir = os.path.dirname(os.path.abspath(__file__))
    found_handlers = find_handlers(current_dir)
    regenerate_self(found_handlers)
