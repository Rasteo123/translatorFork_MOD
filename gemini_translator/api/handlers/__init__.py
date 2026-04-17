# -----------------------------------------------------------------------------
# AUTO-GENERATED IMPORTS - DO NOT EDIT THIS SECTION MANUALLY
# Run this file as a script to update imports: python __init__.py
# -----------------------------------------------------------------------------

if __name__ != "__main__":
    from .browser import BrowserApiHandler
    from .dry_run import DryRunApiHandler
    from .gemini import GeminiApiHandler
    from .huggingface import HuggingFaceApiHandler
    from .deepseek import DeepseekApiHandler
    from .local import LocalApiHandler
    from .openrouter import OpenRouterApiHandler
    from .workascii_chatgpt import WorkAsciiChatGptApiHandler

    __all__ = [
        "BrowserApiHandler",
        "DryRunApiHandler",
        "GeminiApiHandler",
        "HuggingFaceApiHandler",
        "DeepseekApiHandler",
        "LocalApiHandler",
        "OpenRouterApiHandler",
        "WorkAsciiChatGptApiHandler"
    ]

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
        
        # ВАЖНОЕ ИЗМЕНЕНИЕ: Оборачиваем импорты в условие
        lines.append('if __name__ != "__main__":')
        
        for module, classname in handlers:
            lines.append(f"    from .{module} import {classname}")
        
        lines.append("")
        lines.append("    __all__ = [")
        for i, (_, classname) in enumerate(handlers):
            comma = "," if i < len(handlers) - 1 else ""
            lines.append(f'        "{classname}"{comma}')
        lines.append("    ]")
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
