# -*- coding: utf-8 -*-
"""Генерирует update-manifest.json из реально собранных артефактов релиза.

Хеши и размеры считаются по файлам, а не декларируются. Ровно известный
набор имён; отсутствие Setup, Portable или обоих macOS-ассетов — отказ.
Ручные ZIP-дистрибутивы сюда не попадают и не могут стать автообновлением.
"""
import argparse
import hashlib
import json
import os
import sys

try:
    import release_gate_common as gate
except ImportError:
    from tools import release_gate_common as gate

# Имя файла → (platform, channel). Только эти имена могут быть в релизе.
KNOWN_ASSETS = {
    "GeminiTranslator-Setup.exe": ("windows", "windows-installed"),
    "GeminiTranslator-Portable.exe": ("windows", "windows-portable"),
    "GeminiTranslator-macOS.dmg": ("macos", "macos"),
    "GeminiTranslator-macOS.zip": ("macos", "macos"),
}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--assets-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--version-file", default="gemini_translator/version.py")
    args = parser.parse_args(argv)

    def job():
        version = gate.read_version(args.version_file)
        gate.require_final_version(version)
        gate.require_tag_matches(args.tag, version)
        gate.require_commit(args.commit)

        names = sorted(
            n for n in os.listdir(args.assets_dir)
            if os.path.isfile(os.path.join(args.assets_dir, n))
            and n != os.path.basename(args.output))
        unknown = [n for n in names if n not in KNOWN_ASSETS]
        if unknown:
            gate.fail(f"unexpected release assets: {unknown}; only "
                      f"{sorted(KNOWN_ASSETS)} may be published")
        channels = {KNOWN_ASSETS[n][1] for n in names}
        if "windows-installed" not in channels:
            gate.fail("required asset GeminiTranslator-Setup.exe is missing")
        if "windows-portable" not in channels:
            gate.fail("required asset GeminiTranslator-Portable.exe is missing")
        if "macos" not in channels:
            gate.fail("at least one macOS asset (DMG or ZIP) is required")

        assets = []
        for name in names:
            path = os.path.join(args.assets_dir, name)
            size = os.path.getsize(path)
            if size <= 0:
                gate.fail(f"asset {name} is empty")
            platform, channel = KNOWN_ASSETS[name]
            assets.append({"name": name, "platform": platform, "channel": channel,
                           "size": size, "sha256": _sha256(path)})

        payload = {"schema": 1, "version": version, "tag": args.tag,
                   "commit": args.commit.lower(), "assets": assets}
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"update manifest written: {args.output} ({len(assets)} assets)")

    return gate.run_gate(job)


if __name__ == "__main__":
    sys.exit(main())
