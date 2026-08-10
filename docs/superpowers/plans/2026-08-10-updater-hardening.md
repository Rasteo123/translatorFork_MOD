# Updater Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the hardened update system from `docs/superpowers/specs/2026-08-10-updater-hardening-design.md`, amended with the 2026-08-10 review findings (PID-wait, health-window semantics for a live process, snapshot limitations, macOS asset rule, `installed_commit` migration).

**Architecture:** Discovery/verification domain lives in `gemini_translator/utils/updater.py` (Qt worker threads, no UI). Installation/rollback lives in a new Qt-free `gemini_translator/utils/update_installer.py` that renders and launches detached platform helpers (PowerShell on Windows, bash on macOS, Python for source archives) implementing a journal + one-time health-token protocol. `HomePage` becomes a pure coordinator. Release identity is proven end-to-end by three new `tools/` scripts wired into `.github/workflows/release.yml`.

**Tech Stack:** Python 3.10, PyQt6 (workers/UI only), requests (+PySocks already shipped), PowerShell 5.1 / bash / python3 helpers, GitHub REST API, Inno Setup preprocessor, pytest + pytest-qt.

## Global Constraints

- `gemini_translator/version.py` `__version__` stays exactly `"10.5.21"`; `APP_VERSION` format unchanged.
- No release is published by this work; no tags are created.
- No external updater frameworks (no Sparkle/WinSparkle/Squirrel).
- Fail-closed: no failure path may emit `no_update`; the updater never selects an undeclared or first-arbitrary asset; unknown identity ⇒ `DEVELOPMENT`/manual-only.
- macOS: hash + bundle-structure + `codesign --verify --deep --strict` all pass **before** `xattr -cr`; quarantine-removal failure ⇒ rollback.
- Helpers wait for the launching **PID** to exit (bounded 120 s); no fixed-sleep-only gates. If the old process is still alive at the deadline the helper aborts having changed nothing.
- Health window (90 s): rollback **only** when the new process has exited without writing the ack file. Alive-at-deadline ⇒ keep everything (backups + staged), log `HEALTH-TIMEOUT`, never kill, never restore over a locked file, never acknowledge the version.
- Windows Installed rollback restores files only (documented limitation: Inno registry/uninstaller entries may reference the new version); a free-disk-space check (≥ app dir size) runs before the snapshot; snapshot failure aborts before the installer runs.
- Migration deletes **both** `updater/installed_version` and `updater/installed_commit` from QSettings; no code path may write either key again.
- Manifest requires Setup + Portable and **at least one** macOS asset (DMG preferred, ZIP acceptable).
- Tests never execute against the developer's real executable/`.app`; all replacement tests run in `tmp_path`. Qt tests follow the existing offscreen conventions of `tests/`.
- Working branch: `feature/updater-hardening`; commit at the end of every task.

## File Map

| File | Action | Responsibility |
|---|---|---|
| `docs/superpowers/specs/2026-08-10-updater-hardening-design.md` | modify | Amendments section (review decisions) |
| `gemini_translator/utils/updater.py` | rewrite | versions, channels, build identity, manifest, `UpdateChecker`, `UpdateDownloader`, proxy session, `FunctionWorker` |
| `gemini_translator/utils/update_installer.py` | create | staging/ack/journal protocol, per-channel installers, helper templates, stale cleanup |
| `gemini_translator/ui/pages/home_page.py` | modify | coordinator only: state machine, prompts, progress, cancellation |
| `main.py` | modify | startup ack write + stale staging cleanup |
| `translatorFork_MOD.spec` | modify | bundle `update-build.json` when present |
| `tools/generate_build_identity.py` | create | write `update-build.json`, enforce tag==v{version} |
| `tools/verify_release_tag.py` | create | tag/version/notes/commit agreement gate |
| `tools/generate_update_manifest.py` | create | hash/size/channel manifest from real artifacts |
| `.github/workflows/release.yml` | rewrite | verify job → builds → manifest → release with body_path |
| `windows_installer.iss` | modify | `AppVersion` via preprocessor define |
| `build_release_dual.bat` | modify | inject `.translator-update.json` + manual markers into ZIPs |
| `gemini_translator/scripts/updater_script.py` | delete | dead code |
| `tests/test_updater.py` | rewrite | domain + discovery + downloader |
| `tests/test_update_installer.py` | create | installers, helper scripts, protocol |
| `tests/test_release_tools.py` | create | the three `tools/` scripts |
| `tests/test_updater_home_page.py` | create | HomePage coordinator behavior |

Verification command used throughout: `python -m pytest tests/<file> -x -q` from repo root (same interpreter the suite already uses). Full gate at the end: `python -m pytest tests/ -q` and `python tools/run_checks.py`.

---

### Task 0: Branch + spec amendments

**Files:**
- Modify: `docs/superpowers/specs/2026-08-10-updater-hardening-design.md`

- [ ] **Step 0.1:** `git checkout -b feature/updater-hardening`
- [ ] **Step 0.2:** Append an `## Amendments (2026-08-10 review)` section to the spec recording the decisions that bind this plan:
  1. Helpers wait on the launching PID (bounded 120 s) instead of fixed sleeps; still-running app at deadline ⇒ abort with no changes. `/FORCECLOSEAPPLICATIONS` is dropped from the Setup invocation; `/NORESTART` added.
  2. Health-window semantics: rollback only on "new process exited without ack"; alive-at-deadline ⇒ keep backups, log `HEALTH-TIMEOUT`, exit without restore (a live Windows process locks its own exe; a live process may still become healthy).
  3. Windows Installed rollback restores the file snapshot only; Inno registry/uninstall entries are a documented limitation. Free-space check precedes the snapshot; snapshot failure aborts before Setup runs.
  4. The release manifest requires Setup, Portable, and at least one macOS asset (DMG preferred, ZIP acceptable) — matching the existing CI DMG flakiness.
  5. Migration removes `updater/installed_commit` in addition to `updater/installed_version`.
  6. Existing source-ZIP installations (no `.translator-update.json`) become manual-only; `build_release_dual.bat` injects the identity file into future source ZIPs (post-`git archive` step).
  7. Channel detection keys off the packaged executable identity; a renamed executable degrades to `DEVELOPMENT` (manual-only) by design.
  8. Windows helpers are PowerShell (`-NoProfile -ExecutionPolicy Bypass -File`), not batch: PID waits, `-PassThru` PIDs and guarded rollback need real control flow.
