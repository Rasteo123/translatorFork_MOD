# -*- coding: utf-8 -*-
"""Общий рендер лейбла статуса прокси (cluster-45).

``HomePage._update_proxy_display`` и ``InitialSetupDialog._update_proxy_display``
были посимвольно одинаковой копией (текст статуса + tooltip), но со временем
разошлись: версия в ``InitialSetupDialog`` докрашивает лейбл через
``theme_manager`` (зелёный при активном прокси, приглушённый при выключенном),
а версия в ``HomePage`` — нет. Раскраска покрыта тестом
(``tests/test_translator_only_proxy_controls.py``), поэтому она и есть
каноническое поведение; вынесена сюда единственная реализация, оба места
вызывают ``render_proxy_status(label, settings)`` со своим ``QLabel``.
"""
from __future__ import annotations

from .. import theme_manager


def render_proxy_status(label, settings: dict) -> None:
    """Обновляет текст/tooltip/цвет ``label`` по словарю настроек прокси.

    ``settings`` — то же, что возвращает ``SettingsManager.load_proxy_settings()``
    (или событие ``current_proxy_status``): ``enabled``, ``type``, ``host``,
    ``port``, необязательный ``user``. Пароль (``pass``) сюда не попадает.
    """
    enabled = bool(settings.get("enabled", False))
    proxy_type = str(settings.get("type") or "SOCKS5")
    host = str(settings.get("host") or "")
    port = str(settings.get("port") or "")
    user = str(settings.get("user") or "")

    if enabled and host and port:
        label.setText(f"Прокси: {proxy_type}://{host}:{port}")
        tooltip_lines = [f"Тип: {proxy_type}", f"Хост: {host}", f"Порт: {port}"]
        if user:
            tooltip_lines.append(f"Пользователь: {user}")
        label.setToolTip("\n".join(tooltip_lines))
        label.setStyleSheet(f"color: {theme_manager.color('success')};")
    else:
        label.setText("Прокси: выключен")
        label.setToolTip("Сетевые запросы идут без прокси.")
        label.setStyleSheet(f"color: {theme_manager.color('text_muted')};")
