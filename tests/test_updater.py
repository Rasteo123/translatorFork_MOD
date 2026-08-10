import os
import pytest


# --- Version parsing and ordering (Task 1) ---

from gemini_translator.utils.updater import UpdateError, parse_version_tag


@pytest.mark.parametrize("tag,expected", [
    ("v10.5.21", ((10, 5, 21), 1, 0)),
    ("10.5.21", ((10, 5, 21), 1, 0)),
    ("V 10.5.21", ((10, 5, 21), 1, 0)),
    ("v10.5.21-hotfix24", ((10, 5, 21), 2, 24)),
    ("v10.6.0-rc1", ((10, 6, 0), 0, 1)),
    ("v10.6.0-beta.2", ((10, 6, 0), 0, 2)),
    ("v10.6.0-alpha3", ((10, 6, 0), 0, 3)),
])
def test_parse_version_tag(tag, expected):
    v = parse_version_tag(tag)
    assert (v.release, v.phase, v.phase_num) == expected


@pytest.mark.parametrize("bad", ["", "v", "10.5", "10.5.21.7", "abc", "v10.5.x",
                                 "10.5.21-hotfix", "10.5.21-omega1", None])
def test_parse_version_tag_rejects_malformed(bad):
    with pytest.raises(UpdateError):
        parse_version_tag(bad)


def test_version_ordering():
    final = parse_version_tag("v10.5.21")
    assert parse_version_tag("v10.5.21-rc1") < final
    assert final < parse_version_tag("v10.5.21-hotfix1")
    assert parse_version_tag("v10.5.21-hotfix1") < parse_version_tag("v10.5.21-hotfix2")
    assert parse_version_tag("v10.5.21-hotfix24") < parse_version_tag("v10.5.22")
    assert final == parse_version_tag("10.5.21")
    assert final.is_final and not parse_version_tag("v10.6.0-rc1").is_final


# --- Build identity + channel detection (Task 2) ---


def _identity():
    from gemini_translator.utils import updater as u
    return u.BuildIdentity(u.parse_version_tag("v10.5.21"), "v10.5.21", "a" * 40)


@pytest.mark.parametrize("exe,identity,expected", [
    (r"C:\Apps\GT\translatorFork_MOD.exe", True, "WINDOWS_INSTALLED"),
    (r"C:\Users\u\Downloads\GeminiTranslator-Portable.exe", True, "WINDOWS_PORTABLE"),
    ("/Applications/GeminiTranslator.app/Contents/MacOS/GeminiTranslator", True, "MACOS"),
    (r"C:\Apps\GT\renamed.exe", True, "DEVELOPMENT"),
    (r"C:\Apps\GT\translatorFork_MOD.exe", False, "DEVELOPMENT"),
])
def test_detect_channel_frozen(monkeypatch, exe, identity, expected):
    from gemini_translator.utils import updater as u
    monkeypatch.setattr(u.sys, "frozen", True, raising=False)
    monkeypatch.setattr(u.sys, "executable", exe)
    monkeypatch.setattr(u, "read_build_identity", lambda: _identity() if identity else None)
    assert u.detect_update_channel() is u.UpdateChannel[expected]


def test_detect_channel_source(monkeypatch, tmp_path):
    from gemini_translator.utils import updater as u
    monkeypatch.delattr(u.sys, "frozen", raising=False)
    monkeypatch.setattr(u, "project_root", lambda: tmp_path)
    assert u.detect_update_channel() is u.UpdateChannel.SOURCE_ARCHIVE
    (tmp_path / ".git").mkdir()
    assert u.detect_update_channel() is u.UpdateChannel.SOURCE_GIT


