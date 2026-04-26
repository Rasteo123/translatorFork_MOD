"""Command line interface for prompt/model benchmarks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .runner import BenchmarkConfigError, BenchmarkRunner


def _csv_filter(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run prompt/model benchmarks for translation snippets.",
    )
    parser.add_argument("config", help="Path to a benchmark JSON config.")
    parser.add_argument("--output-dir", help="Directory for results.json, results.csv, summary.md.")
    parser.add_argument(
        "--prompt-only",
        action="store_true",
        help="Build and save prompts without calling any model.",
    )
    parser.add_argument(
        "--save-prompts",
        action="store_true",
        help="Save compiled prompts alongside results.",
    )
    parser.add_argument("--cases", help="Comma-separated case ids to run.")
    parser.add_argument("--prompts", help="Comma-separated prompt ids to run.")
    parser.add_argument("--models", help="Comma-separated model ids to run.")
    parser.add_argument("--limit", type=int, help="Maximum number of matrix runs.")
    parser.add_argument(
        "--list",
        action="store_true",
        help="List cases/prompts/models from config and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    filters = {
        "cases": _csv_filter(args.cases),
        "prompts": _csv_filter(args.prompts),
        "models": _csv_filter(args.models),
    }
    filters = {key: value for key, value in filters.items() if value}

    try:
        runner = BenchmarkRunner(
            args.config,
            output_dir=args.output_dir,
            prompt_only=args.prompt_only,
            save_prompts=args.save_prompts,
            filters=filters,
            limit=args.limit,
        )
        if args.list:
            print(json.dumps(runner.list_items(), ensure_ascii=False, indent=2))
            return 0

        report = runner.run()
        output_dir = Path(runner.output_dir)
        best = (report.get("summary") or [{}])[0]
        print(f"Benchmark complete: {output_dir}")
        if best and best.get("avg_score") is not None:
            print(
                "Best: prompt={prompt_id} model={model_id} score={avg_score}".format(
                    **best
                )
            )
        elif args.prompt_only:
            print("Prompt-only run: quality ranking is not available until model outputs are collected.")
        return 0
    except BenchmarkConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Benchmark interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
