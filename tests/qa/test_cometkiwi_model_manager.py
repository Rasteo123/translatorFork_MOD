"""Large licensed weights arrive only when asked for, verified, and reversibly."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from gemini_translator.qa.estimators.cometkiwi_model_manager import (
    CometKiwiLicenseNotAccepted,
    CometKiwiModelManager,
    CometKiwiModelManifest,
    OptionalEstimatorDependencyMissing,
)


_WEIGHTS = b"pretend these are model weights"
_CONFIG = b"pretend this is a config"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(**overrides) -> CometKiwiModelManifest:
    values = {
        "model": "wmt22-cometkiwi-da",
        "source_url": "https://example.invalid/cometkiwi",
        "size_bytes": len(_WEIGHTS) + len(_CONFIG),
        "sha256_by_file": {
            "model.ckpt": _digest(_WEIGHTS),
            "hparams.yaml": _digest(_CONFIG),
        },
        "license_name": "CC BY-NC-SA 4.0",
        "license_url": "https://example.invalid/license",
        "minimum_ram_bytes": 4 * 1024**3,
    }
    values.update(overrides)
    return CometKiwiModelManifest(**values)


def _downloader(files=None):
    payloads = files or {"model.ckpt": _WEIGHTS, "hparams.yaml": _CONFIG}
    seen: list[str] = []

    async def download(url: str) -> bytes:
        seen.append(url)
        return payloads[url.rsplit("/", 1)[-1]]

    download.seen = seen  # type: ignore[attr-defined]
    return download


def _manager(tmp_path: Path, downloader=None, **overrides) -> CometKiwiModelManager:
    return CometKiwiModelManager(
        tmp_path / "cometkiwi", _manifest(**overrides), downloader or _downloader()
    )


def test_the_manifest_names_everything_a_user_needs_before_agreeing():
    """A licence nobody can read before installing is not a licence."""
    manifest = _manifest()

    assert manifest.license_name == "CC BY-NC-SA 4.0"
    assert manifest.license_url.startswith("https://")
    assert manifest.minimum_ram_bytes > 0
    assert set(manifest.sha256_by_file) == {"model.ckpt", "hparams.yaml"}


@pytest.mark.parametrize(
    "changes",
    [
        {"model": ""},
        {"license_name": " "},
        {"license_url": ""},
        {"minimum_ram_bytes": 0},
        {"size_bytes": -1},
        {"sha256_by_file": {}},
        {"sha256_by_file": {"../escape.ckpt": "0" * 64}},
        {"sha256_by_file": {"model.ckpt": "short"}},
    ],
)
def test_an_unusable_manifest_is_refused(changes):
    """A manifest is the only thing standing between a user and an unknown download."""
    with pytest.raises(ValueError):
        _manifest(**changes)


def test_nothing_is_installed_until_the_licence_is_accepted(tmp_path):
    """Enabling a capability must never start a multi-gigabyte download."""
    downloader = _downloader()
    manager = _manager(tmp_path, downloader)

    with pytest.raises(CometKiwiLicenseNotAccepted):
        asyncio.run(manager.install(license_accepted=False))

    assert downloader.seen == []
    assert manager.status().state == "missing"
    assert manager.status().license_name == "CC BY-NC-SA 4.0"


def test_an_accepted_install_verifies_every_file_and_reports_its_size(tmp_path):
    """Weights that do not hash to what the manifest says are not these weights."""
    manager = _manager(tmp_path)

    status = asyncio.run(manager.install(license_accepted=True))

    assert status.state == "ready"
    assert status.model == "wmt22-cometkiwi-da"
    assert status.installed_size_bytes == len(_WEIGHTS) + len(_CONFIG)
    assert (manager.model_dir / "model.ckpt").read_bytes() == _WEIGHTS


def test_a_corrupted_download_leaves_nothing_behind(tmp_path):
    """A half-written model directory would look installed and fail at run time."""
    manager = _manager(tmp_path, _downloader({"model.ckpt": b"wrong", "hparams.yaml": _CONFIG}))

    with pytest.raises(OptionalEstimatorDependencyMissing):
        asyncio.run(manager.install(license_accepted=True))

    assert manager.status().state == "missing"
    assert list(manager.root.glob("*")) == []


def test_a_foreign_manifest_is_refused_rather_than_installed(tmp_path):
    """The manager owns one model; installing another one under its name is a bug."""
    manager = _manager(tmp_path)

    with pytest.raises(OptionalEstimatorDependencyMissing):
        asyncio.run(
            manager.install(_manifest(model="another-model"), license_accepted=True)
        )


def test_uninstall_removes_the_model_and_nothing_above_it(tmp_path):
    """Deleting more than what was installed would take the user's data with it."""
    manager = _manager(tmp_path)
    neighbour = manager.root
    asyncio.run(manager.install(license_accepted=True))
    neighbour.mkdir(parents=True, exist_ok=True)
    (neighbour / "unrelated.txt").write_text("keep me", encoding="utf-8")

    status = manager.uninstall()

    assert status.state == "missing"
    assert not manager.model_dir.exists()
    assert (neighbour / "unrelated.txt").read_text(encoding="utf-8") == "keep me"


