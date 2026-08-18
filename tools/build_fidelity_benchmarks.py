# -*- coding: utf-8 -*-
"""Сборка конфигов бенчмарка для сравнения промптов на верность перевода.

Конфиги содержат исходный текст глав и глоссарий проекта, поэтому они
собираются локально и в репозиторий не попадают (см. .gitignore).

Примеры:
    python tools/build_fidelity_benchmarks.py --epub "book.epub" \\
        --glossary project_glossary.json --chapters 21 22 23 24 25 \\
        --out benchmarks/prompt_fidelity_local.json

    python tools/build_fidelity_benchmarks.py --epub "book.epub" \\
        --glossary project_glossary.json --synthetic \\
        --out benchmarks/prompt_fidelity_synth.json
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

CHAPTER_FILE_RE = re.compile(r"(\d{4})_(\d+)_")

# Синтетический фрагмент с заранее известным эталоном: ровно три произнесённые
# реплики, две внутренние мысли и набор маркеров приблизительности
# (約莫 / 二十來級 / 足足十斤 / 七八分飽 / 四十萬). Служит для проверки того,
# что промпт не превращает мысль в реплику и не уточняет приблизительное.
SYNTHETIC_SOURCE = """<body>
<h1>第1章 測試</h1>
<p>老者走進院子，看著眼前的少年。</p>
<p>這是誰家的孩子?什麽時候來的?</p>
<p>老者問道:“小子，你叫什麽名字?”</p>
<p>少年約莫十五六歲，個子不高，眼睛十分明亮。</p>
<p>少年答道:“回前輩，我今年十五歲。”</p>
<p>老者點點頭，心中暗道:這孩子的魂力，恐怕已經有二十來級了。</p>
<p>他從袖中取出一包靈藥，足足有十斤之多。</p>
<p>老者道:“拿著，這些夠你用三個月。”</p>
<p>少年猶豫了:說實話，怕被人知道自己的來歷；不說，又不禮貌；撒謊，良心過不去。</p>
<p>最後少年只吃了七八分飽，便起身告辭。</p>
<p>院外傳來一聲悶響，石桌被震得一晃，木凳也被震得一顫。</p>
<p>遠處的軍營裡，四十萬大軍正在集結，實戰課主任站在高處看著這一切。</p>
</body>"""

DEFAULT_CHECKS = {
    "preserve_html_tags": False,
    "allow_cjk": False,
    "glossary_required": False,
    "case_sensitive": False,
    "min_length_ratio": 2.0,
    "max_length_ratio": 5.0,
    "forbidden": ["—"],  # em dash в переводе запрещён
}


def load_glossary(path: Path) -> list[dict]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    return [
        {key: entry[key] for key in ("original", "rus", "note") if key in entry}
        for entry in entries
        if isinstance(entry, dict)
    ]


def chapter_bodies(epub_path: Path, numbers: list[int]) -> dict[str, str]:
    """Возвращает {case_id: <body>…</body>} для указанных номеров глав."""
    wanted = {int(n) for n in numbers}
    bodies: dict[str, str] = {}
    with zipfile.ZipFile(epub_path) as archive:
        for name in sorted(archive.namelist()):
            match = CHAPTER_FILE_RE.search(name)
            if not match or not name.lower().endswith((".xhtml", ".html", ".htm")):
                continue
            number = int(match.group(2))
            if number not in wanted:
                continue
            raw = archive.read(name).decode("utf-8", "ignore")
            body_match = re.search(r"<body[^>]*>(.*)</body>", raw, re.S)
            if not body_match:
                continue
            bodies.setdefault(f"ch{number:03d}", "<body>" + body_match.group(1) + "</body>")
    missing = wanted - {int(cid[2:]) for cid in bodies}
    if missing:
        raise SystemExit(f"главы не найдены в epub: {sorted(missing)}")
    return bodies


def build_config(cases: list[dict], name: str, output_dir: str) -> dict:
    return {
        "name": name,
        "output_dir": output_dir,
        "defaults": {"use_system_instruction": False, "prompt_mode": "project"},
        "prompts": [
            {"id": "current", "mode": "project", "path": "config/default_prompt.txt",
             "use_system_instruction": False},
            {"id": "hybrid", "mode": "project", "path": "config/default_prompt.hybrid.txt",
             "use_system_instruction": False},
        ],
        "models": [{
            "id": "gemini-3-low",
            "provider": "gemini",
            "model": "Gemini 3.0 Flash Preview",
            "use_stream": False,
            "thinking_enabled": True,
            "thinking_level": "low",
            "api_key_env": "GEMINI_API_KEY",
        }],
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--epub", type=Path, help="Путь к исходному epub.")
    parser.add_argument("--glossary", type=Path, required=True, help="project_glossary.json проекта.")
    parser.add_argument("--chapters", type=int, nargs="*", default=[], help="Номера глав.")
    parser.add_argument("--synthetic", action="store_true",
                        help="Добавить синтетический кейс с известным эталоном.")
    parser.add_argument("--out", type=Path, required=True, help="Куда записать конфиг.")
    parser.add_argument("--name", default="prompt-fidelity", help="Имя набора.")
    args = parser.parse_args()

    if not args.chapters and not args.synthetic:
        parser.error("укажите --chapters и/или --synthetic")
    if args.chapters and not args.epub:
        parser.error("для --chapters нужен --epub")

    glossary = load_glossary(args.glossary)
    cases: list[dict] = []

    if args.synthetic:
        cases.append({
            "id": "synth",
            "source_html": SYNTHETIC_SOURCE,
            "glossary": glossary,
            "checks": {**DEFAULT_CHECKS, "min_length_ratio": 1.5, "max_length_ratio": 6.0,
                       "forbidden": DEFAULT_CHECKS["forbidden"] + [
                           "почти десять", "декан", "отбросило", "сорок тысяч", "сорокатысячн",
                       ]},
        })

    for case_id, body in chapter_bodies(args.epub, args.chapters).items() if args.chapters else {}.items():
        cases.append({"id": case_id, "source_html": body, "glossary": glossary,
                      "checks": dict(DEFAULT_CHECKS)})

    config = build_config(cases, args.name, f"benchmark_results/{args.name}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"записан {args.out} — кейсов: {len(cases)}, записей глоссария: {len(glossary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
