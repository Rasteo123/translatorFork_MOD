"""Общий код lazy-init для пакетов ``gemini_translator/api/handlers`` и
``gemini_translator/api/servers``.

Оба пакета раньше держали дословно одинаковый PEP 562 ``__getattr__`` (лениво
импортирует класс хендлера/сервера при первом обращении, чтобы import пакета
не тянул тяжёлые опциональные зависимости — curl_cffi/playwright/flask) и
дословно одинаковый self-maintenance скрипт (``python __init__.py``,
пересобирающий авто-генерируемую секцию импортов по классам, найденным в
папке). Отличались только имя реестра классов и две мелкие детали
self-maintenance скрипта:

- servers-версия при сканировании директории целиком игнорирует файл
  ``base.py`` (см. ``ignore_files``);
- handlers-версия при отсутствии SEPARATOR печатает сообщение об ошибке,
  servers-версия молча ничего не делает (см. ``on_missing_separator``).

Обе детали сохранены как параметры ниже — вызывающий код (``handlers/__init__.py``,
``servers/__init__.py``) передаёт их явно.
"""
from __future__ import annotations

import importlib
import os
from typing import Callable, Dict, Iterable, List, Optional, Tuple

SEPARATOR = "# ============================================================================="


def lazy_attr(name: str, registry: Dict[str, str], package: str):
    """Тело PEP 562 ``__getattr__``: лениво импортирует класс ``name`` из
    относительного модуля ``registry[name]`` внутри пакета ``package``.

    Кэширование результата в ``globals()`` остаётся на стороне вызывающего
    модуля — только у него есть доступ к своему модульному namespace.
    """
    module_path = registry.get(name)
    if module_path is None:
        raise AttributeError(f"module {package!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_path, package), name)


def find_classes(
    directory: str,
    *,
    class_suffix: str,
    base_class_name: str,
    ignore_files: Iterable[str] = (),
) -> List[Tuple[str, str]]:
    """Сканирует ``directory`` и ищет классы ``class Xyz<class_suffix>(...)``,
    кроме ``base_class_name``. Возвращает список
    ``(имя_модуля_без_.py, имя_класса)``.
    """
    import ast  # dev-only: не нужен на рантайм-пути lazy_attr (старт GUI).

    ignore = {"__init__.py", *ignore_files}
    classes: List[Tuple[str, str]] = []
    print(f"🔍 Сканирование директории: {directory}")
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".py") or filename in ignore:
            continue
        filepath = os.path.join(directory, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name.endswith(class_suffix):
                    if node.name == base_class_name:
                        continue
                    module_name = filename[:-3]
                    classes.append((module_name, node.name))
                    print(f"   ✅ Найден: {node.name} в {filename}")
        except Exception as e:
            print(f"   ⚠️ Ошибка чтения {filename}: {e}")
    return classes


def regenerate_self(
    *,
    current_file: str,
    classes: List[Tuple[str, str]],
    registry_name: str,
    on_missing_separator: Optional[Callable[[], None]] = None,
) -> None:
    """Читает ``current_file``, сохраняет часть после ``SEPARATOR`` (ручную
    self-maintenance логику) и перезаписывает файл с новой авто-секцией
    (реестр ``registry_name`` + ленивый ``__getattr__`` поверх ``lazy_attr``).

    Если ``SEPARATOR`` не найден — вызывает ``on_missing_separator`` (если он
    задан) и ничего не пишет на диск.
    """
    with open(current_file, "r", encoding="utf-8") as f:
        content = f.read()

    if SEPARATOR not in content:
        if on_missing_separator is not None:
            on_missing_separator()
        return

    script_logic = content[content.find(SEPARATOR):]

    lines = [
        "# -----------------------------------------------------------------------------",
        "# AUTO-GENERATED IMPORTS - DO NOT EDIT THIS SECTION MANUALLY",
        f"# Run this file as a script to update imports: python {os.path.basename(current_file)}",
        "# -----------------------------------------------------------------------------",
        "",
        'if __name__ != "__main__":',
        f"    {registry_name} = {{",
    ]
    for module, classname in classes:
        lines.append(f'        "{classname}": ".{module}",')
    lines.extend(
        [
            "    }",
            "",
            f"    __all__ = list({registry_name})",
            "",
            "    from gemini_translator.api import lazy_module",
            "",
            "    def __getattr__(name):",
            f"        value = lazy_module.lazy_attr(name, {registry_name}, __name__)",
            "        globals()[name] = value",
            "        return value",
            "",
            "",
        ]
    )

    new_content = "\n".join(lines) + script_logic

    with open(current_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✨ Файл {os.path.basename(current_file)} успешно обновлен!")
