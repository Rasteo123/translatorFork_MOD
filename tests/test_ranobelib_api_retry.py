import os
import sys
from datetime import datetime, timedelta, timezone


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


def test_format_publish_at_converts_aware_datetime_to_utc():
    publish_at = datetime(
        2026,
        7,
        30,
        9,
        10,
        tzinfo=timezone(timedelta(hours=4)),
    )

    assert api_upload._format_publish_at(publish_at) == "2026-07-30 05:10:00"


def test_format_publish_at_treats_naive_datetime_as_local_time():
    publish_at = datetime(2026, 7, 30, 9, 10)
    expected = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:00")

    assert api_upload._format_publish_at(publish_at) == expected


def test_format_publish_at_preserves_empty_schedule():
    assert api_upload._format_publish_at(None) is None


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


def test_existing_chapter_identities_keep_restarted_numbers_separate():
    existing = [{"volume": "1", "number": "1"}]

    keys = api_upload.existing_chapter_identities(existing)

    assert api_upload.chapter_identity("1", 1.0) in keys
    assert api_upload.chapter_identity("2", 1.0) not in keys


def test_latest_existing_chapter_uses_volume_before_number():
    chapters = [
        {"volume": "1", "number": "100"},
        {"volume": "2", "number": "1"},
    ]

    latest = api_upload.latest_existing_chapter(chapters)

    assert latest == {"volume": "2", "number": "1"}


def test_api_upload_stops_before_schedule_goes_past_sixty_days(monkeypatch):
    chapters = [
        ChapterData("1", 1.0, "Too late", "Chapter body"),
        ChapterData("1", 2.0, "Also too late", "Chapter body"),
    ]
    worker = ApiUploadWorker(
        "https://ranobelib.me/ru/book/1--test-book/add-chapter",
        chapters,
        schedule_enabled=True,
        start_time=datetime.now() + timedelta(days=61),
        interval_minutes=10,
        paid_enabled=False,
        price=0,
        force_num=True,
    )
    requests = []

    monkeypatch.setattr(
        api_upload,
        "resolve_api_auth",
        lambda slug: ({"access_token": "token"}, {"teams": [{"id": 100}]}),
    )
    monkeypatch.setattr(api_upload, "fetch_existing_chapters", lambda slug: [])
    monkeypatch.setattr(api_upload, "_json_request", lambda *args, **kwargs: requests.append(args))

    worker.run()

    assert requests == []
    assert worker._ok == 0
    assert worker._errors == 0
    assert worker._skipped == 2


def test_api_upload_does_not_retry_publish_at_limit(monkeypatch):
    chapter = ChapterData("1", 2.0, "Too late", "Chapter body")
    worker = _api_worker(chapter)
    worker.schedule_enabled = True
    requests = []

    def fake_json_request(pathname, **kwargs):
        requests.append((pathname, kwargs))
        raise api_upload.RanobeLibApiError(
            "POST",
            "/chapters",
            422,
            {"data": {"publish_at": ["must be before limit"]}},
        )

    monkeypatch.setattr(api_upload, "_json_request", fake_json_request)

    handled = worker._upload_chapter(
        0,
        chapter,
        manga_id=1,
        token={"access_token": "token"},
        existing_chapters=[
            {
                "volume": "1",
                "number": "1",
                "branch_id": 7,
                "branches": [{"branch_id": 7, "teams": [{"id": 100}]}],
                "teams": [{"id": 100}],
            }
        ],
        existing_keys={api_upload._chapter_identity("1", "1")},
        auth_team_ids=[100],
    )

    assert handled is True
    assert len(requests) == 1
    assert worker.is_running is False
    assert worker._skipped == 1
    assert worker._errors == 0
