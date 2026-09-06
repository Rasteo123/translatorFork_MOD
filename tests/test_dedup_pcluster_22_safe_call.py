"""pcluster-22: "safely invoke an optional progress/log callback" was
implemented as a separate function at least four times —
``chapter_qa_coordinator.check_all_now``'s ``report_progress`` and
``report_chapter`` closures, ``qa.model_bundle._report``, and
``qa.assembly._report``. This test module:

(a) characterizes the shared idiom's behaviour through the canonical
    ``gemini_translator.utils.callbacks.safe_call``, and
(b) verifies that every former call site now routes through it (by
    monkeypatching the canonical function and observing it get hit).

Part (b) must fail before the refactor (each site had its own private
copy) and pass after it.
"""

from __future__ import annotations

import asyncio
import hashlib

from gemini_translator.utils.callbacks import safe_call


# --- (a) characterization of the canonical function -------------------


def test_safe_call_noop_when_not_callable():
    # Must not raise for None, or any other non-callable.
    safe_call(None, 1, 2, 3)
    safe_call("not a function", 1)


def test_safe_call_forwards_all_positional_args():
    seen = []
    safe_call(lambda *a: seen.append(a), 1, 2, "x")
    assert seen == [(1, 2, "x")]


def test_safe_call_swallows_callback_exception():
    def boom(*_a):
        raise RuntimeError("display exploded")

    # Must not raise even though the callback itself raises.
    safe_call(boom, 1, 2, 3)


def test_safe_call_with_zero_args():
    seen = []
    safe_call(lambda: seen.append("called"))
    assert seen == ["called"]


# --- (b) routing: every former call site now goes through safe_call ---


def test_model_bundle_install_progress_routes_through_safe_call(monkeypatch, tmp_path):
    from gemini_translator.qa import model_bundle as model_bundle_module

    calls = []

    def fake_safe_call(callback, *args):
        calls.append((callback, args))

    monkeypatch.setattr(model_bundle_module, "safe_call", fake_safe_call)

    data = b"weights"
    digest = hashlib.sha256(data).hexdigest()
    manifest = model_bundle_module.ModelBundle(
        version="1",
        files=(
            model_bundle_module.ModelBundleFile(
                name="a.bin", url="http://example/a.bin", size_bytes=len(data), sha256=digest
            ),
        ),
    )

    async def fake_downloader(_url):
        return data

    manager = model_bundle_module.ModelBundleManager(
        root=tmp_path, manifest=manifest, downloader=fake_downloader
    )

    def fake_progress(name, done, total):
        pass

    asyncio.run(manager.install(progress=fake_progress))

    assert calls, "expected install() to route its progress callback through safe_call"
    assert calls[0] == (fake_progress, ("a.bin", len(data), len(data)))


def test_assembly_key_health_report_routes_through_safe_call(monkeypatch):
    from gemini_translator.qa import assembly as assembly_module

    calls = []

    def fake_safe_call(callback, *args):
        calls.append((callback, args))

    monkeypatch.setattr(assembly_module, "safe_call", fake_safe_call)

    class FakeSettingsManager:
        def mark_key_as_exhausted(self, api_key, model_id):
            return None

    logged = []
    health = assembly_module.SettingsEmbeddingKeyHealth(
        FakeSettingsManager(), "embed-model", log=logged.append
    )
    health.mark_exhausted("secret-key-1234", reason="quota")

    assert len(calls) == 1
    assert calls[0][0] == logged.append
    (message,) = calls[0][1]
    assert "1234" in message
    # The real log callback must not have been invoked directly any more
    # (only through the patched safe_call).
    assert logged == []


def test_chapter_qa_report_progress_routes_through_safe_call(monkeypatch):
    from gemini_translator.core import chapter_qa_coordinator as coordinator_module

    calls = []

    def fake_safe_call(callback, *args):
        calls.append((callback, args))

    monkeypatch.setattr(coordinator_module, "safe_call", fake_safe_call)

    coordinator = coordinator_module.ChapterQaCoordinator.__new__(
        coordinator_module.ChapterQaCoordinator
    )
    coordinator._max_concurrency = 1
    coordinator._cancellation = _FakeCancellation()
    coordinator._options = lambda: coordinator_module.QaOptions()

    async def fake_check_one(event, options):
        return None

    coordinator._check_one = fake_check_one

    def on_progress(done, total, chapter_id):
        pass

    events = (_FakeEvent("ch1"), _FakeEvent("ch2"))

    asyncio.run(coordinator.check_all_now(events, on_progress=on_progress))

    progress_calls = [call for call in calls if call[0] is on_progress]
    assert len(progress_calls) == 2
    # safe_call must have been given the up-to-date done/total/chapter_id, in
    # the exact order they occurred (max_concurrency=1 makes this
    # deterministic) — a sorted/multiset comparison would miss a regression
    # where done/total were captured by reference instead of read at call time.
    assert [call[1] for call in progress_calls] == [(1, 2, "ch1"), (2, 2, "ch2")]


def test_chapter_qa_report_chapter_routes_through_safe_call(monkeypatch):
    from gemini_translator.core import chapter_qa_coordinator as coordinator_module

    calls = []

    def fake_safe_call(callback, *args):
        calls.append((callback, args))

    monkeypatch.setattr(coordinator_module, "safe_call", fake_safe_call)

    coordinator = coordinator_module.ChapterQaCoordinator.__new__(
        coordinator_module.ChapterQaCoordinator
    )
    coordinator._max_concurrency = 1
    coordinator._cancellation = _FakeCancellation()
    coordinator._options = lambda: coordinator_module.QaOptions()

    sentinel_result = object()

    async def fake_check_one(event, options):
        return sentinel_result

    coordinator._check_one = fake_check_one

    def on_chapter(result):
        pass

    events = (_FakeEvent("ch1"),)

    asyncio.run(coordinator.check_all_now(events, on_chapter=on_chapter))

    chapter_calls = [call for call in calls if call[0] is on_chapter]
    assert len(chapter_calls) == 1
    assert chapter_calls[0][1] == (sentinel_result,)


def test_chapter_qa_report_chapter_skips_none_result_without_calling_safe_call(
    monkeypatch,
):
    # The old inline guard returned early on ``result is None`` *before*
    # touching the callback at all. Preserve that: safe_call must not even
    # be invoked (with the callback or otherwise) when there is no result.
    from gemini_translator.core import chapter_qa_coordinator as coordinator_module

    calls = []

    def fake_safe_call(callback, *args):
        calls.append((callback, args))

    monkeypatch.setattr(coordinator_module, "safe_call", fake_safe_call)

    coordinator = coordinator_module.ChapterQaCoordinator.__new__(
        coordinator_module.ChapterQaCoordinator
    )
    coordinator._max_concurrency = 1
    coordinator._cancellation = _FakeCancellation()
    coordinator._options = lambda: coordinator_module.QaOptions()

    async def fake_check_one(event, options):
        return None

    coordinator._check_one = fake_check_one

    def on_chapter(result):
        raise AssertionError("must not be called when result is None")

    events = (_FakeEvent("ch1"),)

    asyncio.run(coordinator.check_all_now(events, on_chapter=on_chapter))

    chapter_calls = [call for call in calls if call[0] is on_chapter]
    assert chapter_calls == []


class _FakeCancellation:
    is_cancelled = False


class _FakeEvent:
    def __init__(self, chapter_id: str) -> None:
        self.chapter_id = chapter_id