def test_read_build_identity_validates(monkeypatch, tmp_path):
    from gemini_translator.utils import updater as u
    p = tmp_path / u.BUILD_IDENTITY_FILENAME
    monkeypatch.setattr(u.api_config, "get_resource_path", lambda name: p)
    assert u.read_build_identity() is None  # файла нет
    p.write_text('{"schema":1,"version":"10.5.21","tag":"v10.5.21","commit":"%s"}' % ("a" * 40))
    ident = u.read_build_identity()
    assert ident.tag == "v10.5.21" and ident.version.is_final
    p.write_text('{"schema":1,"version":"10.5.21","tag":"v10.9.9","commit":"%s"}' % ("a" * 40))
    assert u.read_build_identity() is None  # тег и версия расходятся
    p.write_text('{"schema":1,"version":"10.6.0-rc1","tag":"v10.6.0-rc1","commit":"%s"}' % ("a" * 40))
    assert u.read_build_identity() is None  # не финальная версия
    p.write_text("not json")
    assert u.read_build_identity() is None


def test_read_archive_identity(tmp_path):
    from gemini_translator.utils import updater as u
    assert u.read_archive_identity(tmp_path) is None
    (tmp_path / u.ARCHIVE_IDENTITY_FILENAME).write_text(
        '{"schema":1,"commit":"%s","files":["main.py"]}' % ("b" * 40))
    ident = u.read_archive_identity(tmp_path)
    assert ident["commit"] == "b" * 40
    (tmp_path / u.ARCHIVE_IDENTITY_FILENAME).write_text('{"schema":1,"commit":"short"}')
    assert u.read_archive_identity(tmp_path) is None


# --- Манифест релиза и выбор ассета по каналу (Task 3) ---


def _manifest_data(**over):
    data = {"schema": 1, "version": "10.5.22", "tag": "v10.5.22", "commit": "c" * 40,
            "assets": [
                {"name": "GeminiTranslator-Setup.exe", "platform": "windows",
                 "channel": "windows-installed", "size": 10, "sha256": "a" * 64},
                {"name": "GeminiTranslator-Portable.exe", "platform": "windows",
                 "channel": "windows-portable", "size": 20, "sha256": "b" * 64},
                {"name": "GeminiTranslator-macOS.zip", "platform": "macos",
                 "channel": "macos", "size": 30, "sha256": "c" * 64}]}
    data.update(over)
    return data


def _gh_assets(names):
    return [{"name": n, "browser_download_url": f"https://dl/{n}"} for n in names]


_ALL = ["GeminiTranslator-Setup.exe", "GeminiTranslator-Portable.exe", "GeminiTranslator-macOS.zip"]


def test_manifest_parses_and_selects():
    from gemini_translator.utils import updater as u
    m = u.parse_update_manifest(_manifest_data(), "v10.5.22", _gh_assets(_ALL))
    a = u.select_release_asset(m, u.UpdateChannel.WINDOWS_INSTALLED)
    assert a.name == "GeminiTranslator-Setup.exe" and a.url.endswith("Setup.exe")
    assert u.select_release_asset(m, u.UpdateChannel.WINDOWS_PORTABLE).size == 20
    assert u.select_release_asset(m, u.UpdateChannel.MACOS).name.endswith(".zip")


def test_manifest_macos_prefers_dmg():
    from gemini_translator.utils import updater as u
    data = _manifest_data()
    data["assets"].append({"name": "GeminiTranslator-macOS.dmg", "platform": "macos",
                           "channel": "macos", "size": 40, "sha256": "d" * 64})
    m = u.parse_update_manifest(data, "v10.5.22", _gh_assets(_ALL + ["GeminiTranslator-macOS.dmg"]))
    assert u.select_release_asset(m, u.UpdateChannel.MACOS).name.endswith(".dmg")


