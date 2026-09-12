# Remote CometKiwi Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let CometKiwi score chapters on a Windows PC with an NVIDIA GPU reached over the home Wi-Fi, while the existing local-subprocess path keeps working unchanged.

**Architecture:** `CometKiwiRunnerConfig` gains an `endpoint` field. Empty means today's behaviour — a local subprocess. Set means the estimator swaps its transport for an HTTP one on the same injection point (`run_process`), so `estimate()` itself does not change. On the PC a small stdlib HTTP server answers the same `schema_version: 1` protocol and keeps the loaded model in memory.

**Tech Stack:** Python 3.11, `aiohttp` (already a hard dependency) for the client transport, `http.server.ThreadingHTTPServer` for the PC side, `pytest` + `asyncio.run` for tests, `unbabel-comet` + PyTorch on the PC only.

**Spec:** `docs/superpowers/specs/2026-09-12-remote-cometkiwi-design.md`

## Global Constraints

- The wire protocol is frozen at `schema_version: 1`. No field is added, removed, or renamed in the request or the answer.
- The local subprocess path must keep working with no change in behaviour. Every existing test in `tests/qa/test_cometkiwi_estimator.py` and `tests/qa/test_cometkiwi_runner_protocol.py` must stay green without being edited.
- Every failure reaches the caller as `unavailable(ESTIMATOR_NAME, model, reason)`. An estimate never raises into the QA session and never stops a run. The one exception is `asyncio.CancelledError`, which is always re-raised.
- Chapter text never appears in a log, an exception message, or a server response. Reasons are short fixed identifiers.
- The server takes its model directory and device from its own command line and ignores the `model_dir` and `device` fields of any request.
- No new third-party dependency. `aiohttp` is already in `requirements.txt`; the PC server uses only the standard library plus what the existing runner already needs.
- Run tests with the project interpreter: `.venv/bin/python -m pytest`.
- Comments and docstrings in the application code are written in English, matching the surrounding files. The plan and the spec are in Russian.

---

### Task 1: `endpoint` in the config and a mode-aware readiness check

**Files:**
- Modify: `gemini_translator/qa/estimators/cometkiwi_client.py:36-75`
- Test: `tests/qa/test_cometkiwi_remote.py` (create)

**Interfaces:**
- Consumes: `CometKiwiRunnerConfig`, `QualityEstimateError` from `cometkiwi_client.py`.
- Produces: `CometKiwiRunnerConfig.endpoint: str` (default `""`), `CometKiwiRunnerConfig.is_remote` property returning `bool`, and `setup_problem()` returning `"endpoint_invalid"` for a malformed address.

- [ ] **Step 1: Write the failing test**

Create `tests/qa/test_cometkiwi_remote.py`:

```python
"""Scoring on another machine: the address, the transport, and the refusals."""

from __future__ import annotations

import pytest

from gemini_translator.qa.estimators.cometkiwi_client import CometKiwiRunnerConfig


def _remote(**overrides) -> CometKiwiRunnerConfig:
    values = {
        "runner_path": "",
        "model_dir": "",
        "model": "wmt22-cometkiwi-da",
        "device": "cuda",
        "endpoint": "http://192.168.1.50:8765",
    }
    values.update(overrides)
    return CometKiwiRunnerConfig(**values)


def test_a_remote_config_does_not_want_a_local_runner_or_local_weights():
    """Раннер и веса живут на ПК; требовать их на Mac — выключить возможность."""
    assert _remote().is_remote is True
    assert _remote().setup_problem() == ""


def test_an_empty_endpoint_keeps_the_local_rules():
    config = CometKiwiRunnerConfig(
        runner_path="", model_dir="", model="wmt22-cometkiwi-da"
    )
    assert config.is_remote is False
    assert config.setup_problem() == "runner_missing"


@pytest.mark.parametrize(
    "endpoint",
    [
        "192.168.1.50:8765",        # без схемы
        "ftp://192.168.1.50:8765",  # чужая схема
        "http://",                  # без хоста
        "http://192.168.1.50:0",    # порт вне диапазона
        "http://192.168.1.50:abc",  # порт не число
    ],
)
def test_an_unusable_address_is_named_and_not_dialled(endpoint):
    assert _remote(endpoint=endpoint).setup_problem() == "endpoint_invalid"


def test_the_model_name_is_still_required_remotely():
    """Оценка без имени модели бесполезна при сравнении прогонов."""
    assert _remote(model="").setup_problem() == "model_missing"


def test_a_trailing_slash_does_not_make_a_second_address():
    assert _remote(endpoint="http://192.168.1.50:8765/").score_url() == (
        "http://192.168.1.50:8765/score"
    )
    assert _remote().score_url() == "http://192.168.1.50:8765/score"
    assert _remote().health_url() == "http://192.168.1.50:8765/health"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/qa/test_cometkiwi_remote.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'endpoint'`

- [ ] **Step 3: Write minimal implementation**

In `cometkiwi_client.py`, add the import at the top of the module, next to the existing imports:

```python
from urllib.parse import urlsplit
```

Add the field to `CometKiwiRunnerConfig`, after `timeout_seconds`:

```python
    # Where the runner lives.  Empty means this machine, in a subprocess, which
    # is what every existing installation does.  Set means another machine on
    # the local network answers the same protocol over HTTP: the runner script
    # and the weights are on that machine, and asking for them here would
    # switch the whole capability off for a setup that is perfectly valid.
    endpoint: str = ""
```

Extend `__post_init__`'s string check to include the new field:

```python
        for field_name in ("runner_path", "model_dir", "model", "device", "endpoint"):
```

Add the property and the two URL helpers, and rewrite `setup_problem()`:

