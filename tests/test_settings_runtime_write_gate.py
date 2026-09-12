"""Гейт: состояние квот пишется только в SQLite, не в JSON-кэш настроек.

Пока квоты жили в settings.json, их поля правили присваиванием прямо в записи
ключа. После переезда в SQLite такие присваивания означают второй источник
истины: значение окажется в кэше и в файле, но не в базе — или наоборот,
затрёт то, что база уже знает. Ошибка при этом тихая, тесты поведения её не
ловят, поэтому нужен структурный запрет.

Разрешено присваивать словарь целиком в `status_by_model[model_id]`: так
работает нормализация legacy-записей до импорта, она ничего не публикует.
"""

import ast
import pathlib

RUNTIME_FIELDS = {"exhausted_at", "exhausted_level", "requests"}

SETTINGS_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "gemini_translator" / "utils" / "settings.py"
)


def _subscript_field(node):
    """Возвращает строковый ключ подписки или None."""
    if not isinstance(node, ast.Subscript):
        return None
    index = node.slice
    if isinstance(index, ast.Constant) and isinstance(index.value, str):
        return index.value
    return None


def test_settings_never_assigns_key_runtime_fields_directly():
    tree = ast.parse(SETTINGS_PATH.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            field = _subscript_field(target)
            if field in RUNTIME_FIELDS:
                offenders.append(f"{SETTINGS_PATH.name}:{target.lineno} -> [{field!r}]")

    assert not offenders, (
        "Состояние квот должно меняться только через KeyRuntimeStore. "
        "Прямые присваивания: " + "; ".join(offenders)
    )