def test_manifest_missing_channel_is_manual_not_fallback():
    from gemini_translator.utils import updater as u
    data = _manifest_data()
    data["assets"] = [data["assets"][0]]
    m = u.parse_update_manifest(data, "v10.5.22", _gh_assets(_ALL))
    assert u.select_release_asset(m, u.UpdateChannel.WINDOWS_PORTABLE) is None
    assert u.select_release_asset(m, u.UpdateChannel.SOURCE_GIT) is None  # source не берёт бинарники
    assert u.select_release_asset(m, u.UpdateChannel.DEVELOPMENT) is None


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema=2),
    lambda d: d.update(tag="v9.9.9"),
    lambda d: d.update(version="oops"),
    lambda d: d.update(commit="zz"),
    lambda d: d["assets"][0].update(sha256="short"),
    lambda d: d["assets"][0].update(size=0),
    lambda d: d["assets"][0].update(name="NotUploaded.exe"),
])
def test_manifest_rejects_invalid(mutate):
    from gemini_translator.utils import updater as u
    data = _manifest_data()
    mutate(data)
    with pytest.raises(u.UpdateError):
        u.parse_update_manifest(data, "v10.5.22", _gh_assets(_ALL))


def test_manifest_duplicate_channel_is_ambiguous_manual():
    from gemini_translator.utils import updater as u
    data = _manifest_data()
    data["assets"].append(dict(data["assets"][0], name="GeminiTranslator-Setup2.exe"))
    m = u.parse_update_manifest(data, "v10.5.22",
                                _gh_assets(_ALL + ["GeminiTranslator-Setup2.exe"]))
    assert u.select_release_asset(m, u.UpdateChannel.WINDOWS_INSTALLED) is None


# --- UpdateChecker (Task 4) ---


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, routes):
        self.routes = routes  # list[(url_substr, FakeResponse | Exception)]
        self.calls = []

    def get(self, url, timeout=None, stream=False, **kw):
        self.calls.append(url)
        for substr, resp in self.routes:
            if substr in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected URL {url}")


def _release_payload(tag="v10.5.22", with_manifest=True, assets_names=None):
    names = list(_ALL) if assets_names is None else list(assets_names)
    if with_manifest:
        names = names + ["update-manifest.json"]
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/x/releases/tag/{tag}",
        "body": "Notes",
        "assets": [{"name": n, "browser_download_url": f"https://dl/{n}"} for n in names],
    }


def _make_release_checker(monkeypatch, routes, channel="WINDOWS_INSTALLED", identity=True):
    from gemini_translator.utils import updater as u
    monkeypatch.setattr(u, "detect_update_channel", lambda: u.UpdateChannel[channel])
    monkeypatch.setattr(u, "read_build_identity", lambda: _identity() if identity else None)
    session = FakeSession(routes)
    checker = u.UpdateChecker(manual=True, session_factory=lambda: session)
    return u, checker


def test_checker_release_update_available(qtbot, monkeypatch):
    u, checker = _make_release_checker(monkeypatch, [
        ("releases/latest", FakeResponse(json_data=_release_payload())),
        ("update-manifest.json", FakeResponse(json_data=_manifest_data())),
    ])
    with qtbot.waitSignal(checker.update_available, timeout=2000) as blocker:
        checker.run()
    info = blocker.args[0]
    assert isinstance(info, u.UpdateInfo)
    assert info.kind == "release" and not info.manual
    assert info.asset.name == "GeminiTranslator-Setup.exe"
    assert info.asset.sha256 == "a" * 64
    assert info.suppress_id == "v10.5.22"


def test_checker_release_no_update_on_equal(qtbot, monkeypatch):
    manifest = _manifest_data(version="10.5.21", tag="v10.5.21")
    u, checker = _make_release_checker(monkeypatch, [
        ("releases/latest", FakeResponse(json_data=_release_payload(tag="v10.5.21"))),
        ("update-manifest.json", FakeResponse(json_data=manifest)),
    ])
    with qtbot.waitSignal(checker.no_update, timeout=2000):
        checker.run()


def test_checker_release_missing_channel_is_manual(qtbot, monkeypatch):
    data = _manifest_data()
    data["assets"] = [a for a in data["assets"] if a["channel"] != "windows-installed"]
    u, checker = _make_release_checker(monkeypatch, [
        ("releases/latest", FakeResponse(json_data=_release_payload())),
        ("update-manifest.json", FakeResponse(json_data=data)),
    ])
    with qtbot.waitSignal(checker.update_available, timeout=2000) as blocker:
        checker.run()
    info = blocker.args[0]
    assert info.manual and info.manual_url.startswith("https://github.com/")
    assert info.asset is None


