# Updater Hardening Design

**Status:** approved for implementation

**Date:** 2026-08-10
**Scope:** installed Windows, Windows Portable, macOS, Git source, source ZIP, and release publication

## Goals

The updater must reliably discover a newer build, select only an asset intended for the current installation type, validate the complete download, and install without blocking the UI. Self-replacing binary and source-archive updates must either start the new application or restore the old one; Git-source failures must preserve a recoverable Git/autostash state. A failed download or installation must never suppress a later retry. Release publication must prove that application metadata, the Git tag, installer metadata, the update manifest, and the uploaded files agree.

The existing macOS behavior that avoids a repeated Gatekeeper prompt is preserved: quarantine attributes are removed only after the downloaded application has passed manifest, hash, bundle-structure, and code-signature checks.

## Non-goals and constraints

- Do not replace the updater with Sparkle, WinSparkle, Squirrel, or another external framework.
- Do not change `gemini_translator.version.__version__` as part of this work; it remains `10.5.21`.
- Do not publish a release from this task. The hardened release workflow must refuse the next tagged release until the maintainer intentionally updates the version and release notes.
- Do not modify or remove unknown user files from source installations.
- Do not touch unrelated working-tree changes.

## Root causes being addressed

1. Release identity is inferred by extracting all digits from a tag, while tags and the version embedded in binaries are allowed to diverge.
2. The updater loses asset metadata when it reduces a GitHub asset to a URL, then falls back to the first arbitrary asset.
3. Download completion is treated as installation success; `installed_version` is persisted before an installer runs.
4. Installed and Portable Windows builds share one asset-selection rule even though they require different update mechanisms.
5. Source installations use global QSettings state rather than installation-local commit identity.
6. Download, Git, and installer operations are embedded in `HomePage`, run synchronously, and have fail-open scripts without a transactional boundary.
7. Release publication is independent from tests and does not validate its tag against the built application.

## Architecture

### Domain and discovery layer

`gemini_translator/utils/updater.py` owns the updater domain model and discovery worker:

- `UpdateChannel`: `WINDOWS_INSTALLED`, `WINDOWS_PORTABLE`, `MACOS`, `SOURCE_GIT`, `SOURCE_ARCHIVE`, and `DEVELOPMENT`.
- `ReleaseAsset`: immutable name, URL, platform, channel, size, and SHA-256.
- `UpdateInfo`: immutable release/tag identity, normalized version, description, commit, and selected asset.
- `UpdateState`: `IDLE`, `CHECKING`, `DOWNLOADING`, `VERIFYING`, `PREPARING`, and `EXITING`.
- `UpdateChecker`: performs network or Git discovery in a worker thread and emits `UpdateInfo` rather than three unrelated strings.
- `UpdateDownloader`: downloads and verifies an asset in a worker thread and returns a staged file.

`gemini_translator/ui/pages/home_page.py` remains the presentation coordinator only. It owns prompts, progress, cancellation, and visible errors, but does not perform HTTP requests, run Git, copy applications, or render platform scripts.

### Installation layer

A new `gemini_translator/utils/update_installer.py` owns preparation and launch of platform-specific installers. It exposes a small interface that accepts a verified staged update and returns either a launched external updater or a structured error. Generated batch, shell, and Python helpers live here and are directly covered by tests. The currently unused `gemini_translator/scripts/updater_script.py` is removed after its useful behavior has been migrated, so there is only one active implementation.

The normal flow is:

1. discover `UpdateInfo`;
2. obtain user consent;
3. download to a unique `.part` file in the updater staging directory;
4. validate response, byte count, SHA-256, and file shape;
5. atomically rename `.part` to the staged asset;
6. prepare an installation strategy and rollback journal;
7. start the detached updater and verify that it started;
8. request orderly application shutdown;
9. external updater swaps files and launches the new process with a one-time health token;
10. the new process writes an acknowledgement; the external updater removes backups only after acknowledgement, otherwise it rolls back.

## Version and build identity

`gemini_translator/version.py` remains the sole source of the product version. This task does not change its current value.

Version comparison uses standards-aware ordering. Normal releases use exactly `X.Y.Z`. Legacy tags matching `X.Y.Z-hotfixN` are normalized to a post-release for read compatibility. Pre-releases order below their matching final release. Malformed or missing versions are errors, never evidence that the application is current.

Official builds contain generated `update-build.json` metadata with schema version, product version, release tag, and commit SHA. Tagged CI builds generate this file before PyInstaller runs. Runtime channel detection is separate because one Windows PyInstaller invocation creates both Installed and Portable outputs: the exact packaged executable identity selects `WINDOWS_INSTALLED` or `WINDOWS_PORTABLE`, while an unknown frozen identity is treated as `DEVELOPMENT` and remains manual-only. A local frozen build without valid release metadata is also a `DEVELOPMENT` build and does not automatically replace itself with an older public release; manual release inspection remains available.

