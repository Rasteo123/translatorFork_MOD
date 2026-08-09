# -*- coding: utf-8 -*-
"""JSON для больших кэшей: orjson (Rust), при его отсутствии — stdlib.

Контракт совместимости со stdlib-вызовами вида
`json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)`:
- не-ASCII пишется как есть (у orjson это единственный режим — поэтому модуль
  применим ТОЛЬКО там, где сейчас ensure_ascii=False);
- кортежи сериализуются как массивы (orjson делает это нативно);
- NaN/Infinity orjson пишет как null (валидный JSON; stdlib писал бы
  невалидный литерал NaN) — осознанное расхождение; чтение СТАРЫХ файлов
  с NaN-литералом работает через фолбэк на stdlib;
- int за пределами 64 бит orjson не принимает — фолбэк на stdlib;
- indent поддержан только значением 2 (как во всех наших кэшах); другой
  indent уводит вызов в stdlib-ветку.

НЕ использовать: для payload'ов task_manager (там object_hook
tuple_deserializer) и для строк, из которых считаются fingerprint-хэши
(смена сериализатора инвалидировала бы кэши).
"""

import json as _std_json


def _std_dumps(obj, indent=None, sort_keys=False) -> str:
    return _std_json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=sort_keys)


try:
    import orjson as _orjson

    FAST_JSON_BACKEND = "orjson"

    def dumps(obj, indent=None, sort_keys=False) -> str:
        if indent not in (None, 2):
            return _std_dumps(obj, indent=indent, sort_keys=sort_keys)
        options = 0
        if indent == 2:
            options |= _orjson.OPT_INDENT_2
        if sort_keys:
            options |= _orjson.OPT_SORT_KEYS
        try:
            return _orjson.dumps(obj, option=options).decode("utf-8")
        except (TypeError, _orjson.JSONEncodeError):
            # NaN/Infinity или тип вне JSON-спеки — как раньше через stdlib.
            return _std_dumps(obj, indent=indent, sort_keys=sort_keys)

    def loads(data):
        try:
            return _orjson.loads(data)
        except _orjson.JSONDecodeError:
            # Файлы, записанные stdlib с NaN/Infinity, orjson не принимает.
            return _std_json.loads(data)

except ImportError:
    FAST_JSON_BACKEND = "json"

    def dumps(obj, indent=None, sort_keys=False) -> str:
        return _std_dumps(obj, indent=indent, sort_keys=sort_keys)

    def loads(data):
        return _std_json.loads(data)


def dump(obj, fp, indent=None, sort_keys=False) -> None:
    fp.write(dumps(obj, indent=indent, sort_keys=sort_keys))


def load(fp):
    return loads(fp.read())