- [ ] **Step 0.3:** Commit: `git commit -m "docs: amend updater hardening spec with review decisions"`

---

### Task 1: Version parsing and ordering

**Files:**
- Modify: `gemini_translator/utils/updater.py` (add section; old code untouched until Task 4)
- Test: `tests/test_updater.py` (new section at top; old tests untouched until Task 4)

**Interfaces (Produces):**
```python
class UpdateError(Exception): ...            # user_message attribute mirrors str(self)
@dataclass(frozen=True, order=True)
class ReleaseVersion:
    release: tuple[int, int, int]
    phase: int        # 0 = prerelease, 1 = final, 2 = post (legacy hotfix)
    phase_num: int
    @property
    def is_final(self) -> bool  # phase == 1
def parse_version_tag(text: str) -> ReleaseVersion   # raises UpdateError
```

- [ ] **Step 1.1: Failing tests** — add to `tests/test_updater.py`:

```python
import pytest
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
```

- [ ] **Step 1.2:** Run `python -m pytest tests/test_updater.py -q -k version` — expect ImportError/FAIL.
- [ ] **Step 1.3: Implementation** in `updater.py`:

```python
_VERSION_RE = re.compile(
    r"^[vV]?\s*(\d+)\.(\d+)\.(\d+)"
    r"(?:-(?P<kind>hotfix|rc|beta|alpha)\.?(?P<num>\d+))?$"
)

class UpdateError(Exception):
    @property
    def user_message(self) -> str:
        return str(self)

@dataclass(frozen=True, order=True)
class ReleaseVersion:
    release: tuple[int, int, int]
    phase: int
    phase_num: int
    @property
    def is_final(self) -> bool:
        return self.phase == 1

def parse_version_tag(text) -> ReleaseVersion:
    if not isinstance(text, str) or not (m := _VERSION_RE.match(text.strip())):
        raise UpdateError(f"Некорректная версия: {text!r}")
    release = tuple(int(g) for g in m.group(1, 2, 3))
    kind, num = m.group("kind"), m.group("num")
    if kind is None:
        return ReleaseVersion(release, 1, 0)
    if kind == "hotfix":
        return ReleaseVersion(release, 2, int(num))
    return ReleaseVersion(release, 0, int(num))
```

- [ ] **Step 1.4:** Run same command — expect PASS.
- [ ] **Step 1.5:** Commit `feat(updater): standards-aware version parsing and ordering`.

---

### Task 2: Build identity and channel detection

**Files:**
- Modify: `gemini_translator/utils/updater.py`
- Modify: `translatorFork_MOD.spec` (conditional `datas` entry)
- Test: `tests/test_updater.py`

**Interfaces (Produces):**
```python
class UpdateChannel(enum.Enum):
    WINDOWS_INSTALLED = "windows-installed"; WINDOWS_PORTABLE = "windows-portable"
    MACOS = "macos"; SOURCE_GIT = "source-git"; SOURCE_ARCHIVE = "source-archive"
    DEVELOPMENT = "development"
BUILD_IDENTITY_FILENAME = "update-build.json"
@dataclass(frozen=True)
class BuildIdentity: version: ReleaseVersion; tag: str; commit: str
def read_build_identity() -> BuildIdentity | None          # via api_config.get_resource_path; invalid ⇒ None
def project_root() -> Path                                  # updater.py → parents[2]
def detect_update_channel() -> UpdateChannel
ARCHIVE_IDENTITY_FILENAME = ".translator-update.json"
def read_archive_identity(root: Path) -> dict | None        # {"schema":1,"commit":<40hex>,"files":[...]} else None
```

Detection rules (frozen): identity `None` ⇒ `DEVELOPMENT`; else executable basename `geminitranslator-portable.exe` ⇒ `WINDOWS_PORTABLE`; `translatorfork_mod.exe` ⇒ `WINDOWS_INSTALLED`; `sys.executable` containing `GeminiTranslator.app/Contents/MacOS` ⇒ `MACOS`; anything else ⇒ `DEVELOPMENT`. Non-frozen: `.git` dir at `project_root()` ⇒ `SOURCE_GIT`; else ⇒ `SOURCE_ARCHIVE` (identity validity handled by the checker). CWD must never be consulted.

- [ ] **Step 2.1: Failing tests** (monkeypatch `sys.frozen`, `sys.executable`, `read_build_identity`, and `project_root` — never the real FS):

```python
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
    assert u.read_build_identity() is None                      # missing file
    p.write_text('{"schema":1,"version":"10.5.21","tag":"v10.5.21","commit":"%s"}' % ("a"*40))
    ident = u.read_build_identity()
    assert ident.tag == "v10.5.21" and ident.version.is_final
    p.write_text('{"schema":1,"version":"10.5.21","tag":"v10.9.9","commit":"%s"}' % ("a"*40))
    assert u.read_build_identity() is None                      # tag/version disagree
    p.write_text("not json")
    assert u.read_build_identity() is None

def test_read_archive_identity(tmp_path):
    from gemini_translator.utils import updater as u
    assert u.read_archive_identity(tmp_path) is None
    (tmp_path / u.ARCHIVE_IDENTITY_FILENAME).write_text(
        '{"schema":1,"commit":"%s","files":["main.py"]}' % ("b"*40))
    ident = u.read_archive_identity(tmp_path)
    assert ident["commit"] == "b"*40
    (tmp_path / u.ARCHIVE_IDENTITY_FILENAME).write_text('{"schema":1,"commit":"short"}')
    assert u.read_archive_identity(tmp_path) is None
```

- [ ] **Step 2.2:** Run, expect FAIL.
- [ ] **Step 2.3: Implement** exactly the rules above. `read_build_identity`: `json.load`, require `schema == 1`, `commit` 40-hex, `parse_version_tag(version).is_final`, `tag == f"v{version}"`; any violation ⇒ `None` (never raises). `read_archive_identity`: same guard style, `files` optional list of str. In `translatorFork_MOD.spec` after the `datas` block add:

```python
import os
if os.path.exists('update-build.json'):
    datas += [('update-build.json', '.')]
```

- [ ] **Step 2.4:** Run, expect PASS. Also `python -c "import PyInstaller"` is NOT required — validate the spec edit with `python -c "compile(open('translatorFork_MOD.spec').read(), 'x', 'exec')"`.
- [ ] **Step 2.5:** Commit `feat(updater): build identity file and executable-identity channel detection`.

---

### Task 3: Manifest parsing and channel asset selection