def test_checker_release_invalid_manifest_errors(qtbot, monkeypatch):
    data = _manifest_data()
    data["assets"][0]["name"] = "NotUploaded.exe"
    u, checker = _make_release_checker(monkeypatch, [
        ("releases/latest", FakeResponse(json_data=_release_payload())),
        ("update-manifest.json", FakeResponse(json_data=data)),
    ])
    with qtbot.waitSignal(checker.error_occurred, timeout=2000):
        checker.run()


def test_checker_release_legacy_without_manifest(qtbot, monkeypatch):
    u, checker = _make_release_checker(monkeypatch, [
        ("releases/latest", FakeResponse(json_data=_release_payload(with_manifest=False))),
    ])
    with qtbot.waitSignal(checker.update_available, timeout=2000) as blocker:
        checker.run()
    info = blocker.args[0]
    assert info.manual and info.asset is None

    u2, checker2 = _make_release_checker(monkeypatch, [
        ("releases/latest",
         FakeResponse(json_data=_release_payload(tag="v10.5.21", with_manifest=False))),
    ])
    with qtbot.waitSignal(checker2.no_update, timeout=2000):
        checker2.run()


@pytest.mark.parametrize("routes", [
    [("releases/latest", FakeResponse(status_code=500))],
    [("releases/latest", FakeResponse(json_data=_release_payload(tag="vNext")))],
])
def test_checker_release_failures_are_errors(qtbot, monkeypatch, routes):
    u, checker = _make_release_checker(monkeypatch, routes)
    with qtbot.waitSignal(checker.error_occurred, timeout=2000):
        checker.run()


def test_checker_release_network_exception_is_error(qtbot, monkeypatch):
    import requests as _requests
    u, checker = _make_release_checker(
        monkeypatch, [("releases/latest", _requests.ConnectionError("boom"))])
    with qtbot.waitSignal(checker.error_occurred, timeout=2000):
        checker.run()


def test_checker_development_manual_announce(qtbot, monkeypatch):
    u, checker = _make_release_checker(
        monkeypatch,
        [("releases/latest", FakeResponse(json_data=_release_payload(tag="v99.0.0",
                                                                     with_manifest=False)))],
        channel="DEVELOPMENT", identity=False)
    with qtbot.waitSignal(checker.update_available, timeout=2000) as blocker:
        checker.run()
    assert blocker.args[0].manual


def _git_checker(monkeypatch, responses):
    """responses: list[(substr_of_cmd, (rc, stdout, stderr))], первый матч."""
    import subprocess as sp
    from gemini_translator.utils import updater as u
    monkeypatch.setattr(u, "detect_update_channel", lambda: u.UpdateChannel.SOURCE_GIT)
    calls = []

    def fake_run(cmd, **kw):
        calls.append((list(cmd), kw))
        joined = " ".join(cmd)
        for substr, (rc, out, err) in responses:
            if substr in joined:
                return sp.CompletedProcess(cmd, rc, out, err)
        return sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(u.subprocess, "run", fake_run)
    return u, u.UpdateChecker(manual=True, session_factory=lambda: FakeSession([])), calls


def test_checker_git_missing_upstream_is_error(qtbot, monkeypatch):
    u, checker, _ = _git_checker(monkeypatch, [("--abbrev-ref", (1, "", "no upstream"))])
    with qtbot.waitSignal(checker.error_occurred, timeout=2000) as blocker:
        checker.run()
    assert "upstream" in blocker.args[0]


def test_checker_git_fetch_failure_is_error(qtbot, monkeypatch):
    u, checker, _ = _git_checker(monkeypatch, [
        ("--abbrev-ref", (0, "origin/main\n", "")),
        ("fetch", (1, "", "fatal: could not read")),
    ])
    with qtbot.waitSignal(checker.error_occurred, timeout=2000):
        checker.run()


