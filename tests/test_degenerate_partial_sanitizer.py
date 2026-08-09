import random
import unittest

from gemini_translator.api.errors import PartialGenerationError
from gemini_translator.core.worker_helpers.emerger_tasks import EmergencyTask
from gemini_translator.utils.text import (
    degenerate_repetition_ratio,
    is_degenerate_repetition,
    sanitize_partial_translation,
    strip_degenerate_repeated_tail,
)


_WORDS = (
    "деревня стройка бригада контракт весна отец сын договор посёлок школа дорога мост "
    "камень бетон смета подряд артель бригадир осень зима расчёт прибыль убыток вексель "
    "ссуда банк уезд город столица поезд билет чемодан письмо телеграмма ужин чай двор "
    "яблоня забор колодец лошадь телега мешок зерно мука хлеб печь дым"
).split()

# Разнородная проза: шаблонный текст сам по себе жмётся как вырожденный,
# поэтому фикстура должна быть настолько же неоднородной, как живой перевод.
_rng = random.Random(20260809)
GOOD_PARAGRAPHS = "".join(
    "<p>─ " + " ".join(_rng.choice(_WORDS) for _ in range(14)) + ".</p>\n"
    for _ in range(70)
)
LAST_GOOD_PARAGRAPH = GOOD_PARAGRAPHS.strip().splitlines()[-1]
# Реальная форма отказа: 6018 копий одного и того же абзаца в хвосте ответа.
DEGENERATE_TAIL = "<p>─ …</p>\n" * 6018


class _DummyWorker:
    def __init__(self):
        self.events = []
        self.chunking = True
        self.chunk_on_error = False

    def _post_event(self, name, data=None):
        self.events.append((name, data or {}))

    def log_messages(self):
        return [data.get("message", "") for name, data in self.events if name == "log_message"]


class DegeneracyDetectionTests(unittest.TestCase):
    def test_repeated_block_is_detected_as_degenerate(self):
        self.assertTrue(is_degenerate_repetition(DEGENERATE_TAIL))

    def test_normal_prose_is_not_degenerate(self):
        self.assertFalse(is_degenerate_repetition(GOOD_PARAGRAPHS))

    def test_short_text_is_never_degenerate(self):
        self.assertFalse(is_degenerate_repetition("<p>─ …</p>" * 3))

    def test_ratio_is_none_for_short_text(self):
        self.assertIsNone(degenerate_repetition_ratio("привет"))

    def test_ratio_is_high_for_repeated_block(self):
        ratio = degenerate_repetition_ratio(DEGENERATE_TAIL)
        self.assertIsNotNone(ratio)
        self.assertGreater(ratio, 0.90)


class StripDegenerateTailTests(unittest.TestCase):
    def test_repeated_tail_is_trimmed_to_single_copy(self):
        trimmed, removed = strip_degenerate_repeated_tail(GOOD_PARAGRAPHS + DEGENERATE_TAIL)

        self.assertGreater(removed, 0)
        self.assertTrue(trimmed.startswith(GOOD_PARAGRAPHS[:40]))
        self.assertEqual(trimmed.count("<p>─ …</p>"), 1)
        self.assertIn(LAST_GOOD_PARAGRAPH, trimmed)

    def test_clean_text_is_left_untouched(self):
        trimmed, removed = strip_degenerate_repeated_tail(GOOD_PARAGRAPHS)

        self.assertEqual(removed, 0)
        self.assertEqual(trimmed, GOOD_PARAGRAPHS)

    def test_short_repetition_is_not_trimmed(self):
        text = GOOD_PARAGRAPHS + "<p>─ …</p>\n" * 3
        trimmed, removed = strip_degenerate_repeated_tail(text)

        self.assertEqual(removed, 0)
        self.assertEqual(trimmed, text)


class SanitizePartialTests(unittest.TestCase):
    def test_salvageable_partial_keeps_the_good_prefix(self):
        sanitized = sanitize_partial_translation(GOOD_PARAGRAPHS + DEGENERATE_TAIL)

        self.assertTrue(sanitized)
        self.assertIn(LAST_GOOD_PARAGRAPH, sanitized)
        self.assertFalse(is_degenerate_repetition(sanitized))

    def test_fully_degenerate_partial_is_discarded(self):
        self.assertEqual(sanitize_partial_translation(DEGENERATE_TAIL), "")

    def test_clean_partial_passes_through_unchanged(self):
        self.assertEqual(sanitize_partial_translation(GOOD_PARAGRAPHS), GOOD_PARAGRAPHS)

    def test_empty_partial_stays_empty(self):
        self.assertEqual(sanitize_partial_translation("   "), "")


class MutateTaskForCompletionTests(unittest.TestCase):
    def _chunk_payload(self, *extra):
        return (
            "epub_chunk",
            "book.epub",
            "Text/ch.xhtml",
            "<p>source</p>",
            1,
            2,
            "<html><body>",
            "</body></html>",
            *extra,
        )

    def test_degenerate_partial_is_not_attached_to_payload(self):
        worker = _DummyWorker()
        emerger = EmergencyTask(worker)
        task_info = ("task-id", self._chunk_payload())

        _, payload = emerger._mutate_task_for_completion(
            task_info,
            PartialGenerationError("truncated", partial_text=DEGENERATE_TAIL, reason="MAX_TOKENS"),
        )

        self.assertEqual(len(payload), 8)
        self.assertTrue(
            any("вырожден" in message for message in worker.log_messages()),
            worker.log_messages(),
        )

    def test_degenerate_partial_drops_previously_stored_tail(self):
        worker = _DummyWorker()
        emerger = EmergencyTask(worker)
        task_info = ("task-id", self._chunk_payload("<p>старый хвост</p>"))

        _, payload = emerger._mutate_task_for_completion(
            task_info,
            PartialGenerationError("truncated", partial_text=DEGENERATE_TAIL, reason="MAX_TOKENS"),
        )

        self.assertEqual(len(payload), 8)

    def test_salvageable_partial_is_trimmed_before_storing(self):
        worker = _DummyWorker()
        emerger = EmergencyTask(worker)
        task_info = ("task-id", self._chunk_payload())

        _, payload = emerger._mutate_task_for_completion(
            task_info,
            PartialGenerationError("truncated", partial_text=GOOD_PARAGRAPHS + DEGENERATE_TAIL, reason="MAX_TOKENS"),
        )

        self.assertEqual(len(payload), 9)
        self.assertLess(len(payload[8]), len(GOOD_PARAGRAPHS + DEGENERATE_TAIL))
        self.assertIn(LAST_GOOD_PARAGRAPH, payload[8])
        self.assertFalse(is_degenerate_repetition(payload[8]))

    def test_clean_partial_is_stored_as_before(self):
        worker = _DummyWorker()
        emerger = EmergencyTask(worker)
        task_info = ("task-id", self._chunk_payload())

        _, payload = emerger._mutate_task_for_completion(
            task_info,
            PartialGenerationError("truncated", partial_text=GOOD_PARAGRAPHS, reason="MAX_TOKENS"),
        )

        self.assertEqual(len(payload), 9)
        self.assertIn(LAST_GOOD_PARAGRAPH, payload[8])


if __name__ == "__main__":
    unittest.main()
