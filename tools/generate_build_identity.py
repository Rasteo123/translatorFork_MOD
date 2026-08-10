# -*- coding: utf-8 -*-
"""Генерирует update-build.json — встроенную идентичность релизной сборки.

CI вызывает его до PyInstaller; приложение по этому файлу отличает
официальную сборку своего тега от DEVELOPMENT-сборки.
"""
import argparse
import json
import sys

try:
    import release_gate_common as gate
except ImportError:  # запуск как python tools/generate_build_identity.py
    from tools import release_gate_common as gate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--version-file", default="gemini_translator/version.py")
    args = parser.parse_args(argv)

    def job():
        version = gate.read_version(args.version_file)
        gate.require_final_version(version)
        gate.require_tag_matches(args.tag, version)
        gate.require_commit(args.commit)
        payload = {"schema": 1, "version": version, "tag": args.tag,
                   "commit": args.commit.lower()}
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"build identity written: {args.output} ({args.tag})")

    return gate.run_gate(job)


if __name__ == "__main__":
    sys.exit(main())