def test_checker_git_update_available(qtbot, monkeypatch):
    sha = "f" * 40
    u, checker, calls = _git_checker(monkeypatch, [
        ("--abbrev-ref", (0, "origin/main\n", "")),
        ("fetch", (0, "", "")),
        ("rev-list", (0, "3\n", "")),
        ("rev-parse", (0, sha + "\n", "")),
    ])
    with qtbot.waitSignal(checker.update_available, timeout=2000) as blocker:
        checker.run()
    info = blocker.args[0]
    assert info.kind == "git" and info.suppress_id == sha and info.commit == sha
    for cmd, kw in calls:
        assert kw.get("env", {}).get("GIT_TERMINAL_PROMPT") == "0"
        assert kw.get("timeout")


def test_checker_git_no_update(qtbot, monkeypatch):
    u, checker, _ = _git_checker(monkeypatch, [
        ("--abbrev-ref", (0, "origin/main\n", "")),
        ("fetch", (0, "", "")),
        ("rev-list", (0, "0\n", "")),
        ("rev-parse", (0, "f" * 40 + "\n", "")),
    ])
    with qtbot.waitSignal(checker.no_update, timeout=2000):
        checker.run()


def _archive_checker(monkeypatch, identity, routes):
    from gemini_translator.utils import updater as u
    monkeypatch.setattr(u, "detect_update_channel", lambda: u.UpdateChannel.SOURCE_ARCHIVE)
    monkeypatch.setattr(u, "read_archive_identity", lambda root: identity)
    session = FakeSession(routes)
    return u, u.UpdateChecker(manual=True, session_factory=lambda: session)


def test_checker_archive_unknown_identity_is_manual(qtbot, monkeypatch):
    u, checker = _archive_checker(monkeypatch, None, [])
    with qtbot.waitSignal(checker.update_available, timeout=2000) as blocker:
        checker.run()
    info = blocker.args[0]
    assert info.kind == "archive" and info.manual


def test_checker_archive_update_available_pins_sha(qtbot, monkeypatch):
    sha = "e" * 40
    u, checker = _archive_checker(
        monkeypatch, {"schema": 1, "commit": "d" * 40},
        [("commits/main", FakeResponse(json_data={"sha": sha, "commit": {"message": "msg"}}))])
    with qtbot.waitSignal(checker.update_available, timeout=2000) as blocker:
        checker.run()
    info = blocker.args[0]
    assert info.kind == "archive" and not info.manual
    assert info.suppress_id == sha and info.zip_url.endswith(f"/zipball/{sha}")


def test_checker_archive_no_update(qtbot, monkeypatch):
    sha = "e" * 40
    u, checker = _archive_checker(
        monkeypatch, {"schema": 1, "commit": sha},
        [("commits/main", FakeResponse(json_data={"sha": sha}))])
    with qtbot.waitSignal(checker.no_update, timeout=2000):
        checker.run()


def test_checker_archive_api_error(qtbot, monkeypatch):
    u, checker = _archive_checker(
        monkeypatch, {"schema": 1, "commit": "d" * 40},
        [("commits/main", FakeResponse(status_code=500))])
    with qtbot.waitSignal(checker.error_occurred, timeout=2000):
        checker.run()


# --- UpdateDownloader (Task 5) ---


