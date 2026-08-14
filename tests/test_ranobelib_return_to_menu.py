import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
RANOBELIB_DIR = os.path.join(PROJECT_ROOT, "ranobelib")

if RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, RANOBELIB_DIR)

from main_window import RanobeUploaderApp


class _RanobeUploaderHarness:
    _return_to_menu = RanobeUploaderApp._return_to_menu

    def __init__(self, handler=None):
        self._return_to_menu_handler = handler
        self.calls = []

    def _save_settings(self):
        self.calls.append("save")

    def hide(self):
        self.calls.append("hide")

    def close(self):
        self.calls.append("close")


class _Field:
    def __init__(self, value=""):
        self._value = value

    def text(self):
        return self._value

    def setText(self, value):
        self._value = value

    def clear(self):
        self._value = ""

    def toPlainText(self):
        return self._value

    def setPlainText(self, value):
        self._value = value


class _ButtonField:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, enabled):
        self.enabled = enabled


class _SettingsStub:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, fallback=None, type=None):
        return self.values.get(key, fallback)

    def setValue(self, key, value):
        self.values[key] = value


class _RanobeMediaMetadataHarness:
    _apply_rulate_media_metadata = RanobeUploaderApp._apply_rulate_media_metadata
    _settings_text = RanobeUploaderApp._settings_text
    _media_translator_team_text = RanobeUploaderApp._media_translator_team_text

    def __init__(self, translator_team="", saved_team=""):
        self.settings = _SettingsStub({"media_translator_team": saved_team})
        self._rulate_media_metadata = {}
        self.logs = []
        self.saved = False
        self.codex_cover_cleared = False
        self.media_rulate_url_input = _Field()
        self.media_title_ru_edit = _Field()
        self.media_original_title_edit = _Field()
        self.media_alt_hieroglyph_edit = _Field()
        self.media_title_en_edit = _Field()
        self.media_alt_names_edit = _Field()
        self.media_author_edit = _Field()
        self.media_publisher_edit = _Field()
        self.media_translator_team_edit = _Field(translator_team)
        self.media_source_url_edit = _Field()
        self.media_cover_url_edit = _Field()
        self.media_year_edit = _Field()
        self.media_description_edit = _Field()
        self.media_rulate_genres_edit = _Field()
        self.media_rulate_tags_edit = _Field()
        self.media_genres_edit = _Field("old genres")
        self.media_tags_edit = _Field("old tags")
        self.media_status_combo = object()

    def _refresh_media_cover_preview(self):
        pass

    def _set_combo_data(self, combo, value):
        self.combo_value = value

    def _save_rulate_media_state(self, sync=False):
        self.saved = sync

    def _clear_media_codex_cover(self):
        self.codex_cover_cleared = True

    def _process_log(self, process_key, level, message):
        self.logs.append((process_key, level, message))


class _SignalStub:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _CodexCoverWorkerStub:
    created = None

    def __init__(self, cover_url, title_ru, **kwargs):
        self.cover_url = cover_url
        self.title_ru = title_ru
        self.kwargs = kwargs
        self.log_signal = _SignalStub()
        self.cover_ready = _SignalStub()
        self.finished_signal = _SignalStub()
        self.started = False
        type(self).created = self

    def start(self):
        self.started = True


class _CodexCoverChainHarness:
    _on_media_source_cover_fetch_finished = RanobeUploaderApp._on_media_source_cover_fetch_finished

    def __init__(self):
        self._media_source_cover_fetch_worker = object()
        self._media_source_cover_metadata = SimpleNamespace(
            cover_url="https://bookcover.yuewen.com/original.jpg",
            cover_image_data=b"original-cover",
        )
        self._media_cover_request_source_url = "https://www.qidian.com/book/123/"
        self._media_cover_request_title_ru = "Русское название"
        self._media_codex_cover_worker = None
        self.preview = None

    def _set_media_codex_cover_preview(self, image_path, placeholder=""):
        self.preview = (image_path, placeholder)

    def _process_log(self, *_args):
        pass

    def _apply_media_codex_cover(self, _path):
        pass

    def _on_media_codex_cover_finished(self):
        pass


class _LoginButtonsHarness:
    _set_ranobelib_login_buttons_enabled = (
        RanobeUploaderApp._set_ranobelib_login_buttons_enabled
    )

    def __init__(self):
        self.btn_login = _ButtonField()
        self.btn_media_login_ranobelib = _ButtonField()


class RanobeUploaderReturnToMenuTests(unittest.TestCase):
    def test_both_ranobelib_login_buttons_follow_same_worker_state(self):
        harness = _LoginButtonsHarness()

        harness._set_ranobelib_login_buttons_enabled(False)
        self.assertFalse(harness.btn_login.enabled)
        self.assertFalse(harness.btn_media_login_ranobelib.enabled)

        harness._set_ranobelib_login_buttons_enabled(True)
        self.assertTrue(harness.btn_login.enabled)
        self.assertTrue(harness.btn_media_login_ranobelib.enabled)

    def test_return_to_menu_closes_window_before_handler(self):
        handler_calls = []

        harness = _RanobeUploaderHarness(
            handler=lambda: handler_calls.append("handler")
        )

        harness._return_to_menu()

        self.assertEqual(harness.calls, ["save", "hide", "close"])
        self.assertEqual(handler_calls, ["handler"])

    def test_return_to_menu_without_handler_just_closes_window(self):
        harness = _RanobeUploaderHarness()

        harness._return_to_menu()

        self.assertEqual(harness.calls, ["save", "close"])

    def test_rulate_media_metadata_preserves_saved_translator_team_when_new_metadata_is_empty(self):
        harness = _RanobeMediaMetadataHarness(translator_team="Required Team")

        harness._apply_rulate_media_metadata(
            {
                "rulate_edit_url": "https://tl.rulate.ru/book/123/edit/info",
                "title_ru": "Новая новелла",
                "source_url": "https://www.qidian.com/book/1041604040/",
            }
        )

        self.assertEqual(harness.media_translator_team_edit.text(), "Required Team")
        self.assertEqual(harness.settings.values["media_translator_team"], "Required Team")
        self.assertEqual(harness._rulate_media_metadata["translator_team"], "Required Team")
        self.assertEqual(
            harness.media_source_url_edit.text(),
            "https://www.qidian.com/book/1041604040/",
        )

    def test_new_rulate_source_discards_translated_cover_from_previous_book(self):
        harness = _RanobeMediaMetadataHarness()
        harness._rulate_media_metadata = {
            "source_url": "https://www.qidian.com/book/111/",
        }

        harness._apply_rulate_media_metadata(
            {
                "rulate_edit_url": "https://tl.rulate.ru/book/456/edit/info",
                "title_ru": "Другая новелла",
                "source_url": "https://www.qidian.com/book/222/",
            }
        )

        self.assertTrue(harness.codex_cover_cleared)

    def test_source_cover_metadata_is_forwarded_to_codex_translation(self):
        harness = _CodexCoverChainHarness()

        with patch("main_window.CodexCoverTranslateWorker", _CodexCoverWorkerStub):
            harness._on_media_source_cover_fetch_finished()

        worker = _CodexCoverWorkerStub.created
        self.assertIsNotNone(worker)
        self.assertTrue(worker.started)
        self.assertEqual(worker.cover_url, "https://bookcover.yuewen.com/original.jpg")
        self.assertEqual(worker.title_ru, "Русское название")
        self.assertEqual(worker.kwargs["referer"], "https://www.qidian.com/book/123/")
        self.assertEqual(worker.kwargs["source_image_data"], b"original-cover")


if __name__ == "__main__":
    unittest.main()
