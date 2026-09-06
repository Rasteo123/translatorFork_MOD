"""Общая процедура чтения task/job JSON-файлов с диска.

Выносит дубликат из ``jobs.load_job`` и ``ai_bridge.load_gui_ai_task``
(cluster-19): обе функции читали JSON-файл и парсили его через свой
``from_dict`` абсолютно одинаковым образом, различаясь только
path-функцией и dataclass'ом. Модуль нейтральный (не импортирует ни
jobs, ни ai_bridge), чтобы не создавать цикл между ними.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")


def load_task_json(path: Path, from_dict: Callable[[dict], T]) -> T:
    """Прочитать JSON-файл задачи и разобрать его через ``from_dict``.

    Ошибки не перехватываются: ``FileNotFoundError``/``json.JSONDecodeError``
    (а также любое исключение из ``from_dict``) пробрасываются наружу без
    изменений — так вели себя обе исходные копии.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    return from_dict(payload)