**Files:**
- Modify: `gemini_translator/utils/updater.py`
- Test: `tests/test_updater.py`

**Interfaces (Produces):**
```python
UPDATE_MANIFEST_FILENAME = "update-manifest.json"
@dataclass(frozen=True)
class ReleaseAsset: name: str; url: str; platform: str; channel: str; size: int; sha256: str | None
@dataclass(frozen=True)
class UpdateManifest: version: ReleaseVersion; tag: str; commit: str; assets: tuple[ReleaseAsset, ...]
def parse_update_manifest(data: dict, release_tag: str, gh_assets: list[dict]) -> UpdateManifest
    # raises UpdateError; each manifest asset must resolve to exactly one GitHub asset by exact name,
    # whose URL is taken from browser_download_url. Unmatched manifest asset ⇒ UpdateError.
def select_release_asset(manifest: UpdateManifest, channel: UpdateChannel) -> ReleaseAsset | None
    # None ⇒ manual-only. MACOS prefers name.endswith(".dmg") then ".zip".
    # Two manifest assets for one Windows channel ⇒ None (ambiguous ⇒ manual).
```

Manifest schema (also produced by Task 10's generator — keep in sync):
```json
{"schema": 1, "version": "10.5.22", "tag": "v10.5.22", "commit": "<40hex>",
 "assets": [{"name": "GeminiTranslator-Setup.exe", "platform": "windows",
             "channel": "windows-installed", "size": 12345, "sha256": "<64hex>"}]}
```

- [ ] **Step 3.1: Failing tests:**

```python
def _manifest_data(**over):
    data = {"schema": 1, "version": "10.5.22", "tag": "v10.5.22", "commit": "c"*40,
            "assets": [
                {"name": "GeminiTranslator-Setup.exe", "platform": "windows",
                 "channel": "windows-installed", "size": 10, "sha256": "a"*64},
                {"name": "GeminiTranslator-Portable.exe", "platform": "windows",
                 "channel": "windows-portable", "size": 20, "sha256": "b"*64},
                {"name": "GeminiTranslator-macOS.zip", "platform": "macos",
                 "channel": "macos", "size": 30, "sha256": "c"*64}]}
    data.update(over); return data

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
                           "channel": "macos", "size": 40, "sha256": "d"*64})
    m = u.parse_update_manifest(data, "v10.5.22", _gh_assets(_ALL + ["GeminiTranslator-macOS.dmg"]))
    assert u.select_release_asset(m, u.UpdateChannel.MACOS).name.endswith(".dmg")

def test_manifest_missing_channel_is_manual_not_fallback():
    from gemini_translator.utils import updater as u
    data = _manifest_data(); data["assets"] = [data["assets"][0]]
    m = u.parse_update_manifest(data, "v10.5.22", _gh_assets(_ALL))
    assert u.select_release_asset(m, u.UpdateChannel.WINDOWS_PORTABLE) is None
    assert u.select_release_asset(m, u.UpdateChannel.SOURCE_GIT) is None   # source never binary

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
    data = _manifest_data(); mutate(data)
    with pytest.raises(u.UpdateError):
        u.parse_update_manifest(data, "v10.5.22", _gh_assets(_ALL))

def test_manifest_duplicate_channel_is_ambiguous_manual():
    from gemini_translator.utils import updater as u
    data = _manifest_data()
    data["assets"].append(dict(data["assets"][0], name="GeminiTranslator-Setup2.exe"))
    m = u.parse_update_manifest(data, "v10.5.22", _gh_assets(_ALL + ["GeminiTranslator-Setup2.exe"]))
    assert u.select_release_asset(m, u.UpdateChannel.WINDOWS_INSTALLED) is None
```

- [ ] **Step 3.2:** Run, FAIL. **Step 3.3:** Implement (validation raises `UpdateError` with a specific message per field; selection returns `None` for missing/ambiguous/source/development channels). **Step 3.4:** Run, PASS. **Step 3.5:** Commit `feat(updater): update manifest parsing and strict channel asset selection`.

---

### Task 4: UpdateChecker rewrite + UpdateInfo

**Files:**
- Modify: `gemini_translator/utils/updater.py` (replace old `UpdateChecker` and `_pick_platform_asset` entirely)
- Test: `tests/test_updater.py` (delete the old tests that enshrine removed behavior: `test_update_checker_finds_update`, `test_update_checker_source_no_git_finds_zip`, `test_update_checker_no_update`, `test_update_checker_source_mode`, all four `test_pick_platform_asset_*`; keep/adapt the `_get_real_executable` tests — that helper moves to `update_installer.py` in Task 6)

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class UpdateInfo:
    kind: str                      # "release" | "git" | "archive"
    suppress_id: str               # tag for releases, upstream/remote SHA otherwise
    title_version: str             # human string for the dialog
    description: str
    manual: bool = False
    manual_url: str = ""
    asset: ReleaseAsset | None = None
    commit: str = ""
    zip_url: str = ""              # SHA-pinned zipball for kind=="archive"

class UpdateState(enum.Enum): IDLE; CHECKING; DOWNLOADING; VERIFYING; PREPARING; EXITING

class UpdateChecker(QThread):
    update_available = pyqtSignal(object)   # UpdateInfo
    no_update = pyqtSignal()
    error_occurred = pyqtSignal(str)
    def __init__(self, parent=None, *, manual: bool, session_factory=build_updater_session): ...

def build_updater_session(settings_manager=None) -> requests.Session
    # proxies from settings_manager.load_proxy_settings() when enabled:
    # scheme socks5h:// for SOCKS5, socks4:// for SOCKS4, http:// otherwise;
    # credentials percent-quoted into the URL, never logged; session.max_redirects = 5;
    # trust_env=False so an unrelated system proxy never hijacks updater traffic.
DEFAULT_TIMEOUT = (10, 30)
```

Behavior matrix (each row is a test):
- release: 200 + manifest asset present+valid, remote > local ⇒ `UpdateInfo(kind="release", asset=…)`; remote == local ⇒ `no_update`; remote > local but my channel missing from manifest ⇒ `manual=True` with `manual_url` = release html_url; manifest asset missing from GitHub assets / invalid manifest ⇒ `error_occurred`; **no manifest at all** (legacy release): remote > local ⇒ `manual=True`, else `no_update`; HTTP 404/500 ⇒ `error_occurred("HTTP …")`; network exception ⇒ `error_occurred`; malformed remote tag ⇒ `error_occurred` (never `no_update`).
- `DEVELOPMENT`: manual check ⇒ legacy-style manual announce when remote tag is newer than `__version__` (best effort), silent handled by HomePage (never reaches checker).
- git: no upstream ⇒ `error_occurred` containing "upstream"; fetch timeout/nonzero ⇒ `error_occurred`; ahead ⇒ `UpdateInfo(kind="git", suppress_id=<upstream sha>, commit=<sha>)`; equal ⇒ `no_update`. All git commands: `subprocess.run(..., cwd=project_root(), timeout=60, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, capture_output=True, text=True)`.
- archive: no/invalid local identity ⇒ `UpdateInfo(kind="archive", manual=True, description mentioning неизвестное состояние)`; identity present: GET `commits/main`, sha == local ⇒ `no_update`; different ⇒ `UpdateInfo(kind="archive", commit=sha, suppress_id=sha, zip_url=f"https://api.github.com/repos/{GITHUB_REPO}/zipball/{sha}")`; API error ⇒ `error_occurred`. **The checker never writes QSettings.**

- [ ] **Step 4.1:** Write the failing tests. HTTP is faked by injecting `session_factory=lambda: FakeSession(...)`; `FakeSession.get(url, timeout=..., stream=False)` returns queued `FakeResponse(status_code, json_data, content=b"...")` per URL-substring. Git is faked with `monkeypatch.setattr(u.subprocess, "run", fake_run)` recording commands. Channel is forced via `monkeypatch.setattr(u, "detect_update_channel", ...)` and identity via `read_build_identity`/`read_archive_identity`. Use `qtbot.waitSignal` exactly like the existing tests in this file do. One test per matrix row above, plus:

```python
def test_checker_emits_updateinfo_object(qtbot, monkeypatch):
    ...  # release row asserting isinstance(payload, u.UpdateInfo) and payload.asset.sha256
def test_build_updater_session_socks_credentials():
    from gemini_translator.utils import updater as u
    class SM:
        def load_proxy_settings(self):
            return {"enabled": True, "type": "SOCKS5", "host": "h", "port": 1080,
                    "user": "u@x", "password": "p:w"}
    s = u.build_updater_session(SM())
    assert s.proxies["https"] == "socks5h://u%40x:p%3Aw@h:1080"
    assert s.trust_env is False
def test_build_updater_session_disabled_or_none():
    from gemini_translator.utils import updater as u
    assert u.build_updater_session(None).proxies == {}
```

- [ ] **Step 4.2:** Run, FAIL. **Step 4.3:** Implement checker (`run()` dispatches on channel; every `except Exception as e` ends in `error_occurred.emit(...)`; no bare `no_update` on errors anywhere). Delete `_pick_platform_asset` and the old signal signature; delete obsolete tests listed above in the same commit. **Step 4.4:** Full file run: `python -m pytest tests/test_updater.py -q` PASS. **Step 4.5:** Commit `feat(updater): manifest-driven UpdateChecker with per-channel discovery`.

---

### Task 5: UpdateDownloader with verification

**Files:**
- Modify: `gemini_translator/utils/updater.py`
- Test: `tests/test_updater.py`

**Interfaces (Produces):**
```python
class UpdateDownloader(QThread):
    progress = pyqtSignal(int, int)          # done_bytes, total_bytes (0 if unknown)
    verified = pyqtSignal(str)               # final staged path
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    def __init__(self, url, staging_dir, *, expected_size=None, expected_sha256=None,
                 shape=None, session_factory=build_updater_session, parent=None): ...
    def cancel(self): ...
# shape: "pe" (MZ header) | "zip" (ZipFile + testzip + non-empty) | None (dmg checked at prepare)
```

Rules: download to `<staging>/<final-name>.part` (staging dir created `exist_ok`), stream 64 KiB chunks, `raise_for_status()` first, hash while writing; on completion verify size (exact when declared), sha256 (when declared), shape; then `os.replace(part, final)` and emit `verified`. Any failure/cancel deletes `.part` and never leaves a final-named file. Cancel closes the response and emits only `cancelled`.

- [ ] **Step 5.1:** Failing tests: success-exe (MZ bytes, hash computed with `hashlib` in the test), success-zip (build a real zip in `tmp_path`), wrong-hash ⇒ `failed` + no files left, short-body vs declared size ⇒ `failed`, non-MZ with `shape="pe"` ⇒ `failed`, corrupt zip ⇒ `failed`, HTTP 500 ⇒ `failed`, cancel mid-stream ⇒ `cancelled` + `.part` removed. FakeSession returns a `FakeResponse` with `iter_content(chunk_size)` yielding queued chunks and honouring a `close()` flag the cancel test asserts.
- [ ] **Step 5.2:** FAIL. **Step 5.3:** Implement. **Step 5.4:** PASS. **Step 5.5:** Commit `feat(updater): verifying cancellable UpdateDownloader`.

---

### Task 6: update_installer core — staging, ack protocol, helper launch

**Files:**
- Create: `gemini_translator/utils/update_installer.py` (no Qt imports anywhere in this module)
- Modify: `main.py` (two calls, see 6.4)
- Test: `tests/test_update_installer.py`

**Interfaces (Produces):**
```python
ACK_ENV = "GT_UPDATE_ACK_FILE"
class UpdateInstallError(Exception): ...     # .user_message like UpdateError
def staging_root() -> Path
    # win: %LOCALAPPDATA%/GeminiTranslator/updater ; mac: ~/Library/Application Support/GeminiTranslator/updater
    # else: $XDG_DATA_HOME|~/.local/share /GeminiTranslator/updater   (created on demand)
def update_log_path() -> Path                # staging_root()/"updater.log"
def log_update_event(message: str) -> None   # timestamped append, never raises
def write_startup_acknowledgement() -> None  # if ACK_ENV set: write {"pid":…,"version":APP_VERSION} to that path, then del env; never raises
def cleanup_stale_staging(max_age_days: int = 7) -> None   # best-effort, skips updater.log
def get_real_executable() -> str             # moved verbatim from HomePage._get_real_executable
def clean_helper_env() -> dict               # os.environ minus _PYI_*, plus PYINSTALLER_RESET_ENVIRONMENT=1
def launch_detached_helper(argv: list[str], *, cwd: Path) -> subprocess.Popen
    # win: creationflags CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP|DETACHED_PROCESS; posix: start_new_session=True
    # after Popen: poll() must be None, else UpdateInstallError
def ps_quote(s: str) -> str                  # single-quote PowerShell literal, '' doubling
def sh_quote(s: str) -> str                  # shlex.quote
```

- [ ] **Step 6.1:** Failing tests: `staging_root` honours monkeypatched env per-OS branch (test all three branches by patching `sys.platform` via `monkeypatch.setattr(ui, "sys", types.SimpleNamespace(...))`-free approach — implement `staging_root(platform=sys.platform, env=os.environ)` with injectable args instead, and test those args directly); ack write happy path + env cleared + no-crash when dir missing/env absent; `cleanup_stale_staging` removes an old file, keeps a fresh one and `updater.log`; `ps_quote("it's")=="'it''s'"`; `get_real_executable` tests moved over from `tests/test_updater.py` (same four cases, imports updated); `launch_detached_helper` with `[sys.executable, "-c", "import time; time.sleep(2)"]` returns a live `Popen`.
- [ ] **Step 6.2:** FAIL. **Step 6.3:** Implement; delete `HomePage._get_real_executable` **usages will move in Task 9** (leave HomePage untouched for now; the old copy stays until then — duplicate temporarily, note in commit). **Step 6.4:** In `main.py`, immediately after the `QApplication` instance is created (find the single `QApplication(` construction in `main()`), insert:

```python
from gemini_translator.utils import update_installer as _upd_install
_upd_install.write_startup_acknowledgement()
_upd_install.cleanup_stale_staging()
```

Both are try/except-silent inside the module, so startup can never break. **Step 6.5:** PASS + `python -m pytest tests/ -q -k "update"` green. **Step 6.6:** Commit `feat(updater): installer core with staging, ack protocol and detached helper launch`.

---### Task 7: Windows Installed + Portable PowerShell helpers

**Files:**
- Modify: `gemini_translator/utils/update_installer.py`
- Test: `tests/test_update_installer.py`

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class InstallContext:
    channel: "UpdateChannel"; app_pid: int; real_executable: str
    version_label: str            # tag being installed, for logs/ack naming
def prepare_windows_installed(staged_setup: Path, ctx: InstallContext) -> subprocess.Popen
def prepare_windows_portable(staged_exe: Path, ctx: InstallContext) -> subprocess.Popen
def render_windows_installed_script(...baked params...) -> str   # pure, fully testable
def render_windows_portable_script(...) -> str
HEALTH_WINDOW_S = 90; APP_EXIT_WAIT_S = 120
```

`prepare_windows_installed` additionally: free-space check — `shutil.disk_usage(app_dir).free` must exceed the recursive size of `app_dir` (helper `directory_size(path)`), else `UpdateInstallError` (retryable, nothing changed). `app_dir = Path(ctx.real_executable).parent`; backup dir `staging_root()/f"backup-{version_label}"`; ack file `staging_root()/f"ack-{version_label}.json"` (pre-deleted before launch).

Full installed-helper template (rendered with `ps_quote`d absolute paths; this exact logic, log lines prefixed `[UPD]` with timestamps via a `Log` function appending to the transcript file):

```powershell
$ErrorActionPreference = 'Continue'
function Log($m) { Add-Content -LiteralPath {log} -Value ("[{0}] [UPD] {1}" -f (Get-Date -Format o), $m) }
function Restore {
  Log 'restoring backup'
  robocopy {backup} {appdir} /MIR /R:2 /W:2 | Out-Null
  if ($LASTEXITCODE -ge 8) { Log 'CRITICAL: restore failed; manual recovery from {backup}' ; return $false }
  return $true
}
Log 'waiting for app pid {pid}'
Wait-Process -Id {pid} -Timeout {APP_EXIT_WAIT_S} -ErrorAction SilentlyContinue
if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) { Log 'app still running; aborting untouched'; exit 1 }
Log 'snapshot'
robocopy {appdir} {backup} /MIR /R:2 /W:2 | Out-Null
if ($LASTEXITCODE -ge 8) { Log 'snapshot failed; aborting untouched'; exit 1 }
Log 'running setup'
$setup = Start-Process -FilePath {setup} -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait -PassThru
if ($setup.ExitCode -ne 0) { Log ("setup failed: " + $setup.ExitCode); if (Restore) { Start-Process -FilePath {exe} }; exit 1 }
Remove-Item -LiteralPath {ack} -ErrorAction SilentlyContinue
$env:GT_UPDATE_ACK_FILE = {ack}
$app = Start-Process -FilePath {exe} -PassThru
$deadline = (Get-Date).AddSeconds({HEALTH_WINDOW_S})
while ((Get-Date) -lt $deadline) {
  if (Test-Path -LiteralPath {ack}) {
    Log 'health ack received; cleaning up'
    Remove-Item -Recurse -Force -LiteralPath {backup} -ErrorAction SilentlyContinue
    Remove-Item -Force -LiteralPath {setup} -ErrorAction SilentlyContinue
    exit 0
  }
  if ($app.HasExited) { Log 'new process exited without ack'; if (Restore) { Start-Process -FilePath {exe} }; exit 1 }
  Start-Sleep -Milliseconds 500
}
Log 'HEALTH-TIMEOUT: process alive without ack; keeping backup and staged installer'
exit 2
```

Portable helper: same skeleton; replace snapshot/setup block with:

```powershell
$bak = {exe} + '.bak'
Remove-Item -LiteralPath $bak -Force -ErrorAction SilentlyContinue
$moved = $false
foreach ($i in 1..3) {
  try { Move-Item -LiteralPath {exe} -Destination $bak -Force -ErrorAction Stop; $moved = $true; break }
  catch { Start-Sleep -Seconds 2 }
}
if (-not $moved) { Log 'could not move current exe; aborting untouched'; exit 1 }
try { Move-Item -LiteralPath {staged} -Destination {exe} -Force -ErrorAction Stop }
catch { Log 'staged move failed'; Move-Item -LiteralPath $bak -Destination {exe} -Force; exit 1 }
```
…with rollback branch `Move-Item $bak → {exe}` + relaunch, and success branch removing `$bak`.

- [ ] **Step 7.1:** Failing tests over the **rendered text** (no PowerShell execution): parametrized over both renderers assert — contains `Wait-Process -Id`, contains `-Timeout 120`, contains `HEALTH-TIMEOUT`, `exit 2` only in the alive-at-deadline branch, no `FORCECLOSEAPPLICATIONS` anywhere, `'/NORESTART'` present (installed only), rollback (`Restore` / `$bak`) referenced in both exited-without-ack and setup-failure branches, every embedded path routed through `ps_quote` (test with a path containing `'` and space and assert the doubled-quote form appears), ack pre-delete precedes `$env:GT_UPDATE_ACK_FILE`. `prepare_windows_installed` tests: free-space guard raises `UpdateInstallError` when `directory_size` (monkeypatched) exceeds patched `shutil.disk_usage(...).free`; happy path (monkeypatch `launch_detached_helper` to capture argv) launches `powershell -NoProfile -ExecutionPolicy Bypass -File <script in staging>` and the script file exists with the rendered content.
- [ ] **Step 7.2:** FAIL. **Step 7.3:** Implement. **Step 7.4:** PASS. **Step 7.5:** Commit `feat(updater): transactional Windows installed/portable helpers with health protocol`.

---

### Task 8: macOS bash helper

**Files:**
- Modify: `gemini_translator/utils/update_installer.py`
- Test: `tests/test_update_installer.py`

**Interfaces (Produces):**
```python
def prepare_macos(staged_path: Path, ctx: InstallContext) -> subprocess.Popen
    # ctx.real_executable = .../GeminiTranslator.app/Contents/MacOS/GeminiTranslator
    # bundle path derived by walking up to ".app"; not found ⇒ UpdateInstallError
def render_macos_script(*, staged, bundle, binary_name, ack, log, is_dmg) -> str
```

Template (rendered with `sh_quote`; helper functions `log()`, `fail_before_swap()`, `rollback_after_swap()`):

```bash
#!/bin/bash
exec >> {log} 2>&1
log() { echo "[$(date -u +%FT%TZ)] [UPD] $1"; }
LIVE={bundle}; STAGED={staged}; ACK={ack}; BIN={binary_name}
log "waiting for pid {pid}"
for i in $(seq 1 240); do kill -0 {pid} 2>/dev/null || break; sleep 0.5; done
if kill -0 {pid} 2>/dev/null; then log "app still running; aborting untouched"; exit 1; fi
MNT=""; EXTRACT=""
cleanup_source() { [ -n "$MNT" ] && hdiutil detach "$MNT" -force >/dev/null 2>&1; [ -n "$EXTRACT" ] && rm -rf "$EXTRACT"; }
if {is_dmg}; then
  MNT=$(hdiutil attach -nobrowse -readonly "$STAGED" | awk -F$'\t' '/\/Volumes\//{print $NF}' | tail -1)
  [ -d "$MNT" ] || { log "mount failed"; exit 1; }
  NEW_APP=$(find "$MNT" -maxdepth 1 -name '*.app' | head -1)
else
  EXTRACT=$(mktemp -d)
  unzip -q "$STAGED" -d "$EXTRACT" || { log "unzip failed"; cleanup_source; exit 1; }
  NEW_APP=$(find "$EXTRACT" -maxdepth 2 -name '*.app' | head -1)
fi
[ -d "$NEW_APP/Contents/MacOS" ] || { log "no valid .app in staged update"; cleanup_source; exit 1; }
codesign --verify --deep --strict "$NEW_APP" || { log "codesign verify failed"; cleanup_source; exit 1; }
rm -rf "$LIVE.new" "$LIVE.old"
ditto "$NEW_APP" "$LIVE.new" || { log "ditto failed"; cleanup_source; exit 1; }
cleanup_source
mv "$LIVE" "$LIVE.old" || { log "swap-out failed"; exit 1; }
if ! mv "$LIVE.new" "$LIVE"; then log "swap-in failed"; mv "$LIVE.old" "$LIVE"; exit 1; fi
rollback() { log "rolling back"; rm -rf "$LIVE.rejected"; mv "$LIVE" "$LIVE.rejected"; mv "$LIVE.old" "$LIVE"; }
if ! xattr -cr "$LIVE"; then rollback; "$LIVE/Contents/MacOS/$BIN" >/dev/null 2>&1 & exit 1; fi
rm -f "$ACK"
GT_UPDATE_ACK_FILE="$ACK" "$LIVE/Contents/MacOS/$BIN" >/dev/null 2>&1 &
NEWPID=$!
for i in $(seq 1 180); do
  [ -f "$ACK" ] && { log "health ack; cleanup"; rm -rf "$LIVE.old"; rm -f "$STAGED"; exit 0; }
  kill -0 "$NEWPID" 2>/dev/null || { rollback; "$LIVE/Contents/MacOS/$BIN" >/dev/null 2>&1 & exit 1; }
  sleep 0.5
done
log "HEALTH-TIMEOUT: process alive without ack; keeping $LIVE.old and staged file"
exit 2
```

- [ ] **Step 8.1:** Failing tests, two groups. (a) Rendered-text assertions mirroring Task 7 (codesign **before** any `mv` of `$LIVE`; `xattr -cr` failure path calls `rollback`; `sh_quote` applied — path with space/quote). (b) **Executed** end-to-end on the dev Mac (`@pytest.mark.skipif(sys.platform != "darwin", ...)`): build a fake `Old.app/Contents/MacOS/GT` (shell script writing "old" to a result file) in `tmp_path`, a staged zip containing `New.app` whose binary writes `"new"` **and creates the `$GT_UPDATE_ACK_FILE`**; put fake `codesign`/`xattr`/`hdiutil` shims (exit 0, recording invocation order to a log) at the front of `PATH`; run the rendered script with `bash` for pid `1`-already-dead… use a real short-lived `sleep` process as the old pid. Assert: shim log shows codesign ran before the first swap `mv`; live bundle binary now prints "new"; `.old` removed; ack consumed. Second executed test: staged binary exits 1 without ack ⇒ live bundle back to old, `<bundle>.rejected` exists, staged zip kept.
- [ ] **Step 8.2:** FAIL. **Step 8.3:** Implement. **Step 8.4:** PASS. **Step 8.5:** Commit `feat(updater): verified transactional macOS helper preserving Gatekeeper flow`.

---

### Task 9: Git and source-archive installers + HomePage coordinator

**Files:**
- Modify: `gemini_translator/utils/update_installer.py` (git + archive)
- Modify: `gemini_translator/ui/pages/home_page.py` (update section rewritten; tool-card code untouched)
- Modify: `gemini_translator/utils/updater.py` (add `FunctionWorker(QThread)` running a callable, emitting `done(object)`/`failed(str)`)
- Test: `tests/test_update_installer.py`, `tests/test_updater_home_page.py`

**Interfaces:**
```python
@dataclass(frozen=True)
class GitUpdateResult: old_head: str; new_head: str; requirements_changed: bool; recovery_hint: str
def install_git_update(root: Path, run=subprocess.run) -> GitUpdateResult      # raises UpdateInstallError
def prepare_source_archive(staged_zip: Path, root: Path, ctx: InstallContext) -> subprocess.Popen
def render_archive_helper(...) -> str          # python3 helper source, full journal semantics
```

`install_git_update` sequence (each step checked, stderr excerpts + `old_head` + recovery hint in every error): `rev-parse HEAD` → `rev-parse --abbrev-ref @{u}` (missing ⇒ "нет upstream-ветки…") → `fetch` (timeout 60) → divergence probe `rev-list --left-right --count HEAD...@{u}` (local ahead>0 ⇒ error, no reset ever) → `pull --ff-only --autostash --no-edit` (timeout 300, `GIT_TERMINAL_PROMPT=0`) → `git diff --name-only <old>..HEAD -- requirements.txt`; when changed: `[sys.executable, "-m", "pip", "install", "-r", requirements.txt]` checked (timeout 600). Restart is HomePage's job (checked `QProcess.startDetached`); **no QSettings marker is written for git mode**.

Archive helper (python3, rendered with `repr()` paths; journal dir `staging_root()/f"journal-{sha[:12]}"`): wait for pid (bounded 120 s, `os.kill(pid, 0)` probe); extract zip to temp **stripping the single top-level dir**; refuse absolute/`..` member names; old manifest = `files` from existing `.translator-update.json` (may be absent); backup = copy (preserving rel paths) of every file that will be overwritten **or** is managed-but-absent-from-new (those get deleted after backup); unknown/user files never touched; apply; write new `.translator-update.json` `{"schema":1,"commit":sha,"files":sorted(new_files)}`; `pip install -r requirements.txt` checked; relaunch `[python, "main.py"]` in root with ack env; health-poll ack 90 s with the Task 7/8 three-outcome semantics (exited-without-ack ⇒ restore journal: copy backups back, delete files that were newly added, restore old json; alive ⇒ keep journal, log `HEALTH-TIMEOUT`). Any pre-apply failure ⇒ nothing changed; any apply failure ⇒ journal restore then relaunch old.

HomePage rewrite (update section only):
- Fields: `self._update_state = UpdateState.IDLE`, `self._active_worker = None`, `self._last_silent_error = None`.
- `check_for_updates(silent)`: return unless state is `IDLE`; one-time migration `settings.remove("updater/installed_version"); settings.remove("updater/installed_commit")`; channel `DEVELOPMENT` + silent ⇒ return; set `CHECKING`, button "Проверка..."; construct `UpdateChecker(manual=not silent, session_factory=...)` wired to the three handlers; **keep a reference** and clear it only from handlers (fixes the destroyed-QThread hazard).
- `_on_update_info(info)`: back to `IDLE`/button restored; silent && `info.suppress_id == settings.value("updater/ignored_version")` ⇒ return; dialog identical in style to the old one (RichText body reuse), buttons: install (`"Открыть страницу загрузки"` when `info.manual` else `"Скачать и установить"`), «Напомнить позже», «Игнорировать» (stores `info.suppress_id`).
- Install branch by `info.kind`: manual ⇒ `webbrowser.open(info.manual_url or releases page)`; `release` ⇒ `DOWNLOADING` + `QProgressDialog` (cancel → `downloader.cancel()`) → `verified` ⇒ `PREPARING` in `FunctionWorker` calling the matching `prepare_*` → `_begin_exit()`; `git` ⇒ `FunctionWorker(install_git_update)` with indeterminate progress → checked `QProcess.startDetached(sys.executable, sys.argv)` → `_begin_exit()` (failed start ⇒ error dialog, state `IDLE`, no quit); `archive` ⇒ download `info.zip_url` (`shape="zip"`, no hash) → `prepare_source_archive` → `_begin_exit()`.
- `_begin_exit()`: state `EXITING`; `self.window().setProperty("is_updating", True)`; `QtCore.QTimer.singleShot(15000, lambda: (log_update_event("emergency exit after shutdown timeout"), os._exit(0)))`; `self.window().close()`; if `QtWidgets.QApplication.instance()` still has visible windows after close returns, call `QtWidgets.QApplication.quit()`.
- Every failure handler: state `IDLE`, button restored, silent ⇒ `self._last_silent_error = msg` + `log_update_event`, manual ⇒ warning dialog including `self._last_silent_error` when present. Silent startup failure schedules **one** retry via `QTimer.singleShot(30 * 60 * 1000, ...)`.

- [ ] **Step 9.1:** Failing tests. Git: fake `run` returning scripted results per command prefix — happy path (result fields), missing upstream, fetch nonzero, divergence, pull failure includes `old_head` and hint, requirements-changed triggers checked pip call, pip failure raises with hint. Archive helper: rendered-text checks (journal restore branch, `..`-member refusal, `HEALTH-TIMEOUT` string) **plus** an executed test (any OS — it's python3): fake root with `main.py` printing and ack-writing vs failing, old `.translator-update.json` listing a file the new zip drops ⇒ file removed and restored on rollback; monkeypatched `pip` via a `sitecustomize`-free trick: helper takes `pip_argv` baked in — bake `[sys.executable, "-c", "import sys; sys.exit(0)"]` in tests. HomePage (`tests/test_updater_home_page.py`, offscreen like existing UI tests): state guard blocks a second `check_for_updates`; silent suppression honours `suppress_id`; ignore button writes `ignored_version`; migration removes both legacy keys; error handler restores button text; `no_update` silent shows no dialog (assert no new modal via `QApplication.activeModalWidget()`).
- [ ] **Step 9.2:** FAIL. **Step 9.3:** Implement; delete the now-duplicated `_get_real_executable` from HomePage (import from `update_installer`), drop `tests/test_updater.py::test_get_real_executable_*` originals moved in Task 6, delete obsolete HomePage download/launch tests (`test_show_update_dialog_buttons`, `test_launch_updater_*`) replaced by the new file. **Step 9.4:** `python -m pytest tests/test_update_installer.py tests/test_updater_home_page.py tests/test_updater.py -q` PASS. **Step 9.5:** Commit `feat(updater): git/source-archive installers and HomePage coordinator rewrite`.

---

### Task 10: Release pipeline tools

**Files:**
- Create: `tools/generate_build_identity.py`, `tools/verify_release_tag.py`, `tools/generate_update_manifest.py`
- Test: `tests/test_release_tools.py`

All three: stdlib-only, `main(argv) -> int` + `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`, errors printed to stderr with `RELEASE-GATE:` prefix, exit 1.

- `generate_build_identity.py --tag TAG --commit SHA --output PATH [--version-file gemini_translator/version.py]`: read `__version__` by regex from the version file; require final `X.Y.Z`, `TAG == f"v{__version__}"`, 40-hex SHA; write the Task 2 JSON.
- `verify_release_tag.py --tag TAG [--notes-dir docs/release_notes] [--version-file …]`: same tag/version checks + `docs/release_notes/{TAG}.md` exists and is non-empty (whitespace-only ⇒ fail).
- `generate_update_manifest.py --tag TAG --commit SHA --assets-dir DIR --output PATH [--version-file …]`: classify **exact** names `GeminiTranslator-Setup.exe`→windows-installed, `GeminiTranslator-Portable.exe`→windows-portable, `GeminiTranslator-macOS.dmg`/`GeminiTranslator-macOS.zip`→macos; unknown filename ⇒ fail; duplicates impossible-by-name but a missing Setup, missing Portable, or zero macOS assets ⇒ fail; sha256/size computed from the real files; writes Task 3 schema; also enforces tag/version agreement.

- [ ] **Step 10.1:** Failing tests: happy path per tool against `tmp_path` fixtures (tiny fake artifacts, fake version file), tag/version mismatch, prerelease version rejected, missing notes, unknown asset, missing portable, missing macOS, ZIP-only macOS accepted, manifest round-trips through `parse_update_manifest` (with synthesized gh_assets) and `select_release_asset` picks each channel.
- [ ] **Step 10.2:** FAIL. **Step 10.3:** Implement. **Step 10.4:** PASS. **Step 10.5:** Commit `feat(release): build identity, tag gate and update manifest tools`.

---

### Task 11: Release workflow, installer version, bat metadata

**Files:**
- Modify: `.github/workflows/release.yml`, `windows_installer.iss`, `build_release_dual.bat`
- Modify: `docs/superpowers/specs/2026-08-10-updater-hardening-design.md` (rollout note already covers this; no change unless drift found)

- [ ] **Step 11.1:** `windows_installer.iss`: replace the hard-coded line `AppVersion=10.5.21` with

```
#ifndef MyAppVersion
#define MyAppVersion "10.5.21"
#endif
...
AppVersion={#MyAppVersion}
```

- [ ] **Step 11.2:** Rewrite `release.yml`:
  - `verify` job (ubuntu): checkout; setup Python 3.10 (pip cache); `pip install -r requirements.txt`; `python tools/verify_release_tag.py --tag "${GITHUB_REF_NAME}"` (guarded: only for `refs/tags/*`; `workflow_dispatch` runs fail the gate with a clear message — publication requires a tag); `python tools/run_checks.py --release`.
  - `build` matrix job: `needs: verify`; before `pyinstaller`, run `python tools/generate_build_identity.py --tag "${GITHUB_REF_NAME}" --commit "${GITHUB_SHA}" --output update-build.json`; Windows packaging becomes `iscc /DMyAppVersion="${GITHUB_REF_NAME#v}" windows_installer.iss` (bash shell step for the substitution) — keep artifact uploads as-is.
  - `release` job: `needs: build`; download artifacts; flatten as today; `python tools/generate_update_manifest.py --tag "${GITHUB_REF_NAME}" --commit "${GITHUB_SHA}" --assets-dir release_assets --output release_assets/update-manifest.json`; `softprops/action-gh-release@v2` with `files: release_assets/*`, `body_path: docs/release_notes/${{ github.ref_name }}.md`, `draft: false`, `prerelease: false` (drop `generate_release_notes`).
- [ ] **Step 11.3:** `build_release_dual.bat`: after the `git archive` line, append a PowerShell step that (1) writes `.translator-update.json` (`schema:1`, `commit` = `git rev-parse HEAD`, `files` = zip entry list) and injects it into the source ZIP via `System.IO.Compression`; (2) writes `<name>.sha256` sidecars for every produced ZIP; (3) writes `manual-artifacts.json` next to them marking `channel: "manual"` — names that `generate_update_manifest.py` would reject, so they can never be published as executable updates.
- [ ] **Step 11.4:** Validate: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml'))"`; `tests/test_release_build_script.py` still green (`python -m pytest tests/test_release_build_script.py tests/test_release_metadata_check.py -q`); grep the workflow for `verify_release_tag|generate_build_identity|generate_update_manifest|body_path` — all present.
- [ ] **Step 11.5:** Commit `feat(release): gated release pipeline with manifest and versioned installer`.

---

### Task 12: Cleanup, dead code, full verification

- [ ] **Step 12.1:** `git rm gemini_translator/scripts/updater_script.py` (Task-1 review confirmed zero references incl. tests; re-grep to be safe).
- [ ] **Step 12.2:** Repo-wide greps (must return nothing outside docs/spec history): `installed_version`, `installed_commit` writers; `_pick_platform_asset`; `FORCECLOSEAPPLICATIONS`; `os._exit(0)` in `home_page.py` outside the emergency timer.
- [ ] **Step 12.3:** Full gates: `python -m pytest tests/ -q` and `python tools/run_checks.py`. Fix regressions until green.
- [ ] **Step 12.4:** Update memory notes if behavior contradicts saved memories (updater autostash note stays true; add none unless surprising).
- [ ] **Step 12.5:** Commit `chore(updater): remove dead updater script and finish hardening cleanup`. Do **not** merge to `main`; leave the branch for the user.

---

## Self-Review (performed while writing)

- **Spec coverage:** goals→T4/5/7/8/9; macOS Gatekeeper→T8; identity→T2/T10; channels/assets→T3; network/proxy→T4/5; Windows→T7; git→T9; archive→T9; state/suppression/shutdown→T9; pipeline→T10/11; observability→T6 (`log_update_event` used by every prepare/rollback path); testing strategy rows all land in T1–T10 tests; acceptance 1–9 mapped (8 = Global Constraints; 3 = T3 selection + T10 name gate + T11 bat markers).
- **Placeholder scan:** helper templates and schemas are given in full; mechanical Qt wiring specified by exact names/signatures.
- **Type consistency:** `UpdateInfo.suppress_id`/`kind` consistent across T4/T9; `ReleaseAsset.sha256: str | None` consistent with archive downloads (T5 `expected_sha256=None`); `InstallContext` shared by T7/8/9.
