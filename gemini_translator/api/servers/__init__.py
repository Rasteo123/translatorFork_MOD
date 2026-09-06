# -----------------------------------------------------------------------------
# AUTO-GENERATED IMPORTS - DO NOT EDIT THIS SECTION MANUALLY
# Run this file as a script to update imports: python __init__.py
# -----------------------------------------------------------------------------

if __name__ != "__main__":
    # Ленивая загрузка (PEP 562): PerplexityServer тянет flask (~19МБ) —
    # грузим модуль при первом обращении. Для PyInstaller модули указаны
    # в HIDDEN_IMPORTS_BLOCK build_master.py.
    _LAZY_SERVER_MODULES = {
        "PerplexityServer": ".perplexity",
    }

    __all__ = list(_LAZY_SERVER_MODULES)

    # Общее тело PEP 562 __getattr__ и self-maintenance скрипта вынесено в
    # lazy_module.py — оно было продублировано дословно с handlers/__init__.py.
    from gemini_translator.api import lazy_module

    def __getattr__(name):
        value = lazy_module.lazy_attr(name, _LAZY_SERVER_MODULES, __name__)
        globals()[name] = value
        return value

# =============================================================================
#  SELF-MAINTENANCE SCRIPT (AUTOMATION LOGIC)
# =============================================================================
if __name__ == "__main__":
    import os
    import sys

    # Бутстрап sys.path: при запуске `python __init__.py` sys.path[0] — это
    # сама папка servers/, а не корень репозитория, и пакет
    # gemini_translator не установлен как дистрибутив в окружении. Без этого
    # следующий импорт падает с ModuleNotFoundError.
    _repo_root = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
    )
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

    from gemini_translator.api import lazy_module

    def find_servers(directory):
        """Сканирует папку и ищет классы, заканчивающиеся на 'Server'."""
        # Игнорируем __init__.py и base.py (если он там вдруг остался мусором)
        return lazy_module.find_classes(
            directory,
            class_suffix="Server",
            base_class_name="BaseServer",
            ignore_files=("base.py",),
        )

    def regenerate_self(servers):
        lazy_module.regenerate_self(
            current_file=os.path.abspath(__file__),
            classes=servers,
            registry_name="_LAZY_SERVER_MODULES",
        )

    regenerate_self(find_servers(os.path.dirname(os.path.abspath(__file__))))