The next release workflow requires all of the following before building:

- tag is exactly `v{__version__}`;
- `__version__` is a final three-part version;
- matching release notes exist;
- the tag points at the commit being built;
- installer version is injected from `__version__`, not hard-coded.

Hotfixes after migration use patch increments instead of `-hotfixN` suffixes. The future version number is intentionally outside this implementation.

## Channel and asset selection

Windows Installed, Windows Portable, and macOS are distinct channels:

- Installed selects only the declared Setup asset.
- Portable selects only the declared Portable executable.
- macOS selects only a declared DMG, with a declared ZIP as fallback.
- Source channels never consume binary release assets.
- Missing or ambiguous channel assets produce a manual-download result; the updater never falls back to the first release asset.

Release publication creates `update-manifest.json` with schema version, product version, tag, commit, and an explicit asset list. Each asset entry contains name, platform, channel, byte size, and lowercase SHA-256. Discovery verifies that manifest version/tag agree with the GitHub release before using it. A newer legacy release without a valid manifest may be announced, but it is manual-only and cannot be automatically executed.

`build_release_dual.bat` continues to create manual ZIP distributions. It also generates manifest/checksum metadata that marks those ZIPs as manual artifacts, ensuring they can never be selected as executable updates.

## Download and network behavior

All updater HTTP traffic uses a dedicated `requests.Session` configured from the application's proxy settings. HTTP, HTTPS, and SOCKS proxy forms are supported using the dependencies already shipped by the project. Proxy credentials are never written to logs.

Requests use explicit connect/read timeouts, redirects are bounded, and non-2xx responses fail before any file is accepted. The downloader checks:

- exact byte count when a size is declared;
- manifest SHA-256;
- Windows PE `MZ` header for executable channels;
- ZIP integrity and required entries for source/ZIP channels;
- mountable image and expected `.app` bundle for DMG during preparation.

Cancellation closes the response, deletes `.part`, and returns to `IDLE`. Downloads and Git commands never run on the GUI thread. A silent startup failure is logged and retried with a bounded delay; a manual check displays the exact failure immediately.

## Platform installation behavior

### Windows Installed

Only a verified Setup executable is accepted. Before running it, the helper snapshots the current application directory into a transaction backup. The detached batch helper waits for the application to close, runs Setup synchronously, checks its exit status, and launches the installed executable with a one-time health token only on success. A missing health acknowledgement or failed installer restores the application-directory backup and relaunches the old executable. Failure retains the staged installer, writes a clear log, does not acknowledge the target version, and therefore cannot suppress another update notification.

### Windows Portable

Only a verified Portable PE is accepted. The helper keeps the current executable as `.bak`, moves the verified staged executable into place, and launches it with a one-time health token. If the new process does not acknowledge startup within the bounded health window, the helper restores `.bak` and relaunches the old executable. Backups and staged files are removed after acknowledgement.

### macOS

The helper mounts the verified DMG or expands the verified ZIP into a staging directory. Before touching the live installation it checks the expected `.app` structure and runs `codesign --verify --deep --strict` on the staged bundle. It copies the new bundle to a sibling `.new` path, then swaps the live bundle to `.old` and `.new` to the live name. A health acknowledgement controls cleanup versus rollback.

After hash, structure, and signature verification—and before the first launch—the updater runs `xattr -cr` on the new live bundle. This deliberately preserves the current seamless update behavior so a normal update does not ask the user to approve the downloaded application in Gatekeeper again. Failure of required copy/swap/launch commands is fatal and triggers rollback; quarantine removal failure is logged and causes rollback rather than an unverified launch.

### Git source

Source-mode detection checks only the calculated project root, never an unrelated current working directory. Discovery runs noninteractive `git fetch` with a timeout, verifies an upstream exists, and identifies the exact upstream SHA. Update installation uses a bounded, noninteractive fast-forward-only pull with autostash. It records the old HEAD and the resulting stash/reflog recovery information before changing the tree. Divergence, missing upstream, credential prompts, or stash conflicts produce an actionable error rather than `no_update`; they never trigger a destructive reset of user changes.

The ignored identifier is the upstream commit SHA, not the constant `source`, so ignoring one update never hides later commits. A detached restart result is checked; the current application quits only if the replacement process was started successfully. Changed requirements are installed with a checked exit status before restart, and failure remains visible together with the recorded old HEAD and recovery instructions. No global installed marker is advanced for Git mode.