def test_a_missing_file_is_reported_as_invalid_not_as_ready(tmp_path):
    """Half a model must never be handed to the runner as if it were whole."""
    manager = _manager(tmp_path)
    asyncio.run(manager.install(license_accepted=True))

    (manager.model_dir / "hparams.yaml").unlink()

    status = manager.status()
    assert status.state == "invalid"
    assert status.reason == "missing:hparams.yaml"


# --- what the card tells the user ------------------------------------------


def _settings(**overrides):
    from gemini_translator.qa.capabilities import QaCapabilitySettings
    from gemini_translator.qa.settings import QaSettings

    values = {
        "capabilities": QaCapabilitySettings(cometkiwi_enabled=True),
        "cometkiwi_runner_path": "/opt/cometkiwi/run.py",
        "cometkiwi_model": "wmt22-cometkiwi-da",
        "cometkiwi_device": "cpu",
        "cometkiwi_license_accepted": True,
    }
    values.update(overrides)
    return QaSettings(**values)


def _ready_status():
    from gemini_translator.qa.estimators.cometkiwi_model_manager import (
        CometKiwiModelStatus,
    )

    return CometKiwiModelStatus(
        state="ready",
        model="wmt22-cometkiwi-da",
        installed_size_bytes=2 * 1024**3,
        license_name="CC BY-NC-SA 4.0",
    )


def test_the_card_names_every_missing_part_instead_of_installing_it():
    """A checkbox that silently downloads gigabytes is the thing to avoid here."""
    from gemini_translator.qa.capabilities import QaCapabilitySettings
    from gemini_translator.qa.estimators.cometkiwi_model_manager import (
        describe_cometkiwi_setup,
    )

    text = describe_cometkiwi_setup(
        _settings(
            capabilities=QaCapabilitySettings(cometkiwi_enabled=True),
            cometkiwi_runner_path="",
            cometkiwi_license_accepted=False,
        ),
        None,
    )

    assert "требует настройки" in text
    assert "путь к runner" in text
    assert "принятая лицензия" in text
    assert "установленные веса" in text


def test_a_ready_card_shows_the_model_licence_size_and_last_run():
    """The user decides whether to keep it on from what it costs, so show that."""
    from gemini_translator.qa.estimators.cometkiwi_model_manager import (
        describe_cometkiwi_setup,
    )

    text = describe_cometkiwi_setup(_settings(), _ready_status(), 12.5)

    assert "wmt22-cometkiwi-da" in text
    assert "CC BY-NC-SA 4.0" in text
    assert "2.0 ГБ" in text
    assert "устройство cpu" in text
    assert "12.5 c" in text


def test_an_unchecked_capability_says_nothing_at_all():
    """A card for something the user turned off is noise."""
    from gemini_translator.qa.capabilities import QaCapabilitySettings
    from gemini_translator.qa.estimators.cometkiwi_model_manager import (
        describe_cometkiwi_setup,
    )

    text = describe_cometkiwi_setup(
        _settings(capabilities=QaCapabilitySettings(cometkiwi_enabled=False)),
        _ready_status(),
    )

    assert text == ""