```python
    @property
    def is_remote(self) -> bool:
        """Report whether scoring happens on another machine."""
        return bool(self.endpoint.strip())

    def _base_url(self) -> str:
        return self.endpoint.strip().rstrip("/")

    def score_url(self) -> str:
        """The address one scoring request is sent to."""
        return f"{self._base_url()}/score"

    def health_url(self) -> str:
        """The address that answers without loading the model."""
        return f"{self._base_url()}/health"

    def setup_problem(self) -> str:
        """Name the one thing that is missing, or an empty string when ready."""
        if not self.model.strip():
            return "model_missing"
        if self.is_remote:
            return "" if _usable_endpoint(self._base_url()) else "endpoint_invalid"
        if not self.runner_path.strip():
            return "runner_missing"
        if not Path(self.runner_path).is_file():
            return "runner_not_found"
        if not self.model_dir.strip() or not Path(self.model_dir).is_dir():
            return "weights_missing"
        return ""
```

Add the module-level helper below the dataclass:

```python
def _usable_endpoint(url: str) -> bool:
    """Report whether the address can be dialled at all, without dialling it."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "http" or not parts.hostname:
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    return port is None or 1 <= port <= 65535
```

**Note on the reordering:** `model_missing` moves to the front so it applies to both modes. The existing test `tests/qa/test_cometkiwi_estimator.py` builds configs that always carry a model, so this does not change any existing expectation — verify in step 4.

- [ ] **Step 4: Run the new and the existing tests**

Run: `.venv/bin/python -m pytest tests/qa/test_cometkiwi_remote.py tests/qa/test_cometkiwi_estimator.py tests/qa/test_cometkiwi_runner_protocol.py -v`
Expected: PASS, all of them.

- [ ] **Step 5: Commit**

```bash
git add gemini_translator/qa/estimators/cometkiwi_client.py tests/qa/test_cometkiwi_remote.py
git commit -m "feat(qa): let the CometKiwi config name a runner on another machine

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The network transport

**Files:**
- Modify: `gemini_translator/qa/estimators/cometkiwi_client.py:77-96` (the estimator's `__init__`) and the transport section at `:170-203`
- Test: `tests/qa/test_cometkiwi_remote.py` (append)

**Interfaces:**
- Consumes: `CometKiwiRunnerConfig.score_url()`, `RunnerProcessError`, `MAX_RESPONSE_BYTES` from Task 1 and the existing module.
- Produces: `remote_transport(config: CometKiwiRunnerConfig)` returning an async callable with the signature `(command, payload, *, timeout, cancellation=None) -> str`, matching `run_runner_process`.

- [ ] **Step 1: Write the failing test**

Append to `tests/qa/test_cometkiwi_remote.py`:

```python
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gemini_translator.qa.estimators.base import (
    QualityEstimateRequest,
    SourceTranslationWindow,
)
from gemini_translator.qa.estimators.cometkiwi_client import (
    SCHEMA_VERSION,
    CometKiwiEstimator,
    RunnerProcessError,
    remote_transport,
)


class _Server:
    """A real HTTP server on a free port: the transport is worth testing for real."""

    def __init__(self, handler_body) -> None:
        received = self.received = []

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
                length = int(self.headers.get("Content-Length", "0"))
                received.append(json.loads(self.rfile.read(length)))
                status, body = handler_body(received[-1])
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.endpoint = f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self):
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


def _answer(request):
    return 200, json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "request_id": request["request_id"],
            "model": request["model"],
            "device": "cuda",
            "scores": [0.81, 0.63],
        }
    )


def _request() -> QualityEstimateRequest:
    return QualityEstimateRequest(
        chapter_id="chapter-1",
        source_language="zh",
        target_language="ru",
        windows=(
            SourceTranslationWindow("源文本一", "Перевод один"),
            SourceTranslationWindow("源文本二", "Перевод два"),
        ),
    )


def test_a_remote_estimate_sends_the_same_payload_and_reads_the_answer():
    with _Server(_answer) as server:
        estimator = CometKiwiEstimator(
            _remote(endpoint=server.endpoint),
            license_accepted=True,
        )
        estimate = asyncio.run(estimator.estimate(_request()))

    assert estimate.status == "completed"
    sent = server.received[0]
    assert sent["schema_version"] == SCHEMA_VERSION
    assert [segment["translation"] for segment in sent["segments"]] == [
        "Перевод один",
        "Перевод два",
    ]


def test_a_sleeping_pc_skips_the_estimate_and_never_stops_the_run():
    """Выключенный ПК — не ошибка прогона, а отсутствие оценки с честной причиной."""
    # Порт, который никто не слушает: соединение отвергается сразу.
    estimator = CometKiwiEstimator(
        _remote(endpoint="http://127.0.0.1:9"), license_accepted=True
    )
    estimate = asyncio.run(estimator.estimate(_request()))

    assert estimate.status == "unavailable"
    assert estimate.reason == "endpoint_unreachable"


def test_a_server_error_is_a_named_reason_not_an_exception():
    with _Server(lambda request: (500, "boom")) as server:
        estimator = CometKiwiEstimator(
            _remote(endpoint=server.endpoint), license_accepted=True
        )
        estimate = asyncio.run(estimator.estimate(_request()))

    assert estimate.status == "unavailable"
    assert estimate.reason == "endpoint_status_500"


def test_an_oversized_answer_is_refused_rather_than_read():
    body = json.dumps({"padding": "x" * 1_200_000})
    with _Server(lambda request: (200, body)) as server:
        transport = remote_transport(_remote(endpoint=server.endpoint))
        with pytest.raises(RunnerProcessError) as caught:
            asyncio.run(transport((), "{}", timeout=30))

    assert caught.value.reason == "response_too_large"


