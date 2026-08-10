# -*- coding: utf-8 -*-
"""Гейт релиза: тег, версия и заметки к релизу обязаны согласоваться.

Запускается verify-джобой релизного workflow до любых сборок. Публикация
следующего тега невозможна, пока мейнтейнер осознанно не поднял версию и
не написал docs/release_notes/<тег>.md.
"""
import argparse
import os
import sys

try:
    import release_gate_common as gate
except ImportError:
    from tools import release_gate_common as gate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--notes-dir", default="docs/release_notes")
    parser.add_argument("--version-file", default="gemini_translator/version.py")
    args = parser.parse_args(argv)

    def job():
        version = gate.read_version(args.version_file)
        gate.require_final_version(version)
        gate.require_tag_matches(args.tag, version)
        notes_path = os.path.join(args.notes_dir, f"{args.tag}.md")
        if not os.path.isfile(notes_path):
            gate.fail(f"release notes {notes_path} do not exist; write them "
                      "intentionally before tagging")
        with open(notes_path, "r", encoding="utf-8") as f:
            if not f.read().strip():
                gate.fail(f"release notes {notes_path} are empty")
        print(f"release gate ok: {args.tag} matches {version}, notes present")

    return gate.run_gate(job)


if __name__ == "__main__":
    sys.exit(main())
