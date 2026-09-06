"""
cluster-47: PerplexityBackend._upload_large_text (perplexity.py) заново
реализовывал тот же tempfile-пайплайн загрузки большого текста файлом,
который уже существует как PerplexityUploader.upload_text_as_file
(perplexity_utils/perplexity_uploader.py).

Канонической реализацией остаётся PerplexityUploader.upload_text_as_file:
- (а) характеризационные тесты фиксируют её поведение (дефолтное и
  кастомное имя файла, что возвращается наружу — полный dict, очистка
  временного файла и в успешном, и в аварийном случае);
- (б) тест-маршрутизация проверяет, что _upload_large_text реально идёт
  через upload_text_as_file (а не через свою копию tempfile-цикла +
  upload_file напрямую) и что при этом наружу по-прежнему возвращается
  Optional[str] (url), а не весь dict.
"""
import importlib
import os
import sys

import pytest


# ---------------------------------------------------------------------------
# Фикстура: изолированный импорт gemini_translator...perplexity.
#
# Модуль на импорте создаёт module-level singleton `backend = PerplexityBackend()`,
# который читает файл сессии из реального $HOME пользователя. Чтобы тест был
# герметичным (никаких сетевых вызовов validate_token на настоящий токен),
# на время (пере)импорта подменяем $HOME на пустой tmp_path — тогда файла
# сессии там нет и backend поднимается без токена.
# ---------------------------------------------------------------------------
@pytest.fixture()
def perplexity_module(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PPLX_PORT", "0")
    sys.modules.pop("gemini_translator.api.servers.perplexity", None)
    module = importlib.import_module("gemini_translator.api.servers.perplexity")
    importlib.reload(module)
    yield module
    sys.modules.pop("gemini_translator.api.servers.perplexity", None)


@pytest.fixture()
def uploader_class(perplexity_module):
    return perplexity_module.PerplexityUploader


# ---------------------------------------------------------------------------
# (а) Характеризационные тесты канонической реализации:
#     PerplexityUploader.upload_text_as_file
# ---------------------------------------------------------------------------

def test_upload_text_as_file_default_filename_and_temp_cleanup(uploader_class):
    captured = {}

    def fake_upload_file(self, file_path, filename):
        captured["file_path"] = file_path
        captured["filename"] = filename
        captured["existed_during_call"] = os.path.exists(file_path)
        with open(file_path, "r", encoding="utf-8") as fh:
            captured["written_text"] = fh.read()
        return {"url": "https://s3.example/large_context.txt", "size": 5}

    uploader = uploader_class.__new__(uploader_class)
    uploader.session = object()
    uploader.token = "tok"
    uploader.upload_file = fake_upload_file.__get__(uploader, uploader_class)

    result = uploader.upload_text_as_file("hello")

    assert captured["filename"] == "large_context.txt"
    assert captured["existed_during_call"] is True
    assert captured["written_text"] == "hello"
    assert result == {"url": "https://s3.example/large_context.txt", "size": 5}
    # Временный файл должен быть удалён после успешной загрузки.
    assert not os.path.exists(captured["file_path"])


def test_upload_text_as_file_custom_filename_passed_through(uploader_class):
    captured = {}

    def fake_upload_file(self, file_path, filename):
        captured["filename"] = filename
        return {"url": "https://s3.example/custom.txt"}

    uploader = uploader_class.__new__(uploader_class)
    uploader.session = object()
    uploader.token = "tok"
    uploader.upload_file = fake_upload_file.__get__(uploader, uploader_class)

    result = uploader.upload_text_as_file("text", filename="CONTEXT_123.txt")

    assert captured["filename"] == "CONTEXT_123.txt"
    assert result == {"url": "https://s3.example/custom.txt"}


def test_upload_text_as_file_cleans_up_temp_file_on_error(uploader_class):
    captured = {}

    def failing_upload_file(self, file_path, filename):
        captured["file_path"] = file_path
        raise RuntimeError("network boom")

    uploader = uploader_class.__new__(uploader_class)
    uploader.session = object()
    uploader.token = "tok"
    uploader.upload_file = failing_upload_file.__get__(uploader, uploader_class)

    with pytest.raises(RuntimeError, match="network boom"):
        uploader.upload_text_as_file("text that fails")

    assert not os.path.exists(captured["file_path"])


# ---------------------------------------------------------------------------
# (б) Тест-маршрутизация: PerplexityBackend._upload_large_text обязан идти
# через PerplexityUploader.upload_text_as_file, а не через собственную копию
# tempfile-цикла + upload_file напрямую.
# ---------------------------------------------------------------------------

def test_upload_large_text_routes_through_uploader_upload_text_as_file(
    perplexity_module, monkeypatch
):
    """RED до рефакторинга: _upload_large_text сам пишет tempfile и зовёт
    upload_file напрямую, минуя upload_text_as_file."""
    calls = []

    def fake_upload_text_as_file(self, text, filename="large_context.txt"):
        calls.append((text, filename))
        return {"url": "https://s3.example/ctx.txt", "file_uuid": "abc"}

    def fail_if_called_directly(self, *args, **kwargs):
        raise AssertionError(
            "_upload_large_text должен звать upload_text_as_file, "
            "а не upload_file напрямую"
        )

    monkeypatch.setattr(
        perplexity_module.PerplexityUploader,
        "upload_text_as_file",
        fake_upload_text_as_file,
    )
    monkeypatch.setattr(
        perplexity_module.PerplexityUploader,
        "upload_file",
        fail_if_called_directly,
    )

    backend = perplexity_module.PerplexityBackend()
    result = backend._upload_large_text("large context text", "some-token")

    assert len(calls) == 1, (
        "_upload_large_text должен вызывать "
        "PerplexityUploader.upload_text_as_file ровно один раз"
    )
    text_arg, filename_arg = calls[0]
    assert text_arg == "large context text"
    # Дивергенция сохранена сознательно: имя файла с таймстампом, а не
    # дефолт канонической функции ("large_context.txt").
    assert filename_arg.startswith("CONTEXT_") and filename_arg.endswith(".txt")

    # Наружу по-прежнему возвращается Optional[str] (url), а не весь dict.
    assert result == "https://s3.example/ctx.txt"


def test_upload_large_text_returns_none_without_token(perplexity_module):
    backend = perplexity_module.PerplexityBackend()
    assert backend._upload_large_text("text", "") is None


def test_upload_large_text_does_not_manage_its_own_tempfile(
    perplexity_module, monkeypatch
):
    """После рефакторинга perplexity.py больше не должен сам открывать
    NamedTemporaryFile для этого пайплайна — этим занимается исключительно
    PerplexityUploader.upload_text_as_file."""

    def fake_upload_text_as_file(self, text, filename="large_context.txt"):
        return {"url": "https://s3.example/ctx.txt"}

    monkeypatch.setattr(
        perplexity_module.PerplexityUploader,
        "upload_text_as_file",
        fake_upload_text_as_file,
    )

    def fail_tempfile(*args, **kwargs):
        raise AssertionError(
            "_upload_large_text не должен сам создавать NamedTemporaryFile"
        )

    monkeypatch.setattr(
        perplexity_module.tempfile, "NamedTemporaryFile", fail_tempfile
    )

    backend = perplexity_module.PerplexityBackend()
    result = backend._upload_large_text("text", "tok")

    assert result == "https://s3.example/ctx.txt"