def test_an_explicit_transport_still_wins_over_the_address():
    """Тесты и будущие транспорты подменяют отправку, как и раньше."""

    async def fake(command, payload, *, timeout, cancellation=None):
        return json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "request_id": json.loads(payload)["request_id"],
                "model": "wmt22-cometkiwi-da",
                "device": "cuda",
                "scores": [0.5, 0.5],
            }
        )

    estimator = CometKiwiEstimator(
        _remote(), license_accepted=True, run_process=fake
    )
    estimate = asyncio.run(estimator.estimate(_request()))

    assert estimate.status == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/qa/test_cometkiwi_remote.py -v`
Expected: FAIL with `ImportError: cannot import name 'remote_transport'`

- [ ] **Step 3: Write minimal implementation**

In `cometkiwi_client.py`, replace the transport selection in `CometKiwiEstimator.__init__`:

```python
        self._run_process = run_process or (
            remote_transport(config) if config.is_remote else run_runner_process
        )
```

Add the transport below `run_runner_process`:

```python
def remote_transport(config: CometKiwiRunnerConfig):
    """Send one request to a runner on another machine, over the local network.

    The signature matches ``run_runner_process`` on purpose: the estimator does
    not know, and must not know, which side of the network answered it.  The
    ``command`` argument is unused here and accepted only to keep that shape.
    """

    url = config.score_url()

    async def send(command, payload, *, timeout, cancellation=None) -> str:
        import aiohttp  # noqa: PLC0415 - only a remote estimate pays for it

        session_timeout = aiohttp.ClientTimeout(total=timeout)
        try:
            async with aiohttp.ClientSession(timeout=session_timeout) as session:
                async with session.post(
                    url,
                    data=payload.encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                ) as response:
                    if response.status != 200:
                        raise RunnerProcessError(
                            f"endpoint_status_{response.status}"
                        )
                    body = await response.content.read(MAX_RESPONSE_BYTES + 1)
        except asyncio.CancelledError:
            raise
        except RunnerProcessError:
            raise
        except (TimeoutError, asyncio.TimeoutError):
            raise TimeoutError("cometkiwi endpoint timed out") from None
        except Exception:  # noqa: BLE001 - every network fault is one reason
            raise RunnerProcessError("endpoint_unreachable") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise RunnerProcessError("response_too_large")
        return body.decode("utf-8", errors="replace")

    return send
```

**Why `aiohttp` is imported inside the function:** the estimator is an optional extra, and a module-level import would make every QA session pay for the HTTP stack whether or not anyone configured an endpoint. This matches the late `from comet import ...` in the runner.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/qa/test_cometkiwi_remote.py tests/qa/test_cometkiwi_estimator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gemini_translator/qa/estimators/cometkiwi_client.py tests/qa/test_cometkiwi_remote.py
git commit -m "feat(qa): score a chapter through a CometKiwi runner on the network

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Split the runner so a loaded model can be kept

**Files:**
- Modify: `tools/translation_qa_cometkiwi_runner.py:80-103`
- Test: `tests/qa/test_cometkiwi_runner_protocol.py` (append)

**Interfaces:**
- Consumes: `RequestError`, `_checkpoint_path` from the same file.
- Produces: `load_model(model_dir: Path | str) -> object` and `score_with(model: object, payload: dict) -> list[float]`. `score(payload)` stays as a thin wrapper so the CLI runner is unchanged.

- [ ] **Step 1: Write the failing test**

Append to `tests/qa/test_cometkiwi_runner_protocol.py`. The module is loaded by path in that file already; reuse the same loader it defines. If the existing loader is a fixture or a helper with a different name, call that one instead — do not add a second loader.

```python
def test_scoring_is_separable_from_loading_so_a_server_can_keep_the_model(tmp_path):
    """Сервер на ПК грузит веса один раз; консольный раннер — как и раньше."""
    runner = _load_runner_module()

    class _Model:
        def __init__(self) -> None:
            self.calls = 0

        def predict(self, data, **kwargs):
            self.calls += 1
            return {"scores": [0.5 for _ in data]}

    model = _Model()
    payload = {
        "segments": [
            {"source": "源", "translation": "Перевод"},
            {"source": "文", "translation": "Текст"},
        ],
        "device": "cuda",
    }

    assert runner.score_with(model, payload) == [0.5, 0.5]
    assert runner.score_with(model, payload) == [0.5, 0.5]
    assert model.calls == 2


