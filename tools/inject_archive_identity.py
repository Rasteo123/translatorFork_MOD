# -*- coding: utf-8 -*-
"""Вписывает .translator-update.json в source-ZIP дистрибутива.

Архив, собранный git archive, не может содержать генерируемый файл — этот
шаг добавляет установочную идентичность (коммит + список управляемых
файлов) после сборки. Без неё установка считается «неизвестной» и
обновляется только вручную.
"""
import argparse
import json
import subprocess
import sys
import zipfile

try:
    import release_gate_common as gate
except ImportError:
    from tools import release_gate_common as gate

IDENTITY_NAME = ".translator-update.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, dest="zip_path")
    parser.add_argument("--commit", default=None,
                        help="40-hex SHA; по умолчанию git rev-parse HEAD")
    args = parser.parse_args(argv)

    def job():
        commit = args.commit
        if not commit:
            result = subprocess.run(["git", "rev-parse", "HEAD"],
                                    capture_output=True, text=True)
            if result.returncode != 0:
                gate.fail(f"git rev-parse HEAD failed: {result.stderr.strip()}")
            commit = result.stdout.strip()
        gate.require_commit(commit)
        with zipfile.ZipFile(args.zip_path, "a") as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            if IDENTITY_NAME in names:
                gate.fail(f"{args.zip_path} already contains {IDENTITY_NAME}")
            payload = {"schema": 1, "commit": commit.lower(),
                       "files": sorted(names)}
            z.writestr(IDENTITY_NAME, json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"archive identity injected into {args.zip_path} ({commit[:12]})")

    return gate.run_gate(job)


if __name__ == "__main__":
    sys.exit(main())