class FakeStreamResponse:
    def __init__(self, chunks, status_code=200, content_length=None):
        self.chunks = chunks
        self.status_code = status_code
        self.headers = {}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)
        self.closed = False

    def iter_content(self, chunk_size=1):
        for chunk in self.chunks:
            yield chunk

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _download(qtbot, tmp_path, response, *, expect, cancel_after=None, **kwargs):
    from gemini_translator.utils import updater as u

    class StreamSession:
        def get(self, url, timeout=None, stream=False, **kw):
            assert stream is True
            return response

    dl = u.UpdateDownloader("https://dl/x.bin", tmp_path,
                            session_factory=lambda: StreamSession(), **kwargs)
    if cancel_after is not None:
        seen = []
        orig_iter = response.iter_content

        def counting_iter(chunk_size=1):
            for chunk in orig_iter(chunk_size):
                seen.append(chunk)
                if len(seen) == cancel_after:
                    dl.cancel()
                yield chunk

        response.iter_content = counting_iter
    with qtbot.waitSignal(getattr(dl, expect), timeout=3000) as blocker:
        dl.run()
    return dl, blocker


def test_downloader_verifies_exe(qtbot, tmp_path):
    import hashlib
    payload = b"MZ" + b"x" * 100
    sha = hashlib.sha256(payload).hexdigest()
    resp = FakeStreamResponse([payload[:50], payload[50:]], content_length=len(payload))
    dl, blocker = _download(qtbot, tmp_path, resp, expect="verified",
                            expected_size=len(payload), expected_sha256=sha, shape="pe")
    final = blocker.args[0]
    assert os.path.exists(final) and not final.endswith(".part")
    with open(final, "rb") as f:
        assert f.read() == payload
    assert not list(tmp_path.glob("*.part"))


def test_downloader_verifies_zip(qtbot, tmp_path):
    import hashlib
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("main.py", "print('hi')")
    payload = buf.getvalue()
    sha = hashlib.sha256(payload).hexdigest()
    resp = FakeStreamResponse([payload], content_length=len(payload))
    dl, blocker = _download(qtbot, tmp_path, resp, expect="verified",
                            expected_size=len(payload), expected_sha256=sha, shape="zip")
    assert os.path.exists(blocker.args[0])


@pytest.mark.parametrize("kwargs,chunks,content_length", [
    # неверный хеш
    (dict(expected_sha256="0" * 64, shape="pe"), [b"MZ123"], 5),
    # тело короче заявленного размера
    (dict(expected_size=999, shape="pe"), [b"MZ123"], 999),
    # не PE
    (dict(shape="pe"), [b"PK123"], 5),
    # битый zip
    (dict(shape="zip"), [b"PK\x03\x04garbage"], 14),
])
def test_downloader_failures_leave_nothing(qtbot, tmp_path, kwargs, chunks, content_length):
    resp = FakeStreamResponse(chunks, content_length=content_length)
    dl, _ = _download(qtbot, tmp_path, resp, expect="failed", **kwargs)
    assert list(tmp_path.iterdir()) == []


def test_downloader_http_error(qtbot, tmp_path):
    resp = FakeStreamResponse([], status_code=500)
    _download(qtbot, tmp_path, resp, expect="failed")
    assert list(tmp_path.iterdir()) == []


def test_downloader_cancel_removes_part(qtbot, tmp_path):
    resp = FakeStreamResponse([b"MZ", b"aa", b"bb", b"cc"], content_length=8)
    dl, _ = _download(qtbot, tmp_path, resp, expect="cancelled", cancel_after=1,
                      expected_size=8, shape="pe")
    assert list(tmp_path.iterdir()) == []
    assert resp.closed


def test_build_updater_session_socks_credentials():
    from gemini_translator.utils import updater as u

    class SM:
        def load_proxy_settings(self):
            return {"enabled": True, "type": "SOCKS5", "host": "h", "port": 1080,
                    "user": "u@x", "password": "p:w"}

    s = u.build_updater_session(SM())
    assert s.proxies["https"] == "socks5h://u%40x:p%3Aw@h:1080"
    assert s.proxies["http"] == s.proxies["https"]
    assert s.trust_env is False


def test_build_updater_session_disabled_or_none():
    from gemini_translator.utils import updater as u
    assert u.build_updater_session(None).proxies == {}

    class SM:
        def load_proxy_settings(self):
            return {"enabled": False, "type": "SOCKS5", "host": "h", "port": 1}

    assert u.build_updater_session(SM()).proxies == {}


