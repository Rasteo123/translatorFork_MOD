# -*- coding: utf-8 -*-
"""Тесты релизных инструментов: identity, гейт тега, генерация манифеста."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import generate_build_identity
import generate_update_manifest
import verify_release_tag


@pytest.fixture
def version_file(tmp_path):
    vf = tmp_path / "version.py"
    vf.write_text('__version__ = "10.5.22"\nAPP_VERSION = f"V {__version__}"\n')
    return vf


def test_generate_build_identity_happy(tmp_path, version_file):
    out = tmp_path / "update-build.json"
    rc = generate_build_identity.main([
        "--tag", "v10.5.22", "--commit", "a" * 40,
        "--output", str(out), "--version-file", str(version_file)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert data == {"schema": 1, "version": "10.5.22", "tag": "v10.5.22",
                    "commit": "a" * 40}


@pytest.mark.parametrize("args_over", [
    {"--tag": "v10.5.23"},          # тег не совпадает с версией
    {"--commit": "zzz"},            # некорректный SHA
])
def test_generate_build_identity_rejects(tmp_path, version_file, args_over, capsys):
    args = {"--tag": "v10.5.22", "--commit": "a" * 40,
            "--output": str(tmp_path / "o.json"), "--version-file": str(version_file)}
    args.update(args_over)
    argv = [x for pair in args.items() for x in pair]
    rc = generate_build_identity.main(argv)
    assert rc == 1
    assert "RELEASE-GATE:" in capsys.readouterr().err
    assert not (tmp_path / "o.json").exists()


def test_generate_build_identity_rejects_prerelease_version(tmp_path, capsys):
    vf = tmp_path / "version.py"
    vf.write_text('__version__ = "10.6.0-rc1"\n')
    rc = generate_build_identity.main([
        "--tag", "v10.6.0-rc1", "--commit", "a" * 40,
        "--output", str(tmp_path / "o.json"), "--version-file", str(vf)])
    assert rc == 1


def test_verify_release_tag_happy(tmp_path, version_file):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "v10.5.22.md").write_text("## Изменения\n- надёжный апдейтер\n")
    rc = verify_release_tag.main([
        "--tag", "v10.5.22", "--notes-dir", str(notes),
        "--version-file", str(version_file)])
    assert rc == 0


@pytest.mark.parametrize("tag,notes_body", [
    ("v10.5.23", "notes"),   # тег не совпадает с версией
    ("v10.5.22", None),      # нет файла заметок
    ("v10.5.22", "   \n"),   # заметки пустые
])
def test_verify_release_tag_rejects(tmp_path, version_file, tag, notes_body, capsys):
    notes = tmp_path / "notes"
    notes.mkdir()
    if notes_body is not None:
        (notes / "v10.5.22.md").write_text(notes_body)
    rc = verify_release_tag.main([
        "--tag", tag, "--notes-dir", str(notes), "--version-file", str(version_file)])
    assert rc == 1
    assert "RELEASE-GATE:" in capsys.readouterr().err


def _make_assets(tmp_path, names):
    d = tmp_path / "assets"
    d.mkdir(exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"MZ" + n.encode())
    return d


_FULL_SET = ["GeminiTranslator-Setup.exe", "GeminiTranslator-Portable.exe",
             "GeminiTranslator-macOS.dmg", "GeminiTranslator-macOS.zip"]


def test_generate_manifest_happy_and_roundtrip(tmp_path, version_file):
    assets_dir = _make_assets(tmp_path, _FULL_SET)
    out = tmp_path / "update-manifest.json"
    rc = generate_update_manifest.main([
        "--tag", "v10.5.22", "--commit", "c" * 40,
        "--assets-dir", str(assets_dir), "--output", str(out),
        "--version-file", str(version_file)])
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["schema"] == 1 and data["tag"] == "v10.5.22"
    by_name = {a["name"]: a for a in data["assets"]}
    setup = by_name["GeminiTranslator-Setup.exe"]
    payload = (assets_dir / "GeminiTranslator-Setup.exe").read_bytes()
    assert setup["sha256"] == hashlib.sha256(payload).hexdigest()
    assert setup["size"] == len(payload)
    assert setup["channel"] == "windows-installed"

    # круговая проверка: домен апдейтера принимает манифест и выбирает каналы
    from gemini_translator.utils import updater as u
    gh = [{"name": n, "browser_download_url": f"https://dl/{n}"} for n in by_name]
    manifest = u.parse_update_manifest(data, "v10.5.22", gh)
    assert u.select_release_asset(manifest, u.UpdateChannel.WINDOWS_INSTALLED).name \
        == "GeminiTranslator-Setup.exe"
    assert u.select_release_asset(manifest, u.UpdateChannel.WINDOWS_PORTABLE).name \
        == "GeminiTranslator-Portable.exe"
    assert u.select_release_asset(manifest, u.UpdateChannel.MACOS).name.endswith(".dmg")


def test_generate_manifest_zip_only_macos_accepted(tmp_path, version_file):
    assets_dir = _make_assets(tmp_path, ["GeminiTranslator-Setup.exe",
                                         "GeminiTranslator-Portable.exe",
                                         "GeminiTranslator-macOS.zip"])
    rc = generate_update_manifest.main([
        "--tag", "v10.5.22", "--commit", "c" * 40,
        "--assets-dir", str(assets_dir), "--output", str(tmp_path / "m.json"),
        "--version-file", str(version_file)])
    assert rc == 0


@pytest.mark.parametrize("names", [
    ["GeminiTranslator-Portable.exe", "GeminiTranslator-macOS.zip"],   # нет Setup
    ["GeminiTranslator-Setup.exe", "GeminiTranslator-macOS.zip"],      # нет Portable
    ["GeminiTranslator-Setup.exe", "GeminiTranslator-Portable.exe"],   # нет macOS
    _FULL_SET + ["random-artifact.bin"],                               # неизвестный файл
])
def test_generate_manifest_rejects_bad_asset_sets(tmp_path, version_file, names, capsys):
    assets_dir = _make_assets(tmp_path, names)
    rc = generate_update_manifest.main([
        "--tag", "v10.5.22", "--commit", "c" * 40,
        "--assets-dir", str(assets_dir), "--output", str(tmp_path / "m.json"),
        "--version-file", str(version_file)])
    assert rc == 1
    assert "RELEASE-GATE:" in capsys.readouterr().err
    assert not (tmp_path / "m.json").exists()


def test_inject_archive_identity(tmp_path):
    import zipfile
    import inject_archive_identity
    zpath = tmp_path / "source.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("main.py", "print('x')")
        z.writestr("gemini_translator/version.py", "__version__ = '10.5.22'")
    rc = inject_archive_identity.main(["--zip", str(zpath), "--commit", "d" * 40])
    assert rc == 0
    with zipfile.ZipFile(zpath) as z:
        data = json.loads(z.read(".translator-update.json"))
    assert data["commit"] == "d" * 40
    assert data["files"] == ["gemini_translator/version.py", "main.py"]
    # повторная инъекция — отказ, а не дубль записи
    assert inject_archive_identity.main(["--zip", str(zpath), "--commit", "d" * 40]) == 1


def test_generate_manifest_rejects_tag_version_mismatch(tmp_path, version_file):
    assets_dir = _make_assets(tmp_path, _FULL_SET)
    rc = generate_update_manifest.main([
        "--tag", "v10.9.9", "--commit", "c" * 40,
        "--assets-dir", str(assets_dir), "--output", str(tmp_path / "m.json"),
        "--version-file", str(version_file)])
    assert rc == 1
