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

    def __getattr__(name):
        module_path = _LAZY_HANDLER_MODULES.get(name)
        if module_path is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        import importlib
        value = getattr(importlib.import_module(module_path, __name__), name)
        globals()[name] = value  # кэш: дальше атрибут отдаётся без __getattr__
        return value

# =============================================================================
#  SELF-MAINTENANCE SCRIPT (AUTOMATION LOGIC)
# =============================================================================
if __name__ == "__main__":
    import os
    import ast
    import sys

    # Маркер, разделяющий авто-код и логику скрипта
    SEPARATOR = "# ============================================================================="

    def find_handlers(directory):
        """Сканирует папку и ищет классы, заканчивающиеся на 'ApiHandler'."""
        handlers = [] # (filename_no_ext, class_name)
        
        print(f"🔍 Сканирование директории: {directory}")
        
        for filename in sorted(os.listdir(directory)):
            if filename.endswith(".py") and filename != "__init__.py":
                filepath = os.path.join(directory, filename)
                
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read())
                        
                    for node in tree.body:
                        # Ищем классы: class XyzApiHandler(...)
                        if isinstance(node, ast.ClassDef) and node.name.endswith("ApiHandler"):
                            if node.name == "BaseApiHandler": continue
                                
                            module_name = filename[:-3] # убираем .py
                            handlers.append((module_name, node.name))
                            print(f"   ✅ Найден: {node.name} в {filename}")
                            
                except Exception as e:
                    print(f"   ⚠️ Ошибка чтения {filename}: {e}")
        
        return handlers

    def regenerate_self(handlers):
        """Читает себя, сохраняет нижнюю часть и генерирует новую верхнюю."""
        current_file = os.path.abspath(__file__)
        
        with open(current_file, "r", encoding="utf-8") as f:
            content = f.read()

        if SEPARATOR not in content:
            print("❌ ОШИБКА: Не найден разделитель секций в файле __init__.py!")
            return

        # Сохраняем скрипт (нижнюю часть)
        script_logic = content[content.find(SEPARATOR):]

        # Генерируем новую верхнюю часть
        lines = []
        lines.append("# -----------------------------------------------------------------------------")
        lines.append("# AUTO-GENERATED IMPORTS - DO NOT EDIT THIS SECTION MANUALLY")
        lines.append(f"# Run this file as a script to update imports: python {os.path.basename(current_file)}")
        lines.append("# -----------------------------------------------------------------------------")
        lines.append("")
        
        # ВАЖНОЕ ИЗМЕНЕНИЕ: генерируем ЛЕНИВУЮ секцию (PEP 562) — импорт
        # пакета не должен тянуть тяжёлые зависимости хендлеров.
        lines.append('if __name__ != "__main__":')
        lines.append("    _LAZY_HANDLER_MODULES = {")
        for module, classname in handlers:
            lines.append(f'        "{classname}": ".{module}",')
        lines.append("    }")
        lines.append("")
        lines.append("    __all__ = list(_LAZY_HANDLER_MODULES)")
        lines.append("")
        lines.append("    def __getattr__(name):")
        lines.append("        module_path = _LAZY_HANDLER_MODULES.get(name)")
        lines.append("        if module_path is None:")
        lines.append("            raise AttributeError(f\"module {__name__!r} has no attribute {name!r}\")")
        lines.append("        import importlib")
        lines.append("        value = getattr(importlib.import_module(module_path, __name__), name)")
        lines.append("        globals()[name] = value")
        lines.append("        return value")
        lines.append("")
        lines.append("")

        # Собираем и пишем
        new_content = "\n".join(lines) + script_logic

        with open(current_file, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        print(f"✨ Файл {os.path.basename(current_file)} успешно обновлен!")

    # --- ЗАПУСК ---
    current_dir = os.path.dirname(os.path.abspath(__file__))
    found_handlers = find_handlers(current_dir)
    regenerate_self(found_handlers)
