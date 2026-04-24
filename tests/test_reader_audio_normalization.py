import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import gemini_reader_v3 as reader


class ReaderAudioNormalizationTests(unittest.TestCase):
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
        self.assertEqual(reader.READER_VIDEO_AUDIO_BITRATE, "192k")
        self.assertEqual(reader.AUDIO_CHANNELS, 1)


if __name__ == "__main__":
    unittest.main()
