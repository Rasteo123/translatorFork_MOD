# -*- coding: utf-8 -*-

from __future__ import annotations

from gemini_translator.utils.text import split_csv


def _split_csv(text: str) -> list[str]:
    # Сохраняем прежнее поведение этого места вызова (жанры/теги): дедуп,
    # и ';' НЕ считается разделителем (в отличие от канонического default) -
    # см. pcluster-20.
    return split_csv(text, delimiters=r"[,\n]+", dedupe=True)
