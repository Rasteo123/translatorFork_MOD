import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import gemini_reader_v3 as reader


class ReaderAudioNormalizationTests(unittest.TestCase):
    def test_sentence_tokenize_does_not_download_punkt_by_default(self):
        fake_nltk = SimpleNamespace(
            sent_tokenize=mock.Mock(side_effect=LookupError("punkt missing")),
            download=mock.Mock(),
        )

        with mock.patch.object(reader, "nltk", fake_nltk), mock.patch.dict(os.environ, {}, clear=True):
            sentences = reader._sentence_tokenize("One sentence. Two sentence.")

        self.assertEqual(sentences, ["One sentence.", "Two sentence."])
        fake_nltk.download.assert_not_called()

    def test_normalize_mp3_file_runs_loudnorm_and_replaces_output(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = os.path.join(tmp_dir, "book_part.mp3")
            with open(audio_path, "wb") as audio_file:
                audio_file.write(b"original")

            calls = []

            def fake_run_subprocess(cmd, **kwargs):
                calls.append((cmd, kwargs))
                with open(cmd[-1], "wb") as normalized_file:
                    normalized_file.write(b"normalized")
                return SimpleNamespace(returncode=0, stderr="", stdout="")

            with mock.patch.object(reader, "_run_subprocess", side_effect=fake_run_subprocess):
                reader._normalize_mp3_file(audio_path, ffmpeg_path="ffmpeg-test")

            with open(audio_path, "rb") as audio_file:
                self.assertEqual(audio_file.read(), b"normalized")
            self.assertEqual(len(calls), 1)

            cmd, kwargs = calls[0]
            self.assertEqual(cmd[0], "ffmpeg-test")
            self.assertIn(reader.READER_AUDIO_NORMALIZE_FILTER, cmd)
            self.assertIn("-map_metadata", cmd)
            self.assertIn("-map_chapters", cmd)
            self.assertIn(reader.READER_MP3_EXPORT_BITRATE, cmd)
            self.assertEqual(kwargs["timeout"], reader.READER_FFMPEG_NORMALIZE_TIMEOUT_SEC)

    def test_youtube_video_audio_export_settings_are_explicit(self):
        self.assertEqual(reader.READER_VIDEO_AUDIO_SAMPLE_RATE, 48000)
        self.assertEqual(reader.READER_VIDEO_AUDIO_BITRATE, "320k")
        self.assertEqual(reader.AUDIO_CHANNELS, 1)

    def test_combine_audio_sequence_encodes_and_normalizes_once(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = os.path.join(tmp_dir, f"seg_00000.{reader.READER_LOSSLESS_TEMP_AUDIO_FORMAT}")
            output_path = os.path.join(tmp_dir, "chapter.mp3")
            with open(input_path, "wb") as audio_file:
                audio_file.write(b"audio")

            calls = []

            def fake_run_subprocess(cmd, **kwargs):
                calls.append((cmd, kwargs))
                with open(cmd[-1], "wb") as output_file:
                    output_file.write(b"combined")
                return SimpleNamespace(returncode=0, stderr="", stdout="")

            with (
                mock.patch.object(reader, "_resolve_tool_path", return_value="ffmpeg-test"),
                mock.patch.object(reader, "_run_subprocess", side_effect=fake_run_subprocess),
            ):
                reader._combine_mp3_sequence([input_path], output_path)

            with open(output_path, "rb") as audio_file:
                self.assertEqual(audio_file.read(), b"combined")
            self.assertEqual(len(calls), 1)

            cmd, kwargs = calls[0]
            self.assertEqual(cmd[0], "ffmpeg-test")
            self.assertIn(reader.READER_AUDIO_NORMALIZE_FILTER, cmd)
            self.assertIn("libmp3lame", cmd)
            self.assertIn(reader.READER_MP3_EXPORT_BITRATE, cmd)
            self.assertIn("mp3", cmd)
            self.assertNotIn("copy", cmd)
            self.assertEqual(kwargs["timeout"], reader.READER_FFMPEG_CONCAT_TIMEOUT_SEC)

    def test_wait_for_request_budget_uses_supplied_token_count_for_tpm(self):
        worker = SimpleNamespace()
        worker._is_running = True
        worker.model_id = "gemini-2.5-flash-preview-tts"
        worker.api_key = ""
        worker.request_rpm_limiter = None
        worker.request_tpm_limiter = reader.TPMLimiter(100)
        worker.daily_request_limiter = None
        worker._estimate_tokens = mock.Mock(return_value=99)
        worker._rpm_required_delay = lambda _limiter=None: 0.0

        async def sleep_interruptibly(_seconds):
            return True

        worker._sleep_interruptibly = sleep_interruptibly

        asyncio.run(
            reader.GeminiWorker._wait_for_request_budget(
                worker,
                "payload",
                token_count=42,
                request_label="flash-tts test",
                rpd_limit=0,
            )
        )

        worker._estimate_tokens.assert_not_called()
        self.assertEqual(worker.request_tpm_limiter._events[-1][1], 42)

    def test_prepare_tts_script_chunks_respects_token_budget(self):
        worker = SimpleNamespace()
        worker.worker_id = 1
        worker.model_id = "gemini-2.5-flash-preview-tts"
        worker.voice_mode = "single"
        worker._tts_chunk_limit = lambda: 1000
        worker._tts_chunk_token_target = lambda: 200
        worker._tts_safe_request_token_limit = lambda: 200
        worker._tts_request_token_limit = lambda: 250
        worker._build_tts_prompt_for_chunk = lambda chunk: chunk
        worker.tts_tpm_limiter = reader.TPMLimiter(250)

        async def fake_count_tokens(_tts_client, prompt_text, force_api=False):
            return len(prompt_text), False

        worker._count_tts_prompt_tokens = fake_count_tokens
        script_text = "word " * 120

        chunks = asyncio.run(reader.FlashTtsWorker._prepare_tts_script_chunks(worker, None, script_text))

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(token_count <= 200 for _, token_count in chunks))

    def test_flash_tts_block_value_is_token_budget(self):
        worker = SimpleNamespace()
        worker.chunk = 1.5
        worker._tts_safe_request_token_limit = lambda: 7000

        token_target = reader.FlashTtsWorker._tts_chunk_token_target(worker)
        worker._tts_chunk_token_target = lambda: token_target
        initial_char_limit = reader.FlashTtsWorker._tts_chunk_limit(worker)

        self.assertEqual(token_target, 1500)
        self.assertEqual(initial_char_limit, int(1500 * reader.FLASH_TTS_TOKEN_TO_CHAR_SPLIT_RATIO))

    def test_yo_prompt_rule_restores_common_words(self):
        preprocess_prompt = reader._build_preprocess_prompt(
            "Еремей идет домой.",
            voice_mode="single",
            profile_prompt="",
            extra_directive="",
        )
        repair_prompt = reader._build_author_gender_repair_prompt(
            "Еремей идет домой.",
            "Author: Еремей идет домой.",
            [],
            profile_prompt="",
            extra_directive="",
        )

        for prompt in (preprocess_prompt, repair_prompt):
            self.assertIn("actively restore the Russian letter Ё/ё", prompt)
            self.assertIn("even if the source writes Е/е without dots", prompt)
            self.assertIn("идёт", prompt)
            self.assertIn("Only avoid guessing Ё/ё in names", prompt)

    def test_flash_tts_preprocess_prompt_uses_sparse_reliable_tags(self):
        preprocess_prompt = reader._build_preprocess_prompt(
            "Он тихо вздохнул. — Пошли, — сказала она.",
            voice_mode="single",
            profile_prompt="",
            extra_directive="",
        )

        self.assertIn("Allowed tag vocabulary:", preprocess_prompt)
        self.assertIn("[sigh]", preprocess_prompt)
        self.assertIn("[whispering]", preprocess_prompt)
        self.assertIn("[short pause]", preprocess_prompt)
        self.assertIn("Most sentences should have no audio tag", preprocess_prompt)
        self.assertIn("never target a tag count or density", preprocess_prompt)
        self.assertIn("Gemini may pronounce them aloud", preprocess_prompt)
        self.assertNotIn("10-18 bracketed tags", preprocess_prompt)
        self.assertNotIn("[hesitates]", reader.TTS_AUDIO_TAG_HINT)
        self.assertNotIn("[angry]", reader.TTS_AUDIO_TAG_HINT)

    def test_flash_tts_generation_prompt_has_explicit_sections_and_spoken_boundary(self):
        script = "Тихо. [short pause] Теперь продолжай."

        prompt = reader._build_tts_generation_prompt(
            script,
            voice_mode="single",
            speed_key="Normal",
        )

        headings = ("# AUDIO PROFILE", "# SCENE", "# DIRECTOR'S NOTES", "# TRANSCRIPT")
        self.assertTrue(prompt.startswith("SYNTHESIS TASK:"))
        self.assertEqual([prompt.index(heading) for heading in headings], sorted(prompt.index(heading) for heading in headings))
        self.assertIn("Speak only the transcript", prompt)
        self.assertIn("never pronounce the brackets or tag names", prompt)
        self.assertTrue(prompt.endswith(script))

    def test_flash_tts_generation_prompt_normalizes_legacy_tags(self):
        prompt = reader._build_tts_generation_prompt(
            "[whispers] Тише. [angry] Стой. [very fast] Бежим.",
            voice_mode="single",
            speed_key="Normal",
        )
        transcript = prompt.split("# TRANSCRIPT\n", 1)[1]

        self.assertIn("[whispering]", transcript)
        self.assertIn("[extremely fast]", transcript)
        self.assertNotIn("[whispers]", transcript)
        self.assertNotIn("[angry]", transcript)
        self.assertEqual(transcript, "[whispering] Тише. Стой. [extremely fast] Бежим.")

    def test_legacy_default_preprocess_directive_migrates_to_sparse_default(self):
        self.assertEqual(
            reader._normalize_preprocess_directive(reader.LEGACY_DEFAULT_PREPROCESS_DIRECTIVE),
            reader.DEFAULT_PREPROCESS_DIRECTIVE,
        )
        self.assertEqual(reader._normalize_preprocess_directive("Custom direction"), "Custom direction")

    def test_duo_prompt_uses_male_female_roles(self):
        preprocess_prompt = reader._build_preprocess_prompt(
            "— Пошли, — сказала она.",
            voice_mode="duo",
            profile_prompt="",
            extra_directive="",
        )
        tts_prompt = reader._build_tts_generation_prompt(
            "Male: Он кивнул.\nFemale: Пошли.",
            voice_mode="duo",
            speed_key="Normal",
        )

        self.assertIn("Male and Female", preprocess_prompt)
        self.assertIn("Use `Female:` only for direct speech", preprocess_prompt)
        self.assertNotIn("Narrator and Dialogue", preprocess_prompt)
        self.assertIn("`Male` is the male/primary voice", tts_prompt)
        self.assertIn("Speaker labels select the voice and are not spoken aloud", tts_prompt)

    def test_duo_script_matching_rejects_legacy_narrator_dialogue(self):
        self.assertTrue(reader._script_matches_voice_mode("Male: ready\nFemale: ready", "duo"))
        self.assertFalse(reader._script_matches_voice_mode("Narrator: ready\nDialogue: ready", "duo"))
        self.assertFalse(reader._script_matches_voice_mode("Author: intro\nMale: ready", "duo"))
        self.assertFalse(reader._script_matches_voice_mode("Male: ready\nFemale: ready", "author_gender"))

    def test_live_duo_fallback_detects_female_dialogue(self):
        script = reader._build_live_role_script("— Пошли, — сказала она.\n\nОн кивнул.")

        self.assertIn("Female: Пошли,", script)
        self.assertIn("Male: сказала она. Он кивнул.", script)


if __name__ == "__main__":
    unittest.main()