def test_the_device_decides_whether_a_gpu_is_asked_for(tmp_path):
    runner = _load_runner_module()
    seen = {}

    class _Model:
        def predict(self, data, **kwargs):
            seen.update(kwargs)
            return {"scores": [0.5]}

    segments = [{"source": "源", "translation": "Перевод"}]
    runner.score_with(_Model(), {"segments": segments, "device": "cuda"})
    assert seen["gpus"] == 1
    runner.score_with(_Model(), {"segments": segments, "device": "cpu"})
    assert seen["gpus"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/qa/test_cometkiwi_runner_protocol.py -k separable -v`
Expected: FAIL with `AttributeError: module has no attribute 'score_with'`

- [ ] **Step 3: Write minimal implementation**

Replace `score()` in `tools/translation_qa_cometkiwi_runner.py` with three functions:

```python
def load_model(model_dir: Path | str):
    """Import the deep-learning stack only now, and load the installed weights.

    Split out of ``score`` so a long-lived server may load once and answer many
    requests; the console runner still loads and scores in one breath.
    """
    from comet import load_from_checkpoint  # noqa: PLC0415 - deliberately late

    checkpoint = _checkpoint_path(Path(model_dir))
    return load_from_checkpoint(str(checkpoint))


def score_with(model, payload: dict) -> list[float]:
    """Score one validated request with a model that is already loaded."""
    data = [
        {"src": segment["source"], "mt": segment["translation"]}
        for segment in payload["segments"]
    ]
    device = str(payload.get("device") or "cpu")
    output = model.predict(
        data,
        batch_size=8,
        gpus=1 if device.startswith("cuda") else 0,
        progress_bar=False,
    )
    scores = getattr(output, "scores", None)
    if scores is None and isinstance(output, dict):
        scores = output.get("scores")
    if scores is None:
        raise RequestError("invalid_model_output")
    return [float(value) for value in scores]


def score(payload: dict) -> list[float]:
    """Load the weights this request names and score it in one call."""
    return score_with(load_model(payload["model_dir"]), payload)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/qa/test_cometkiwi_runner_protocol.py -v`
Expected: PASS, including every test that existed before.

- [ ] **Step 5: Commit**

```bash
git add tools/translation_qa_cometkiwi_runner.py tests/qa/test_cometkiwi_runner_protocol.py
git commit -m "refactor(qa): separate loading the CometKiwi weights from scoring with them

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The server that runs on the PC

**Files:**
- Create: `tools/translation_qa_cometkiwi_server.py`
- Test: `tests/qa/test_cometkiwi_server.py` (create)

**Interfaces:**
- Consumes: `read_request`, `load_model`, `score_with`, `RequestError`, `SCHEMA_VERSION` from `tools/translation_qa_cometkiwi_runner.py`.
- Produces: `ScoringService(model_dir: str, device: str, model: str, loader=load_model)` with `handle(payload: dict) -> dict` and `health() -> dict`; `build_handler(service)` returning a `BaseHTTPRequestHandler` subclass; `main(argv)` for the command line.

**Note on `read_request`:** it ends with a `model_dir.is_dir()` check against the request's own path, which is exactly what the server must not honour. The server therefore validates the payload with its own `validate_payload()` that reuses every rule except that one. Do not call `read_request` from the server.

- [ ] **Step 1: Write the failing test**

Create `tests/qa/test_cometkiwi_server.py`:

```python
"""The PC side: one loaded model, its own weights, and nothing the network says."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


def _load(name: str):
    path = Path(__file__).resolve().parents[2] / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server_module():
    return _load("translation_qa_cometkiwi_server")


class _Model:
    def __init__(self) -> None:
        self.calls = 0

    def predict(self, data, **kwargs):
        self.calls += 1
        return {"scores": [0.7 for _ in data]}


def _service(server_module, model_dir="C:/kiwi/weights", **overrides):
    model = _Model()
    loads = []

    def loader(path):
        loads.append(str(path))
        return model

    values = {"model_dir": model_dir, "device": "cuda", "model": "wmt22-cometkiwi-da"}
    values.update(overrides)
    service = server_module.ScoringService(loader=loader, **values)
    return service, model, loads


def _payload(**overrides):
    values = {
        "schema_version": 1,
        "request_id": "r-1",
        "model": "wmt22-cometkiwi-da",
        "model_dir": "/anything/the/network/says",
        "device": "cpu",
        "source_language": "zh",
        "target_language": "ru",
        "segments": [{"source": "源", "translation": "Перевод"}],
    }
    values.update(overrides)
    return values


def test_the_weights_come_from_the_command_line_never_from_the_request(server_module):
    """Иначе любой узел сети заставит процесс загрузить произвольный чекпоинт."""
    service, _, loads = _service(server_module)

    answer = service.handle(_payload(model_dir="/etc/passwd", device="cpu"))

    assert loads == ["C:/kiwi/weights"]
    assert answer["device"] == "cuda"
    assert answer["scores"] == [0.7]


def test_the_model_is_loaded_once_for_many_requests(server_module):
    service, model, loads = _service(server_module)

    service.handle(_payload(request_id="r-1"))
    service.handle(_payload(request_id="r-2"))

    assert loads == ["C:/kiwi/weights"]
    assert model.calls == 2


def test_a_request_for_another_model_is_answered_with_a_warning_not_a_refusal(
    server_module,
):
    service, _, _ = _service(server_module)

    answer = service.handle(_payload(model="some-other-model"))

    assert answer["scores"] == [0.7]
    assert answer["model"] == "wmt22-cometkiwi-da"
    assert answer["model_mismatch"] == "some-other-model"


@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"schema_version": 2}, "unsupported_schema_version"),
        ({"segments": []}, "invalid_request"),
        ({"segments": [{"source": "源", "translation": "П", "reference": "R"}]},
         "unexpected_segment_field"),
        ({"request_id": ""}, "invalid_request"),
    ],
)
def test_a_bad_request_is_refused_before_the_model_is_touched(
    server_module, payload, reason
):
    service, _, loads = _service(server_module)

    answer = service.handle(_payload(**payload))

    assert answer["error"] == reason
    assert loads == []


def test_health_answers_without_loading_anything(server_module):
    service, _, loads = _service(server_module)

    health = service.health()

    assert health == {
        "schema_version": 1,
        "model": "wmt22-cometkiwi-da",
        "device": "cuda",
        "loaded": False,
    }
    assert loads == []


def test_health_says_when_the_weights_are_in_memory(server_module):
    service, _, _ = _service(server_module)
    service.handle(_payload())

    assert service.health()["loaded"] is True


def test_a_failing_model_answers_a_reason_and_never_the_chapter_text(server_module):
    class _Broken:
        def predict(self, data, **kwargs):
            raise RuntimeError("Перевод один утёк бы сюда")

    service = server_module.ScoringService(
        model_dir="C:/kiwi/weights",
        device="cuda",
        model="wmt22-cometkiwi-da",
        loader=lambda path: _Broken(),
    )

    answer = service.handle(_payload())

    assert answer["error"] == "runtimeerror"
    assert "Перевод" not in json.dumps(answer, ensure_ascii=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/qa/test_cometkiwi_server.py -v`
Expected: FAIL with `FileNotFoundError` for `tools/translation_qa_cometkiwi_server.py`

- [ ] **Step 3: Write minimal implementation**

Create `tools/translation_qa_cometkiwi_server.py`:

```python
#!/usr/bin/env python3
"""Answer COMETKiwi scoring requests over the local network, from one process.

This script is deliberately not part of the application package: like the
console runner beside it, it is meant to run under an interpreter where PyTorch
and ``unbabel-comet`` are installed, on the machine that owns the GPU.

It speaks the same protocol as ``translation_qa_cometkiwi_runner.py``, schema
version 1, over HTTP:

    POST /score    one request object in, one answer object out
    GET  /health   the model, the device, and whether the weights are loaded

Two rules make it safe enough to listen on a home network.  The weights
directory and the device come from this command line and the ``model_dir`` and
``device`` of an incoming request are ignored: otherwise anyone who can reach
the port could make this process load an arbitrary checkpoint file.  And the
text of a chapter never reaches a log or an answer: failures are named by short
identifiers, exactly as the console runner names them.

There is no authentication.  Run it only on a network you trust, and do not
forward its port through a router.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time


def _runner():
    """Load the console runner beside this file and reuse its validation."""
    path = Path(__file__).resolve().parent / "translation_qa_cometkiwi_runner.py"
    spec = importlib.util.spec_from_file_location("cometkiwi_runner", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["cometkiwi_runner"] = module
    spec.loader.exec_module(module)
    return module


_RUNNER = _runner()
SCHEMA_VERSION = _RUNNER.SCHEMA_VERSION
MAX_SEGMENTS = _RUNNER.MAX_SEGMENTS
MAX_REQUEST_BYTES = _RUNNER.MAX_REQUEST_BYTES
RequestError = _RUNNER.RequestError
DEFAULT_PORT = 8765


def validate_payload(payload: object) -> dict:
    """Apply every rule the console runner applies, except the local path one.

    ``read_request`` ends by requiring the request's own ``model_dir`` to exist
    on disk.  Over a network that field is not ours to trust and not ours to
    honour, so it is neither checked nor used.
    """
    if not isinstance(payload, dict):
        raise RequestError("invalid_request")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RequestError("unsupported_schema_version")
    for field in ("request_id", "model"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RequestError("invalid_request")
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise RequestError("invalid_request")
    if len(segments) > MAX_SEGMENTS:
        raise RequestError("too_many_segments")
    for segment in segments:
        if not isinstance(segment, dict):
            raise RequestError("invalid_request")
        if set(segment) - {"source", "translation"}:
            raise RequestError("unexpected_segment_field")
        for field in ("source", "translation"):
            if not isinstance(segment.get(field), str) or not segment[field].strip():
                raise RequestError("invalid_request")
    return payload


class ScoringService:
    """Hold one loaded model and answer one request at a time."""

    def __init__(self, model_dir: str, device: str, model: str, loader=None) -> None:
        self._model_dir = str(model_dir)
        self._device = str(device or "cpu")
        self._model_name = str(model)
        self._loader = loader or _RUNNER.load_model
        self._model = None
        # One GPU cannot usefully run two batches at once, and serialising here
        # is what keeps the memory it needs predictable.
        self._lock = threading.Lock()

    def health(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "model": self._model_name,
            "device": self._device,
            "loaded": self._model is not None,
        }

    def handle(self, payload: object) -> dict:
        request_id = ""
        try:
            request = validate_payload(payload)
            request_id = request["request_id"]
            started = time.monotonic()
            with self._lock:
                if self._model is None:
                    self._model = self._loader(self._model_dir)
                scores = _RUNNER.score_with(
                    self._model, {**request, "device": self._device}
                )
            if len(scores) != len(request["segments"]):
                raise RequestError("score_count_mismatch")
            answer = {
                "schema_version": SCHEMA_VERSION,
                "request_id": request_id,
                "model": self._model_name,
                "device": self._device,
                "scores": scores,
                "runtime_seconds": round(time.monotonic() - started, 3),
            }
            asked = request.get("model")
            if isinstance(asked, str) and asked != self._model_name:
                answer["model_mismatch"] = asked
            return answer
        except RequestError as error:
            return _failure(request_id, str(error))
        except MemoryError:
            return _failure(request_id, "out_of_memory")
        except ImportError:
            return _failure(request_id, "runner_environment_incomplete")
        except Exception as error:  # noqa: BLE001 - the caller sees only a reason
            return _failure(request_id, type(error).__name__.lower())


def _failure(request_id: str, reason: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "error": reason,
    }


def build_handler(service: ScoringService):
    """Return a handler bound to one service, with no logging of request bodies."""

    class _Handler(BaseHTTPRequestHandler):
        server_version = "CometKiwiQA/1"

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
            if self.path.rstrip("/") != "/health":
                self._send(404, {"error": "not_found"})
                return
            self._send(200, service.health())

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
            if self.path.rstrip("/") != "/score":
                self._send(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "invalid_request"})
                return
            if length > MAX_REQUEST_BYTES:
                self._send(413, {"error": "request_too_large"})
                return
            try:
                payload = json.loads(self.rfile.read(length) or b"")
            except ValueError:
                self._send(400, {"error": "invalid_request"})
                return
            self._send(200, service.handle(payload))

        def _send(self, status: int, body: dict) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, fmt, *args):
            # The default logs the request line.  Nothing of a chapter belongs
            # in a terminal that may be left open all day.
            sys.stderr.write(f"{self.command} {self.path} -> done\n")

    return _Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, help="каталог с весами")
    parser.add_argument("--model", required=True, help="имя модели для журнала")
    parser.add_argument("--device", default="cuda", choices=("cpu", "cuda"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    if not Path(args.model_dir).is_dir():
        sys.stderr.write(f"Каталог весов не найден: {args.model_dir}\n")
        return 2

    service = ScoringService(args.model_dir, args.device, args.model)
    server = ThreadingHTTPServer((args.host, args.port), build_handler(service))
    sys.stderr.write(
        f"COMETKiwi слушает http://{args.host}:{args.port} "
        f"({args.model}, {args.device}). Ctrl+C — остановить.\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/qa/test_cometkiwi_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/translation_qa_cometkiwi_server.py tests/qa/test_cometkiwi_server.py
git commit -m "feat(qa): serve CometKiwi scoring from the machine that owns the GPU

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Settings, readiness, and the wiring

**Files:**
- Modify: `gemini_translator/qa/settings.py:71-74`, `:143-144`, `:214-217`, `:242-251`
- Modify: `gemini_translator/qa/estimators/cometkiwi_model_manager.py:185-200`
- Modify: `gemini_translator/qa/assembly.py:686-693`
- Test: `tests/qa/test_cometkiwi_remote.py` (append)

**Interfaces:**
- Consumes: `CometKiwiRunnerConfig.endpoint` from Task 1.
- Produces: `QaSettings.cometkiwi_endpoint: str` (default `""`); `QaSettings.cometkiwi_is_remote` property; `describe_cometkiwi_setup` treating an endpoint as a substitute for the local runner and weights.

**This is the task that decides whether the feature works at all.** Without it `effective_capabilities()` switches CometKiwi off whenever the local runner path is empty, and a remote setup has no local runner path by design. The user would see no error — just no scores.

- [ ] **Step 1: Write the failing test**

Append to `tests/qa/test_cometkiwi_remote.py`:

```python
from gemini_translator.qa.capabilities import QaCapabilityKey
from gemini_translator.qa.settings import QaCapabilitySettings, QaSettings


def _settings(**overrides) -> QaSettings:
    values = {
        "capabilities": QaCapabilitySettings(cometkiwi_enabled=True),
        "cometkiwi_model": "wmt22-cometkiwi-da",
        "cometkiwi_license_accepted": True,
        "cometkiwi_endpoint": "http://192.168.1.50:8765",
    }
    values.update(overrides)
    return QaSettings(**values)


def test_an_address_stands_in_for_the_local_runner_and_weights():
    """Иначе возможность выключится молча: раннера и весов на этой машине нет."""
    settings = _settings()

    assert settings.cometkiwi_is_remote is True
    assert QaCapabilityKey.COMETKIWI.value not in settings.unsatisfied_requirements()
    assert settings.effective_capabilities().cometkiwi_enabled is True


def test_without_an_address_the_local_runner_is_still_required():
    settings = _settings(cometkiwi_endpoint="")

    assert QaCapabilityKey.COMETKIWI.value in settings.unsatisfied_requirements()
    assert settings.effective_capabilities().cometkiwi_enabled is False


def test_the_licence_is_owed_in_both_modes():
    settings = _settings(cometkiwi_license_accepted=False)

    assert QaCapabilityKey.COMETKIWI.value in settings.unsatisfied_requirements()


def test_the_address_survives_a_save_and_a_load():
    settings = _settings()

    assert QaSettings.from_dict(settings.to_dict()).cometkiwi_endpoint == (
        "http://192.168.1.50:8765"
    )


def test_the_setup_description_asks_for_the_address_not_for_local_weights():
    from gemini_translator.qa.estimators.cometkiwi_model_manager import (
        describe_cometkiwi_setup,
    )

    assert describe_cometkiwi_setup(_settings(), None, None) == ""
    described = describe_cometkiwi_setup(_settings(cometkiwi_model=""), None, None)
    assert "модель" in described
    assert "путь к runner" not in described
    assert "установленные веса" not in described
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/qa/test_cometkiwi_remote.py -k address -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'cometkiwi_endpoint'`

- [ ] **Step 3: Write minimal implementation**

In `gemini_translator/qa/settings.py`, add the field after `cometkiwi_device`:

```python
    # The runner on another machine, as ``http://host:port``.  Empty means the
    # local subprocess, which is what every existing installation uses.
    cometkiwi_endpoint: str = ""
```

Add it to the stripped-string loop at `:143`:

```python
            "cometkiwi_runner_path",
            "cometkiwi_model",
            "cometkiwi_endpoint",
```

Add it to `to_dict()` after `cometkiwi_device`:

```python
            "cometkiwi_endpoint": self.cometkiwi_endpoint,
```

Add the property next to the other helpers on `QaSettings`:

```python
    @property
    def cometkiwi_is_remote(self) -> bool:
        """Report whether scoring is configured to happen on another machine."""
        return bool(self.cometkiwi_endpoint)
```

Replace the CometKiwi branch of `unsatisfied_requirements()`:

```python
        if self.capabilities.cometkiwi_enabled and (
            not (self.cometkiwi_endpoint or self.cometkiwi_runner_path)
            or not self.cometkiwi_model
            or not self.cometkiwi_license_accepted
        ):
            missing.append(QaCapabilityKey.COMETKIWI.value)
```

In `gemini_translator/qa/estimators/cometkiwi_model_manager.py`, replace the body of `describe_cometkiwi_setup` between the capability check and the `if missing:` line:

```python
    missing: list[str] = []
    remote = bool(str(getattr(settings, "cometkiwi_endpoint", "") or "").strip())
    if not remote and not str(getattr(settings, "cometkiwi_runner_path", "") or "").strip():
        missing.append("путь к runner")
    if not str(getattr(settings, "cometkiwi_model", "") or "").strip():
        missing.append("модель")
    if not getattr(settings, "cometkiwi_license_accepted", False):
        missing.append("принятая лицензия")
    if not remote and (status is None or status.state != "ready"):
        missing.append("установленные веса")
```

In `gemini_translator/qa/assembly.py`, replace the config construction:

```python
        remote = bool(qa_settings.cometkiwi_endpoint)
        config = CometKiwiRunnerConfig(
            runner_path="" if remote else qa_settings.cometkiwi_runner_path,
            # The weights are on the other machine; a local path built here
            # would point at a directory that does not exist.
            model_dir=""
            if remote or not qa_settings.cometkiwi_model
            else str(paths.cometkiwi_models / qa_settings.cometkiwi_model),
            model=qa_settings.cometkiwi_model,
            device=qa_settings.cometkiwi_device,
            endpoint=qa_settings.cometkiwi_endpoint,
        )
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/qa/ -q`
Expected: PASS, every test in the directory.

- [ ] **Step 5: Commit**

```bash
git add gemini_translator/qa/settings.py gemini_translator/qa/estimators/cometkiwi_model_manager.py gemini_translator/qa/assembly.py tests/qa/test_cometkiwi_remote.py
git commit -m "feat(qa): accept a network address in place of a local CometKiwi runner

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The address field and a connection check in the dialog

**Files:**
- Modify: `gemini_translator/ui/dialogs/validation_dialogs/translation_quality_dialog.py:400-405` (the CometKiwi group), `:518-521` (inside `qa_settings()`), `:525-` (`_apply_settings_to_widgets`)
- Test: `tests/qa/test_translation_quality_dialog.py` (append)

**Interfaces:**
- Consumes: `QaSettings.cometkiwi_endpoint` from Task 5, `CometKiwiRunnerConfig.health_url()` from Task 1.
- Produces: `self.cometkiwi_endpoint_edit` (a `QLineEdit`) and `self.cometkiwi_check_button` (a `QPushButton`) on the dialog.

**The harness already exists** in `tests/qa/test_translation_quality_dialog.py`: that file sets `QT_QPA_PLATFORM=offscreen` at import, defines a module-scoped `qt_app` fixture, and builds the dialog with `TranslationQualityDialog(**kwargs)`. Use those. The dialog takes `settings=` as a keyword argument and hands its collected settings back from `qa_settings()` — not `collect_settings()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/qa/test_translation_quality_dialog.py`:

```python
def test_the_quality_window_carries_the_cometkiwi_address_both_ways(qt_app):
    """Адрес ПК — единственная настройка CometKiwi, которая реально меняется."""
    dialog = TranslationQualityDialog(
        settings=QaSettings(
            capabilities=QaCapabilitySettings(cometkiwi_enabled=True),
            cometkiwi_model="wmt22-cometkiwi-da",
            cometkiwi_license_accepted=True,
            cometkiwi_endpoint="http://192.168.1.50:8765",
        )
    )

    assert dialog.cometkiwi_endpoint_edit.text() == "http://192.168.1.50:8765"

    dialog.cometkiwi_endpoint_edit.setText("  http://192.168.1.77:9000  ")

    assert dialog.qa_settings().cometkiwi_endpoint == "http://192.168.1.77:9000"


def test_an_empty_address_says_the_scoring_stays_on_this_machine(qt_app):
    dialog = TranslationQualityDialog(settings=QaSettings())

    dialog.cometkiwi_endpoint_edit.setText("")
    dialog.cometkiwi_check_button.click()

    assert "на этом компьютере" in dialog.cometkiwi_status_label.text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/qa/test_translation_quality_dialog.py -k cometkiwi_address -v`
Expected: FAIL with `AttributeError: 'TranslationQualityDialog' object has no attribute 'cometkiwi_endpoint_edit'`

- [ ] **Step 3: Write minimal implementation**

Add the widgets in the CometKiwi group, just above `self.cometkiwi_status_label`:

```python
        self.cometkiwi_endpoint_edit = QLineEdit(group)
        self.cometkiwi_endpoint_edit.setPlaceholderText(
            "http://192.168.1.50:8765 — пусто: считать на этом компьютере"
        )
        layout.addWidget(QLabel("Адрес счётного сервера:", group))
        layout.addWidget(self.cometkiwi_endpoint_edit)
        self.cometkiwi_check_button = QPushButton("Проверить связь", group)
        self.cometkiwi_check_button.clicked.connect(self._check_cometkiwi_endpoint)
        layout.addWidget(self.cometkiwi_check_button)
```

Add `QLineEdit` and `QPushButton` to the Qt imports at the top of the file if they are not already there.

In `qa_settings()` (the method that collects the widgets, around `:518`), add the endpoint next to the other CometKiwi lines:

```python
            cometkiwi_endpoint=self.cometkiwi_endpoint_edit.text().strip(),
```

In `_apply_settings_to_widgets`, next to the other CometKiwi lines:

```python
        self.cometkiwi_endpoint_edit.setText(settings.cometkiwi_endpoint)
```

Add the check, which must never raise into the dialog:

```python
    def _check_cometkiwi_endpoint(self) -> None:
        """Ask the scoring server what it is, without loading anything there."""
        endpoint = self.cometkiwi_endpoint_edit.text().strip()
        if not endpoint:
            self.cometkiwi_status_label.setText(
                "Адрес пуст: оценка будет считаться на этом компьютере."
            )
            return
        import json
        import urllib.error
        import urllib.request

        url = endpoint.rstrip("/") + "/health"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                health = json.loads(response.read(100_000))
            loaded = "веса в памяти" if health.get("loaded") else "веса ещё не загружены"
            self.cometkiwi_status_label.setText(
                f"Связь есть: {health.get('model', '?')} на "
                f"{health.get('device', '?')}, {loaded}."
            )
        except urllib.error.URLError:
            self.cometkiwi_status_label.setText(
                "Сервер не отвечает. Проверьте, запущен ли он на ПК, "
                "и открыт ли порт в брандмауэре."
            )
        except Exception:  # noqa: BLE001 - a failed check never breaks the dialog
            self.cometkiwi_status_label.setText("Ответ сервера не разобран.")
```

**Why `urllib` and not `aiohttp` here:** the dialog runs on the Qt thread with no event loop of its own, and a five-second blocking check on a button press is simpler and more honest than starting a loop for one request.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/qa/test_translation_quality_dialog.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gemini_translator/ui/dialogs/validation_dialogs/translation_quality_dialog.py tests/qa/test_translation_quality_dialog.py
git commit -m "feat(ui): type the CometKiwi server address and check it answers

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Setting it up on the PC

**Files:**
- Create: `tools/start_cometkiwi_server.bat`
- Modify: `docs/translation-quality-qa.md`

**Interfaces:**
- Consumes: the command line of `tools/translation_qa_cometkiwi_server.py` from Task 4.
- Produces: no code interface; this task is the instructions a person follows once.

- [ ] **Step 1: Write the batch file**

Create `tools/start_cometkiwi_server.bat`:

```bat
@echo off
rem Запуск счётного сервера COMETKiwi на ПК с видеокартой.
rem Отредактируйте три строки ниже под свою машину и положите ярлык
rem на этот файл в автозагрузку, если хотите, чтобы он поднимался сам.

set KIWI_PYTHON=C:\kiwi\venv\Scripts\python.exe
set KIWI_WEIGHTS=C:\kiwi\weights\wmt22-cometkiwi-da
set KIWI_MODEL=wmt22-cometkiwi-da

"%KIWI_PYTHON%" "%~dp0translation_qa_cometkiwi_server.py" ^
  --model-dir "%KIWI_WEIGHTS%" ^
  --model "%KIWI_MODEL%" ^
  --device cuda ^
  --port 8765

pause
```

- [ ] **Step 2: Write the documentation**

Append a section to `docs/translation-quality-qa.md`. Match the heading level and tone of the sections already there.

```markdown
## Счёт качества на другом компьютере

COMETKiwi можно считать не на той машине, где идёт перевод, а на домашнем ПК с
видеокартой. Перевод при этом ничего не ждёт: если ПК выключен, глава просто
проверяется без оценки качества, а в журнале появляется причина.

Порядок настройки — один раз:

1. На ПК создайте окружение с PyTorch под CUDA и `unbabel-comet`, положите туда
   веса COMETKiwi.
2. Отредактируйте три переменные в `tools/start_cometkiwi_server.bat` и
   запустите его. В окне появится строка вида
   `COMETKiwi слушает http://0.0.0.0:8765`.
3. Разрешите входящие соединения на порт 8765 в брандмауэре Windows для
   **частной** сети.
4. На роутере закрепите за ПК постоянный адрес (резервирование DHCP). Без этого
   адрес сменится после перезагрузки, и оценки тихо пропадут.
5. В окне «Качество перевода» впишите адрес вида `http://192.168.1.50:8765` и
   нажмите «Проверить связь». Должно ответить именем модели и устройством.

Пустое поле адреса возвращает прежнее поведение: счёт идёт на этой машине через
локальный `translation_qa_cometkiwi_runner.py`.

Сервер рассчитан только на доверенную домашнюю сеть: пароля у него нет.
Не пробрасывайте его порт наружу через роутер. Если доступ из другого места
когда-нибудь понадобится, поднимайте VPN до домашней сети.
```

- [ ] **Step 3: Verify the server starts and answers**

This step needs the PC. On the Mac, verify only that the command line parses and refuses a missing directory:

Run: `.venv/bin/python tools/translation_qa_cometkiwi_server.py --model-dir /nope --model m`
Expected: prints `Каталог весов не найден: /nope` and exits with code 2.

- [ ] **Step 4: Commit**

```bash
git add tools/start_cometkiwi_server.bat docs/translation-quality-qa.md
git commit -m "docs(qa): explain how to run CometKiwi on the PC with the GPU

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: The whole suite

**Files:**
- No production file changes expected. Fix whatever this task uncovers.

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`

Expected: no new failures. Three failures in `tests/test_dedup_pcluster_22_safe_call.py` pre-date this work and come from unrelated uncommitted changes in `gemini_translator/core/chapter_qa_coordinator.py`; confirm they are the same three and nothing else.

- [ ] **Step 2: Confirm the local path is untouched**

Run: `git diff --stat HEAD~7 -- tests/qa/test_cometkiwi_estimator.py tests/qa/test_cometkiwi_runner_protocol.py`
Expected: `test_cometkiwi_estimator.py` unchanged; `test_cometkiwi_runner_protocol.py` has only the two appended tests from Task 3 and no edits to existing ones.

- [ ] **Step 3: Commit any fixes**

```bash
git add -- <named files only>
git commit -m "fix(qa): <what the suite caught>

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

**Note:** this repository is shared with other sessions. Always stage files by name; never `git add -A`.