### Source ZIP

Distributed source archives contain installation-local `.translator-update.json` with commit SHA and a manifest of managed files. An archive without trustworthy local identity is reported as unknown, not silently stamped with the current remote SHA.

The remote archive URL is pinned to the exact detected SHA. It is fully downloaded and validated before extraction into a staging directory. Managed files that will be replaced or removed are backed up in a transaction directory; unknown and user-created files are never deleted. Dependencies are installed with a checked status. Commit state is written locally only after file replacement and successful restart acknowledgement. Extraction or dependency failure restores the journal and leaves the old commit state unchanged.

## State, suppression, and shutdown

The updater permits one operation at a time. UI controls reflect the explicit `UpdateState` and are restored on every terminal path.

`updater/installed_version` is removed from decision-making and deleted during migration. The embedded build identity is the only binary installation truth. `ignored_version` remains exact-release suppression for release builds; source suppression uses a commit SHA. Manual checks always bypass ignore suppression.

Installation starts only after the detached helper reports successful process creation. The application then requests its normal close path so settings, projects, proxy tunnels, and workers can flush. A bounded emergency exit is retained only after shutdown has been requested and timed out; it is logged as an abnormal condition.

## Release pipeline

`.github/workflows/release.yml` gains a `verify` job. It installs test dependencies, runs `tools/run_checks.py --release`, validates the tag and generated build identity, and must pass before platform build jobs start. The release job depends on all builds and manifest generation, and publication remains impossible after any verification/build failure.

The manifest-generation script computes hashes and sizes from the actual collected artifacts. It rejects duplicate channel/platform pairs, missing required Installed/Portable/macOS assets, unexpected executable extensions, and a version/tag mismatch. `windows_installer.iss` receives its `AppVersion` through an Inno preprocessor define populated from `version.py`.

The ordinary test workflow remains independent for branches and pull requests, while the release workflow repeats the release-critical gate so a concurrently failing test workflow can never publish a release.

## Error reporting and observability

Update logs use an installation-specific application-data location and include timestamps, channel, release identity, state transitions, command exit codes, and rollback outcome. URLs are logged without sensitive query data and proxy passwords are redacted. Silent background failures do not open modal dialogs, but their retry and last error remain available to the manual check path.

No API schema failure, missing asset, malformed version, missing upstream, installer error, or restart failure is represented as `no_update`.

## Testing strategy

Tests are added before production changes and cover:

- final, pre-release, legacy-hotfix, malformed, and equal-version ordering;
- release-tag/build-version agreement without changing the current version;
- Installed, Portable, macOS, missing, ambiguous, and wrong-platform asset matrices;
- proxy construction and credential redaction;
- 404/500 responses, redirect bounds, size mismatch, hash mismatch, invalid PE, corrupt ZIP, cancellation, and cleanup;
- no `installed_version` suppression after failed launch or installer failure;
- Portable and macOS health acknowledgement and rollback using temporary directories and fake commands;
- preservation of the macOS verification-then-`xattr -cr` ordering;
- source project-root detection, missing upstream, timeout, exact SHA ignore behavior, and restart failure;
- source-ZIP unknown first-run state, SHA-pinned URL, staging, journal rollback, and local state isolation;
- manifest generation against the actual workflow asset names;
- release workflow dependency on verification and tag/version checks;
- `HomePage` state restoration and the absence of synchronous HTTP/Git work on the UI thread.

Platform replacement tests operate only in isolated temporary directories. They do not execute or overwrite the developer's real executable or `.app` bundle.

## Acceptance criteria

1. A failed download, verification, installer, copy, dependency update, or restart remains retryable and is never marked installed.
2. Installed and Portable Windows builds select and install only their own channels.
3. A wrong-platform or manual ZIP asset cannot be executed or copied over the application.
4. Source first-run and ignore behavior cannot suppress a newer commit incorrectly.
5. The GUI remains responsive and cancellation removes partial data.
6. macOS update verification occurs before `xattr -cr`, and a healthy update launches without a repeated Gatekeeper approval prompt under the same signing conditions as the current release.
7. Release publication cannot occur unless tests, strict metadata, tag, build identity, installer version, manifest, and assets agree.
8. Current product version remains `10.5.21` throughout this implementation.
9. Focused updater tests and the full project check suite pass with no changes to unrelated user files.

## Rollout note

The existing published `v10.5.21-hotfix24` binaries contain the old updater and cannot be retroactively changed. The hardened code becomes effective when a future, intentionally versioned release is published. This task prepares and gates that future release without changing the current product version.
