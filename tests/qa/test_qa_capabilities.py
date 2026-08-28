from dataclasses import replace

import pytest

from gemini_translator.qa.capabilities import (
    CAPABILITY_DESCRIPTIONS,
    QaCapabilityKey,
    QaCapabilityRuntimeStatus,
    QaCapabilitySettings,
)


def test_capabilities_have_safe_independent_defaults():
    settings = QaCapabilitySettings()
    assert settings.razdel_enabled is True
    assert settings.language_tool_enabled is False
    assert settings.slovnet_enabled is False
    assert settings.cometkiwi_enabled is False

    changed = replace(settings, slovnet_enabled=True)
    assert changed.slovnet_enabled is True
    assert changed.razdel_enabled is True
    assert changed.language_tool_enabled is False
    assert changed.cometkiwi_enabled is False


def test_every_capability_has_complete_user_facing_metadata():
    assert set(CAPABILITY_DESCRIPTIONS) == set(QaCapabilityKey)
    for item in CAPABILITY_DESCRIPTIONS.values():
        assert item.title
        assert item.summary
        assert item.load_level in {"low", "medium", "high"}
        assert item.resources
        assert set(item.resources) <= {"cpu", "memory", "gpu", "network"}
        assert item.speed_impact
        assert item.quality_benefit
        assert item.quality_risk
        assert item.network_policy


def test_capability_fingerprint_changes_only_with_flags():
    base = QaCapabilitySettings()
    assert base.fingerprint() == QaCapabilitySettings().fingerprint()
    assert len(base.fingerprint()) == 20
    assert base.fingerprint() != replace(
        base, language_tool_enabled=True
    ).fingerprint()


def test_runtime_status_rejects_negative_measurements():
    with pytest.raises(ValueError):
        QaCapabilityRuntimeStatus(
            key=QaCapabilityKey.COMETKIWI,
            state="available",
            last_duration_seconds=-1,
        )

    with pytest.raises(ValueError):
        QaCapabilityRuntimeStatus(
            key=QaCapabilityKey.COMETKIWI,
            state="available",
            installed_size_bytes=-1,
        )


def test_runtime_status_keeps_persisted_settings_separate():
    settings = QaCapabilitySettings()
    status = QaCapabilityRuntimeStatus(
        key=QaCapabilityKey.RAZDEL,
        state="unavailable",
        reason="dependency missing",
        last_duration_seconds=0,
        installed_size_bytes=0,
    )

    assert settings.razdel_enabled is True
    assert status.state == "unavailable"
    assert settings.fingerprint() == QaCapabilitySettings().fingerprint()
