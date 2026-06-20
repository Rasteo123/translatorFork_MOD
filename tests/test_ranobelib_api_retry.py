import os
import sys
from datetime import datetime


TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
RANOBELIB_DIR = os.path.join(PROJECT_ROOT, "ranobelib")

if RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, RANOBELIB_DIR)

import api_upload
from api_upload import ApiUploadWorker
from models import ChapterData


def _api_worker(chapter):
    return ApiUploadWorker(
        "https://ranobelib.me/ru/book/1--test-book/add-chapter",
        [chapter],
        schedule_enabled=False,
        start_time=datetime(2026, 1, 1, 12, 0),
        interval_minutes=10,
        paid_enabled=False,
        price=0,
        force_num=True,
    )


def test_api_upload_retries_chapter_after_transient_error(monkeypatch):
    chapter = ChapterData("1", 2.0, "Retry chapter", "Chapter body")
    worker = _api_worker(chapter)
    requests = []

    def fake_json_request(pathname, **kwargs):
        requests.append((pathname, kwargs))
        if len(requests) == 1:
            raise RuntimeError("temporary API failure")
        return {"data": {"id": 42}}

    monkeypatch.setattr(api_upload, "_json_request", fake_json_request)
    monkeypatch.setattr(api_upload, "RETRY_DELAY_SEC", 0)

    existing_chapters = [
        {
            "volume": "1",
            "number": "1",
            "branch_id": 7,
            "branches": [{"branch_id": 7, "teams": [{"id": 100}]}],
            "teams": [{"id": 100}],
        }
    ]
    existing_keys = {api_upload._chapter_identity("1", "1")}

    handled = worker._upload_chapter(
        0,
        chapter,
        manga_id=1,
        token={"access_token": "token"},
        existing_chapters=existing_chapters,
        existing_keys=existing_keys,
        auth_team_ids=[100],
    )

    assert handled is True
    assert len(requests) == 2
    assert worker._ok == 1
    assert worker._errors == 0
    assert api_upload._chapter_identity("1", 2.0) in existing_keys
