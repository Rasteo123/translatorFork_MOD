# -*- coding: utf-8 -*-

from __future__ import annotations


def _split_csv(text: str) -> list[str]:
    result = []
    for part in (text or "").replace("\n", ",").split(","):
        item = part.strip()
        if item and item not in result:
            result.append(item)
    return result
