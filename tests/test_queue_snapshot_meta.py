import unittest

from gemini_translator.core.task_manager import build_queue_snapshot_meta


class QueueSnapshotMetaTests(unittest.TestCase):
    def test_build_queue_snapshot_meta_counts_completed_in_saved_total(self):
        meta = build_queue_snapshot_meta({
            "pending": 2,
            "in_progress": 1,
            "failed": 3,
            "completed": 4,
            "held": 5,
        }, saved_at=123.0)

        self.assertEqual(meta["saved_at"], "123.0")
        self.assertEqual(meta["recoverable_tasks"], "11")
        self.assertEqual(meta["saved_task_count"], "15")


if __name__ == "__main__":
    unittest.main()
