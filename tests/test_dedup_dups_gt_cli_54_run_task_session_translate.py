"""dups-gt_cli-54: command_translate should reuse _run_task_session instead of
re-implementing its body (CliSessionObserver setup, start_session, event_bus
set_data/pop_data, app.exec()).

Characterization tests pin the behaviour of the canonical _run_task_session
(task_chains support + pop_data-on-exception safety) and a routing test
proves command_translate actually calls it.
"""
from argparse import Namespace

import pytest

from gemini_translator import cli
from gemini_translator.cli import TaskPlan, _run_task_session, command_translate


class FakeSignal:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FakeEventBus:
    def __init__(self):
        self.event_posted = FakeSignal()
        self.data = {}
        self.popped = []

    def set_data(self, key, value):
        self.data[key] = value

    def pop_data(self, key, default=None):
        self.popped.append(key)
        return self.data.pop(key, default)


class FakeTaskManager:
    def __init__(self):
        self.pending_tasks = None
        self.pending_task_chains = None
        self.cleared = False

    def clear_all_queues(self):
        self.cleared = True

    def set_pending_tasks(self, payloads):
        self.pending_tasks = payloads

    def set_pending_task_chains(self, task_chains, initial_history=None):
        self.pending_task_chains = task_chains


class FakeApp:
    def __init__(self, *, raise_in_exec=False):
        self.settings_manager = object()
        self.task_manager = FakeTaskManager()
        self.event_bus = FakeEventBus()
        self.exec_called = False
        self._raise_in_exec = raise_in_exec

    def exec(self):
        self.exec_called = True
        if self._raise_in_exec:
            raise RuntimeError("boom")


class FakeQTimer:
    @staticmethod
    def singleShot(ms, callback):
        callback()


class FakeRuntime:
    def __init__(self, app=None):
        self.shutdown_called = False
        self.app = app or FakeApp()
        self.app_main = Namespace(QtCore=Namespace(QTimer=FakeQTimer))

    def bootstrap(self, *, include_engine):
        return self.app

    def shutdown(self):
        self.shutdown_called = True


class FakeObserver:
    instances = []

    def __init__(self, app, *, verbose=False, timeout_sec=None, capture_results=False):
        self.app = app
        self.verbose = verbose
        self.timeout_sec = timeout_sec
        self.capture_results = capture_results
        self.task_results = []
        FakeObserver.instances.append(self)

    def result_payload(self, task_manager):
        return {"finished": True, "timed_out": False, "task_events": {"total": 1, "success": 1, "failed": 0}}


@pytest.fixture(autouse=True)
def _reset_observer_instances():
    FakeObserver.instances = []
    yield
    FakeObserver.instances = []


# --- Characterization tests for the canonical _run_task_session ---


def test_run_task_session_uses_plain_payloads_when_no_task_chains(monkeypatch):
    monkeypatch.setattr(cli, "CliSessionObserver", FakeObserver)
    app = FakeApp()
    runtime = FakeRuntime(app)

    _run_task_session(app, runtime, {"provider": "fake"}, [("epub", "a", "b")], verbose=True, timeout=5)

    assert app.task_manager.cleared is True
    assert app.task_manager.pending_tasks == [("epub", "a", "b")]
    assert app.task_manager.pending_task_chains is None
    assert app.exec_called is True
    assert app.event_bus.popped == ["cli_session_active"]


def test_run_task_session_routes_task_chains_when_provided(monkeypatch):
    """command_translate's distinguishing behaviour: chained payloads must go
    through set_pending_task_chains instead of set_pending_tasks."""
    monkeypatch.setattr(cli, "CliSessionObserver", FakeObserver)
    app = FakeApp()
    runtime = FakeRuntime(app)
    chains = [[("epub", "a", "b")], [("epub", "a", "c")]]

    _run_task_session(
        app,
        runtime,
        {"provider": "fake"},
        payloads=[("epub", "a", "b"), ("epub", "a", "c")],
        task_chains=chains,
        verbose=False,
        timeout=None,
    )

    assert app.task_manager.pending_task_chains == chains
    assert app.task_manager.pending_tasks is None


def test_run_task_session_pops_cli_session_active_even_if_exec_raises(monkeypatch):
    monkeypatch.setattr(cli, "CliSessionObserver", FakeObserver)
    app = FakeApp(raise_in_exec=True)
    runtime = FakeRuntime(app)

    with pytest.raises(RuntimeError):
        _run_task_session(app, runtime, {"provider": "fake"}, [("epub", "a", "b")])

    assert app.event_bus.popped == ["cli_session_active"]


# --- Routing test: command_translate must delegate to _run_task_session ---


def _patch_translate_plumbing(monkeypatch, app, *, task_chains=None):
    runtime_holder = {}

    class RecordingRuntime(FakeRuntime):
        def __init__(self):
            super().__init__(app)
            runtime_holder["runtime"] = self

    monkeypatch.setattr(cli, "HeadlessRuntime", RecordingRuntime)
    monkeypatch.setattr(cli, "_project_manager", lambda project_folder: object())
    monkeypatch.setattr(cli, "select_chapters", lambda *a, **k: ["OEBPS/ch1.xhtml"])
    monkeypatch.setattr(cli, "build_session_settings", lambda *a, **k: {"provider": "fake"})
    monkeypatch.setattr(
        cli,
        "build_task_plan",
        lambda epub_path, chapters, settings, project_manager=None: TaskPlan(
            chapters=chapters,
            payloads=[("epub", epub_path, chapters[0])],
            task_chains=task_chains or [],
            settings=settings,
            summary={"task_count": 1},
        ),
    )
    return runtime_holder


def _translate_args(tmp_path):
    return Namespace(
        project=str(tmp_path / "project"),
        epub=str(tmp_path / "book.epub"),
        chapters="pending",
        chapter=[],
        offset=0,
        limit=None,
        verbose=False,
        timeout=None,
    )


def test_command_translate_routes_through_run_task_session(monkeypatch, tmp_path):
    """This is the RED/GREEN routing test: before the fix command_translate
    builds its own CliSessionObserver/app.exec() block and never calls
    cli._run_task_session, so patching that name has no effect and this
    assertion fails. After routing through it, the call is observed."""
    app = FakeApp()
    monkeypatch.setattr(cli, "CliSessionObserver", FakeObserver)
    _patch_translate_plumbing(monkeypatch, app)

    calls = []
    real_run_task_session = cli._run_task_session

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_run_task_session(*args, **kwargs)

    monkeypatch.setattr(cli, "_run_task_session", spy)

    payload = command_translate(_translate_args(tmp_path))

    assert len(calls) == 1, "command_translate did not call _run_task_session"
    assert payload["ok"] is True
    assert payload["status"] == "finished"


def test_command_translate_passes_task_chains_through(monkeypatch, tmp_path):
    app = FakeApp()
    monkeypatch.setattr(cli, "CliSessionObserver", FakeObserver)
    chains = [[("epub", str(tmp_path / "book.epub"), "OEBPS/ch1.xhtml")]]
    _patch_translate_plumbing(monkeypatch, app, task_chains=chains)

    command_translate(_translate_args(tmp_path))

    assert app.task_manager.pending_task_chains == chains
    assert app.task_manager.pending_tasks is None


def test_command_translate_pops_cli_session_active(monkeypatch, tmp_path):
    app = FakeApp()
    monkeypatch.setattr(cli, "CliSessionObserver", FakeObserver)
    _patch_translate_plumbing(monkeypatch, app)

    command_translate(_translate_args(tmp_path))

    assert app.event_bus.popped == ["cli_session_active"]
