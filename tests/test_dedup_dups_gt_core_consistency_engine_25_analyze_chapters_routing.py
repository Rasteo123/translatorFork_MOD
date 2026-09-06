"""
Дедуп-находка core-b/design/1-analyze-chapters-duplicated-se (dups-gt_core_consistency_engine-25).

Последовательный проход analyze_chapters дублировал логику, уже вынесенную
в _analyze_chunk_request / _handle_analysis_response для параллельного
(fast_proofread) режима. Этот тест-маршрутизация обязан ПАДАТЬ до рефакторинга
(последовательный путь вызывал _call_api_with_transient_chunk_retry напрямую,
инлайново повторяя логику) и ПРОХОДИТЬ после (последовательный путь идёт через
те же два метода, что и параллельный).
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.core.consistency_engine import ConsistencyEngine


class _RetrySettingsStub:
    def is_key_limit_active(self, key_info, model_id):
        return False

    def load_proxy_settings(self):
        return None

    def increment_request_count(self, key_to_update, model_id):
        return True

    def mark_key_as_exhausted(self, key_to_mark, model_id):
        return True


class AnalyzeChaptersRoutingTests(unittest.TestCase):
    def test_sequential_path_routes_through_shared_request_and_handle_helpers(self):
        """analyze_chapters (последовательный, не fast_proofread) должен звать
        _analyze_chunk_request и _handle_analysis_response — те же хелперы,
        которыми уже пользуется параллельный fast_proofread путь — вместо
        собственной инлайн-копии этой логики."""
        settings = _RetrySettingsStub()
        engine = ConsistencyEngine(settings)

        request_calls = []
        handle_calls = []

        def fake_request(chunk, config, active_keys, *, chunk_index, total_chunks):
            request_calls.append((chunk_index, total_chunks))
            return ("FAKE_PROMPT", "FAKE_RESPONSE")

        def fake_handle(*, chunk, chunk_index, total_chunks, mode, config, prompt, response_text):
            handle_calls.append((chunk_index, total_chunks, mode, prompt, response_text))
            # Достаточно правдоподобно воспроизводим побочный эффект реального
            # _handle_analysis_response, чтобы проверить, что накопление
            # проблем идёт именно через него.
            engine._store_analysis_result(
                {
                    "problems": [{"chapter": "chapter_01.xhtml", "id": 1}],
                    "glossary_update": {},
                    "context_summary": {},
                },
                chunk,
                chunk_index,
            )

        engine._analyze_chunk_request = fake_request
        engine._handle_analysis_response = fake_handle

        # engine._call_api не задан намеренно: если последовательный путь
        # по-прежнему обходит _analyze_chunk_request и бьёт в API напрямую
        # (старая инлайн-копия), вызов упадёт / ничего не накопит, и
        # request_calls/handle_calls останутся пустыми -> тест провалится.
        engine.analyze_chapters(
            [{"name": "chapter_01.xhtml", "content": "Text", "path": "chapter_01.xhtml"}],
            {"chunk_size": 1, "provider": "gemini", "model": "gemini-2.0-flash-exp"},
            ["key-1"],
        )

        self.assertEqual(request_calls, [(0, 1)])
        self.assertEqual(handle_calls[0][0], 0)
        self.assertEqual(handle_calls[0][1], 1)
        self.assertEqual(handle_calls[0][3], "FAKE_PROMPT")
        self.assertEqual(handle_calls[0][4], "FAKE_RESPONSE")
        self.assertEqual(len(engine.all_problems), 1)
        self.assertEqual(engine.all_problems[0]["chunk_index"], 0)


if __name__ == "__main__":
    unittest.main()
