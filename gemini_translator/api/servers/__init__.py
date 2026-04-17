# -----------------------------------------------------------------------------
# AUTO-GENERATED IMPORTS - DO NOT EDIT THIS SECTION MANUALLY
# Run this file as a script to update imports: python __init__.py
# -----------------------------------------------------------------------------

if __name__ != "__main__":
    from .perplexity import PerplexityServer

    __all__ = [
        "PerplexityServer"
    ]

# =============================================================================
#  SELF-MAINTENANCE SCRIPT (AUTOMATION LOGIC)
# =============================================================================
if __name__ == "__main__":
    import os
    import ast
    import sys

    SEPARATOR = "# ============================================================================="

    def find_servers(directory):
        """Сканирует папку и ищет классы, заканчивающиеся на 'Server'."""
        servers = []
        print(f"🔍 Сканирование директории: {directory}")

        for filename in sorted(os.listdir(directory)):
            # Игнорируем __init__.py и base.py (если он там вдруг остался мусором)
            if filename.endswith(".py") and filename != "__init__.py" and filename != "base.py":
                filepath = os.path.join(directory, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read())
                    for node in tree.body:
                        # Ищем классы: class XyzServer(BaseServer)
                        if isinstance(node, ast.ClassDef) and node.name.endswith("Server"):
                            # Доп. проверка: не импортируем сам BaseServer, если он вдруг определен тут
                            if node.name == "BaseServer":
                                continue

                            module_name = filename[:-3]
                            servers.append((module_name, node.name))
                            print(f"   ✅ Найден: {node.name} в {filename}")
                except Exception as e:
                    print(f"   ⚠️ Ошибка чтения {filename}: {e}")
        return servers

    def regenerate_self(servers):
        current_file = os.path.abspath(__file__)
        with open(current_file, "r", encoding="utf-8") as f:
            content = f.read()

        if SEPARATOR not in content:
            return

        script_logic = content[content.find(SEPARATOR):]
        lines = [
            "# -----------------------------------------------------------------------------",
            "# AUTO-GENERATED IMPORTS - DO NOT EDIT THIS SECTION MANUALLY",
            f"# Run this file as a script to update imports: python {os.path.basename(current_file)}",
            "# -----------------------------------------------------------------------------",
            "",
            'if __name__ != "__main__":'
        ]

        for module, classname in servers:
            lines.append(f"    from .{module} import {classname}")

        lines.append("")
        lines.append("    __all__ = [")

        for i, (_, classname) in enumerate(servers):
            comma = "," if i < len(servers) - 1 else ""
            lines.append(f'        "{classname}"{comma}')

        lines.append("    ]")
        lines.append("")
        lines.append("")

        new_content = "\n".join(lines) + script_logic
        with open(current_file, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"✨ Файл {os.path.basename(current_file)} успешно обновлен!")

    regenerate_self(find_servers(os.path.dirname(os.path.abspath(__file__))))
