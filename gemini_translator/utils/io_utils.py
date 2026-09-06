"""Canonical atomic file writes.

Три независимые копии одной и той же схемы (temp-файл в той же директории +
fsync + ``os.replace``) — ``gemini_translator/mcp/ai_bridge.py``,
``gemini_translator/ui/dialogs/chapter_editor.py`` и
``gemini_translator/qa/repair_store.py`` — сведены сюда (dedup cluster-43).

Каноническая реализация берёт самое строгое поведение из всех трёх копий:

* временный файл всегда получает случайный уникальный суффикс
  (``secrets.token_hex``), как в ``ai_bridge.py`` — фиксированное имя
  ``<path>.tmp`` (старый ``repair_store.py``) уязвимо к гонке при
  параллельной записи в один и тот же путь;
* ``flush()`` + ``os.fsync()`` перед ``os.replace`` выполняются всегда
  (как в ``chapter_editor.py``/``repair_store.py``) — без этого сбой
  процесса/ОС между записью и заменой может потерять данные на диске;
* права доступа (``chmod``) — опциональный параметр ``mode``, а не
  встроенное поведение: только вызывающая сторона знает, чувствительны ли
  данные (``ai_bridge.py`` этого хотел, остальные — нет).

Не реализовывайте атомарную запись заново — импортируйте отсюда.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets


def atomic_write_bytes(
    path: Path | str,
    data: bytes,
    *,
    fsync: bool = True,
    mode: int | None = None,
) -> None:
    """Заменить ``path`` содержимым ``data`` так, чтобы сбой не оставил частичный файл.

    Пишет во временный файл с уникальным именем в той же директории (чтобы
    ``os.replace`` был атомарным на той же файловой системе и не столкнулся с
    параллельной записью по тому же пути), затем опционально выставляет
    права доступа и заменяет ``path``. Временный файл подчищается и при
    ошибке.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f"{target.name}.{secrets.token_hex(8)}.tmp")
    try:
        with open(temp_path, "wb") as handle:
            handle.write(data)
            if fsync:
                handle.flush()
                os.fsync(handle.fileno())
        if mode is not None:
            try:
                temp_path.chmod(mode)
            except OSError:
                pass
        os.replace(temp_path, target)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def atomic_write_text(
    path: Path | str,
    text: str,
    *,
    encoding: str = "utf-8",
    fsync: bool = True,
    mode: int | None = None,
) -> None:
    """Текстовый вариант :func:`atomic_write_bytes`.

    Кодирует ``text`` в байты без трансляции переводов строк (эквивалент
    записи в текстовом режиме с ``newline=""``) и передаёт дальше в
    :func:`atomic_write_bytes`.
    """
    atomic_write_bytes(path, text.encode(encoding), fsync=fsync, mode=mode)
