"""Runner for prompt/model benchmark matrices."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import json
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace
from typing import Any

from gemini_translator.api import config as api_config
from gemini_translator.core.worker_helpers.prompt_builder import PromptBuilder
from gemini_translator.utils.text import safe_format

from .evaluator import estimate_tokens, evaluate_translation


DEFAULT_KEY_ENVS = {
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
}


class BenchmarkConfigError(ValueError):
    pass


@dataclass
class PromptBundle:
    user_prompt: str
    system_instruction: str | None
    debug_report: str


class _BenchmarkContextManager:
    def __init__(self, glossary_entries: list[dict[str, Any]] | None = None):
        self.glossary_entries = list(glossary_entries or [])

    def prepare_html_for_translation(self, text_content):
        return text_content or ""

    def format_glossary_for_prompt(self, text_content="", current_chapters_list=None):
        usable_entries = []
        source_text = str(text_content or "")
        for entry in self.glossary_entries:
            if not isinstance(entry, dict):
                continue
            original = str(entry.get("original") or entry.get("source") or "").strip()
            translated = str(entry.get("rus") or entry.get("translation") or entry.get("target") or "").strip()
            note = str(entry.get("note") or "").strip()
            if not original or not translated:
                continue
            if source_text and original not in source_text:
                continue
            item = {"original": original, "rus": translated}
            if note:
                item["note"] = note
            usable_entries.append(item)

        if not usable_entries:
            return ""
        return json.dumps(usable_entries, ensure_ascii=False, indent=2)


class _NoopSettingsManager:
    def increment_request_count(self, key_to_update, model_id):
        return True

    def decrement_request_count(self, key_to_update, model_id):
        return True


class _BenchmarkWorker:
    def __init__(
        self,
        provider_config: dict[str, Any],
        model_config: dict[str, Any],
        system_instruction: str | None,
        model_spec: dict[str, Any],
    ):
        self.provider_config = provider_config
        self.model_config = model_config
        self.prompt_builder = SimpleNamespace(system_instruction=system_instruction)
        self.settings_manager = _NoopSettingsManager()
        self.api_key = ""
        self.model_id = model_config.get("id", "")
        self.is_cancelled = False
        self.debug_logging_enabled = bool(model_spec.get("debug_logging", False))
        self.debug_operation_filters = None
        self.debug_max_log_mb = int(model_spec.get("debug_max_log_mb", 64) or 64)
        self.temperature = model_spec.get("temperature", model_config.get("default_temperature"))
        self.temperature_override_enabled = model_spec.get("temperature_override_enabled", True)
        self.thinking_enabled = bool(model_spec.get("thinking_enabled", False))
        self.thinking_level = model_spec.get("thinking_level")
        self.thinking_budget = model_spec.get("thinking_budget", 0)
        self.events: list[dict[str, Any]] = []

    def _post_event(self, name: str, data: dict | None = None):
        self.events.append({"event": name, "data": data or {}})

    def get_debug_operation_context(self):
        return {
            "benchmark": True,
            "provider": self.provider_config.get("display_name"),
            "model": self.model_config.get("id"),
        }


def _slug(value: str, fallback: str = "item") -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    text = text.strip("._")
    return text[:120] or fallback


def _read_text(path: Path) -> str:
    if path.suffix.lower() in {".docx", ".md", ".markdown", ".html", ".htm", ".xhtml", ".pdf"}:
        from gemini_translator.utils.document_importer import extract_document_chapters

        result = extract_document_chapters(path)
        return "\n\n".join(chapter.html for chapter in result.chapters)
    return path.read_text(encoding="utf-8")


def _resolve_path(base_dir: Path, value: str | os.PathLike) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    candidate = (base_dir / path).resolve()
    if candidate.exists():
        return candidate
    return (Path.cwd() / path).resolve()


def _load_text_field(item: dict[str, Any], base_dir: Path, *names: str) -> str:
    for name in names:
        if name in item and item[name] is not None:
            return str(item[name])
        path_name = f"{name}_path"
        if path_name in item and item[path_name]:
            return _read_text(_resolve_path(base_dir, item[path_name]))
    return ""


def _load_prompt_template(prompt_spec: dict[str, Any], base_dir: Path) -> str:
    builtin = str(prompt_spec.get("builtin") or "").strip().lower()
    if builtin == "default":
        return api_config.default_prompt()
    if builtin == "sequential":
        return api_config.default_sequential_prompt()
    template = _load_text_field(prompt_spec, base_dir, "template", "text", "prompt")
    if template:
        return template
    if prompt_spec.get("path"):
        return _read_text(_resolve_path(base_dir, prompt_spec["path"]))
    raise BenchmarkConfigError(f"Prompt '{prompt_spec.get('id', '<unknown>')}' has no template/path/builtin")


def _load_system_instruction(prompt_spec: dict[str, Any], defaults: dict[str, Any], base_dir: Path) -> str:
    text = _load_text_field(prompt_spec, base_dir, "system_instruction")
    if text:
        return text
    if prompt_spec.get("system_instruction_path"):
        return _read_text(_resolve_path(base_dir, prompt_spec["system_instruction_path"]))
    default_text = _load_text_field(defaults, base_dir, "system_instruction")
    if default_text:
        return default_text
    if defaults.get("system_instruction_path"):
        return _read_text(_resolve_path(base_dir, defaults["system_instruction_path"]))
    return ""


def _load_case_source(case_spec: dict[str, Any], base_dir: Path) -> str:
    source = _load_text_field(case_spec, base_dir, "source_html", "source", "text")
    if source:
        return source
    if case_spec.get("source_path"):
        return _read_text(_resolve_path(base_dir, case_spec["source_path"]))
    raise BenchmarkConfigError(f"Case '{case_spec.get('id', '<unknown>')}' has no source/source_path")


def _load_case_reference(case_spec: dict[str, Any], base_dir: Path) -> str:
    if case_spec.get("reference_path"):
        return _read_text(_resolve_path(base_dir, case_spec["reference_path"]))
    return _load_text_field(case_spec, base_dir, "reference", "reference_html")


def _merge_defaults(items: list[dict[str, Any]], defaults: dict[str, Any], section_name: str) -> list[dict[str, Any]]:
    merged = []
    section_defaults = defaults.get(section_name, {})
    for index, item in enumerate(items):
        merged_item = dict(section_defaults)
        merged_item.update(item or {})
        if not merged_item.get("id"):
            merged_item["id"] = f"{section_name}_{index + 1}"
        merged.append(merged_item)
    return merged


def build_prompt_bundle(
    prompt_spec: dict[str, Any],
    case_spec: dict[str, Any],
    *,
    defaults: dict[str, Any],
    base_dir: Path,
) -> PromptBundle:
    template = _load_prompt_template(prompt_spec, base_dir)
    source_html = _load_case_source(case_spec, base_dir)
    glossary = list(case_spec.get("glossary") or defaults.get("glossary") or [])
    system_instruction = _load_system_instruction(prompt_spec, defaults, base_dir)
    use_system_instruction = bool(prompt_spec.get("use_system_instruction", defaults.get("use_system_instruction", True)))
    mode = str(prompt_spec.get("mode") or defaults.get("prompt_mode") or "project").strip().lower()

    context_manager = _BenchmarkContextManager(glossary)
    if mode == "raw":
        glossary_text = context_manager.format_glossary_for_prompt(source_html)
        user_prompt = safe_format(
            template,
            text=source_html,
            glossary=glossary_text,
            format_examples=str(case_spec.get("format_examples") or defaults.get("format_examples") or ""),
            previous_chapter_reference=str(
                case_spec.get("previous_chapter_reference")
                or defaults.get("previous_chapter_reference")
                or ""
            ),
        )
        return PromptBundle(
            user_prompt=user_prompt,
            system_instruction=system_instruction if use_system_instruction else None,
            debug_report="PROMPT_MODE: raw",
        )

    if mode != "project":
        raise BenchmarkConfigError(f"Unsupported prompt mode '{mode}' for prompt '{prompt_spec.get('id')}'")

    builder = PromptBuilder(
        template,
        context_manager,
        use_system_instruction=use_system_instruction,
        sequential_mode=bool(prompt_spec.get("sequential_mode", defaults.get("sequential_mode", False))),
    )
    user_prompt, system_text, debug_report = builder.prepare_for_api(
        source_html,
        system_instruction,
        current_chapters_list=[str(case_spec.get("id") or "benchmark_case")],
    )
    return PromptBundle(user_prompt=user_prompt, system_instruction=system_text, debug_report=debug_report)


def _resolve_provider_and_model(model_spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    provider_id = str(model_spec.get("provider") or "").strip()
    if not provider_id:
        raise BenchmarkConfigError(f"Model '{model_spec.get('id', '<unknown>')}' has no provider")

    provider_config = deepcopy(api_config.ensure_dynamic_provider_models(provider_id) or {})
    if not provider_config:
        raise BenchmarkConfigError(f"Unknown provider '{provider_id}'")

    model_name = str(model_spec.get("model") or model_spec.get("model_name") or "").strip()
    model_id = str(model_spec.get("model_id") or "").strip()
    models = provider_config.get("models", {})
    if model_name and model_name in models:
        model_config = deepcopy(models[model_name])
    elif model_id:
        model_config = {"id": model_id, "needs_chunking": True}
    elif model_name:
        model_config = {"id": model_name, "needs_chunking": True}
    else:
        raise BenchmarkConfigError(f"Model '{model_spec.get('id', '<unknown>')}' has no model/model_id")

    if isinstance(model_spec.get("model_config"), dict):
        model_config.update(deepcopy(model_spec["model_config"]))
    if model_spec.get("base_url"):
        model_config["base_url"] = model_spec["base_url"]
    if model_spec.get("base_timeout"):
        provider_config["base_timeout"] = model_spec["base_timeout"]
    if model_spec.get("provider_config"):
        provider_config.update(deepcopy(model_spec["provider_config"]))

    return provider_config, model_config


def _api_key_for_model(provider_id: str, model_spec: dict[str, Any]) -> str:
    if model_spec.get("api_key"):
        return str(model_spec["api_key"])
    env_name = str(model_spec.get("api_key_env") or DEFAULT_KEY_ENVS.get(provider_id, "")).strip()
    if env_name and os.environ.get(env_name):
        return str(os.environ[env_name])
    keys_by_provider = model_spec.get("_api_keys_by_provider")
    if isinstance(keys_by_provider, dict):
        provider_keys = [
            str(key).strip()
            for key in (keys_by_provider.get(provider_id) or [])
            if str(key).strip()
        ]
        if provider_keys:
            return provider_keys[0]
    if not api_config.provider_requires_api_key(provider_id):
        return api_config.provider_placeholder_api_key(provider_id)
    raise BenchmarkConfigError(
        f"Model '{model_spec.get('id', '<unknown>')}' needs an API key. "
        f"Set api_key_env or {DEFAULT_KEY_ENVS.get(provider_id, '<provider>_API_KEY')}."
    )


def _call_existing_handler(
    model_spec: dict[str, Any],
    prompt: str,
    system_instruction: str | None,
    api_keys_by_provider: dict[str, list[str]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    from gemini_translator.api.base import get_worker_loop
    from gemini_translator.api.factory import get_api_handler_class

    provider_id = str(model_spec.get("provider") or "").strip()
    if api_keys_by_provider:
        model_spec = dict(model_spec)
        model_spec["_api_keys_by_provider"] = api_keys_by_provider
    provider_config, model_config = _resolve_provider_and_model(model_spec)
    api_key = _api_key_for_model(provider_id, model_spec)
    handler_class = get_api_handler_class(provider_config.get("handler_class"))
    worker = _BenchmarkWorker(provider_config, model_config, system_instruction, model_spec)
    handler = handler_class(worker)
    if not handler.setup_client(SimpleNamespace(api_key=api_key), proxy_settings=model_spec.get("proxy")):
        raise BenchmarkConfigError(f"Handler setup failed for provider '{provider_id}'")

    loop = get_worker_loop()
    try:
        result = loop.run_until_complete(
            handler.execute_api_call(
                prompt,
                f"BENCH:{model_spec.get('id') or provider_id}",
                allow_incomplete=bool(model_spec.get("allow_incomplete", False)),
                debug=bool(model_spec.get("debug", False)),
                use_stream=bool(model_spec.get("use_stream", True)),
                max_output_tokens=model_spec.get("max_output_tokens"),
            )
        )
        return result, worker.events
    finally:
        close_session = getattr(handler, "_close_thread_session_internal", None)
        if callable(close_session):
            try:
                loop.run_until_complete(close_session())
            except Exception:
                pass


class BenchmarkRunner:
    def __init__(
        self,
        config_path: str | os.PathLike,
        *,
        output_dir: str | os.PathLike | None = None,
        prompt_only: bool = False,
        save_prompts: bool = False,
        filters: dict[str, set[str]] | None = None,
        limit: int | None = None,
        api_keys_by_provider: dict[str, list[str]] | None = None,
        progress_callback=None,
    ):
        self.config_path = Path(config_path).resolve()
        self.base_dir = self.config_path.parent
        self.config = json.loads(_read_text(self.config_path))
        self.defaults = dict(self.config.get("defaults") or {})
        self.prompt_only = bool(prompt_only)
        self.save_prompts = bool(save_prompts or prompt_only or self.config.get("save_prompts", False))
        self.filters = filters or {}
        self.limit = limit
        self.api_keys_by_provider = api_keys_by_provider or {}
        self.progress_callback = progress_callback
        self.started_at = datetime.now(timezone.utc)

        self.prompts = _merge_defaults(list(self.config.get("prompts") or []), self.defaults, "prompt")
        self.models = _merge_defaults(list(self.config.get("models") or []), self.defaults, "model")
        self.cases = _merge_defaults(list(self.config.get("cases") or []), self.defaults, "case")
        self._validate_nonempty()

        if output_dir:
            self.output_dir = Path(output_dir).resolve()
        else:
            configured_output = self.config.get("output_dir") or "benchmark_results"
            root = Path(configured_output)
            if not root.is_absolute():
                root = Path.cwd() / root
            stamp = self.started_at.strftime("%Y%m%d_%H%M%S")
            self.output_dir = root / f"{_slug(self.config.get('name') or self.config_path.stem)}_{stamp}"

    def _validate_nonempty(self):
        if not self.prompts:
            raise BenchmarkConfigError("Benchmark config has no prompts")
        if not self.models:
            raise BenchmarkConfigError("Benchmark config has no models")
        if not self.cases:
            raise BenchmarkConfigError("Benchmark config has no cases")

    def _passes_filter(self, section: str, item_id: str) -> bool:
        selected = self.filters.get(section)
        return not selected or item_id in selected

    def iter_matrix(self):
        count = 0
        for case_spec in self.cases:
            case_id = str(case_spec.get("id"))
            if not self._passes_filter("cases", case_id):
                continue
            for prompt_spec in self.prompts:
                prompt_id = str(prompt_spec.get("id"))
                if not self._passes_filter("prompts", prompt_id):
                    continue
                for model_spec in self.models:
                    model_id = str(model_spec.get("id"))
                    if not self._passes_filter("models", model_id):
                        continue
                    if self.limit is not None and count >= self.limit:
                        return
                    count += 1
                    yield case_spec, prompt_spec, model_spec

    def list_items(self) -> dict[str, list[str]]:
        return {
            "cases": [str(item.get("id")) for item in self.cases],
            "prompts": [str(item.get("id")) for item in self.prompts],
            "models": [str(item.get("id")) for item in self.models],
        }

    def run(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        prompts_dir = self.output_dir / "prompts"
        if self.save_prompts:
            prompts_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for case_spec, prompt_spec, model_spec in self.iter_matrix():
            self._emit_progress(
                {
                    "event": "start_run",
                    "case_id": str(case_spec.get("id")),
                    "prompt_id": str(prompt_spec.get("id")),
                    "model_id": str(model_spec.get("id")),
                }
            )
            result = self._run_one(case_spec, prompt_spec, model_spec, prompts_dir)
            results.append(result)
            self._emit_progress({"event": "finish_run", **result})

        report = {
            "name": self.config.get("name") or self.config_path.stem,
            "config_path": str(self.config_path),
            "output_dir": str(self.output_dir),
            "created_at": self.started_at.isoformat(),
            "prompt_only": self.prompt_only,
            "results": results,
            "summary": summarize_results(results),
        }
        self._write_outputs(report)
        self._emit_progress(
            {
                "event": "complete",
                "output_dir": str(self.output_dir),
                "runs": len(results),
            }
        )
        return report

    def _emit_progress(self, payload: dict[str, Any]):
        if not callable(self.progress_callback):
            return
        try:
            self.progress_callback(dict(payload))
        except Exception:
            pass

    def _run_one(
        self,
        case_spec: dict[str, Any],
        prompt_spec: dict[str, Any],
        model_spec: dict[str, Any],
        prompts_dir: Path,
    ) -> dict[str, Any]:
        case_id = str(case_spec.get("id"))
        prompt_id = str(prompt_spec.get("id"))
        model_id = str(model_spec.get("id"))
        source_html = _load_case_source(case_spec, self.base_dir)
        reference_text = _load_case_reference(case_spec, self.base_dir)
        glossary = list(case_spec.get("glossary") or self.defaults.get("glossary") or [])

        prompt_bundle = build_prompt_bundle(
            prompt_spec,
            case_spec,
            defaults=self.defaults,
            base_dir=self.base_dir,
        )
        prompt_path = ""
        if self.save_prompts:
            filename = f"{_slug(case_id)}__{_slug(prompt_id)}__{_slug(model_id)}.txt"
            target = prompts_dir / filename
            sections = []
            if prompt_bundle.system_instruction:
                sections.extend(["SYSTEM INSTRUCTION", prompt_bundle.system_instruction, ""])
            sections.extend(["USER PROMPT", prompt_bundle.user_prompt])
            target.write_text("\n".join(sections), encoding="utf-8")
            prompt_path = str(target.relative_to(self.output_dir))

        base_result = {
            "case_id": case_id,
            "prompt_id": prompt_id,
            "model_id": model_id,
            "provider": str(model_spec.get("provider") or ""),
            "model": str(model_spec.get("model") or model_spec.get("model_id") or ""),
            "status": "prompt_only" if self.prompt_only else "pending",
            "latency_ms": None,
            "prompt_tokens_estimate": estimate_tokens(
                (prompt_bundle.system_instruction or "") + "\n" + prompt_bundle.user_prompt
            ),
            "output_tokens_estimate": 0,
            "score": None,
            "metrics": {},
            "issues": [],
            "error": "",
            "prompt_path": prompt_path,
            "debug_report": prompt_bundle.debug_report,
        }

        if self.prompt_only:
            return base_result

        started = time.perf_counter()
        try:
            output_text, events = _call_existing_handler(
                model_spec,
                prompt_bundle.user_prompt,
                prompt_bundle.system_instruction,
                api_keys_by_provider=self.api_keys_by_provider,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            checks = dict(case_spec.get("checks") or {})
            evaluation = evaluate_translation(
                source_html,
                output_text,
                reference_text=reference_text,
                glossary_entries=glossary,
                checks=checks,
            )
            output_filename = f"{_slug(case_id)}__{_slug(prompt_id)}__{_slug(model_id)}.output.txt"
            output_path = self.output_dir / "outputs" / output_filename
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(output_text, encoding="utf-8")
            base_result.update(
                {
                    "status": "ok",
                    "latency_ms": latency_ms,
                    "output_tokens_estimate": estimate_tokens(output_text),
                    "score": evaluation.score,
                    "metrics": evaluation.metrics,
                    "issues": evaluation.issues,
                    "output_path": str(output_path.relative_to(self.output_dir)),
                    "events": events[-20:],
                }
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            base_result.update(
                {
                    "status": "error",
                    "latency_ms": latency_ms,
                    "error": f"{type(exc).__name__}: {exc}",
                    "issues": [f"run failed: {type(exc).__name__}"],
                }
            )
        return base_result

    def _write_outputs(self, report: dict[str, Any]):
        (self.output_dir / "results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_csv(report["results"])
        (self.output_dir / "summary.md").write_text(render_markdown_summary(report), encoding="utf-8")

    def _write_csv(self, results: list[dict[str, Any]]):
        columns = [
            "case_id",
            "prompt_id",
            "model_id",
            "provider",
            "model",
            "status",
            "score",
            "latency_ms",
            "prompt_tokens_estimate",
            "output_tokens_estimate",
            "error",
            "prompt_path",
            "output_path",
        ]
        with (self.output_dir / "results.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for result in results:
                writer.writerow({key: result.get(key, "") for key in columns})


def summarize_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results:
        key = (str(result.get("prompt_id")), str(result.get("model_id")))
        item = grouped.setdefault(
            key,
            {
                "prompt_id": key[0],
                "model_id": key[1],
                "runs": 0,
                "ok": 0,
                "errors": 0,
                "scores": [],
                "latencies": [],
                "prompt_tokens": [],
                "output_tokens": [],
            },
        )
        item["runs"] += 1
        if result.get("status") == "ok":
            item["ok"] += 1
        elif result.get("status") == "error":
            item["errors"] += 1
        if isinstance(result.get("score"), (int, float)):
            item["scores"].append(float(result["score"]))
        if isinstance(result.get("latency_ms"), (int, float)):
            item["latencies"].append(float(result["latency_ms"]))
        item["prompt_tokens"].append(int(result.get("prompt_tokens_estimate") or 0))
        item["output_tokens"].append(int(result.get("output_tokens_estimate") or 0))

    summary = []
    for item in grouped.values():
        scores = item.pop("scores")
        latencies = item.pop("latencies")
        prompt_tokens = item.pop("prompt_tokens")
        output_tokens = item.pop("output_tokens")
        item["avg_score"] = round(sum(scores) / len(scores), 2) if scores else None
        item["avg_latency_ms"] = round(sum(latencies) / len(latencies), 1) if latencies else None
        item["avg_prompt_tokens"] = round(sum(prompt_tokens) / max(1, len(prompt_tokens)), 1)
        item["avg_output_tokens"] = round(sum(output_tokens) / max(1, len(output_tokens)), 1)
        summary.append(item)

    return sorted(
        summary,
        key=lambda item: (
            item["avg_score"] is None,
            -(item["avg_score"] or 0),
            item["errors"],
            item["avg_latency_ms"] or 10**12,
        ),
    )


def render_markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        f"# Benchmark: {report.get('name')}",
        "",
        f"- Created: {report.get('created_at')}",
        f"- Prompt-only: {report.get('prompt_only')}",
        f"- Runs: {len(report.get('results') or [])}",
        "",
        "## Ranking",
        "",
        "| Prompt | Model | Runs | OK | Errors | Avg score | Avg latency ms | Avg input tokens |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report.get("summary") or []:
        lines.append(
            "| {prompt_id} | {model_id} | {runs} | {ok} | {errors} | {avg_score} | {avg_latency_ms} | {avg_prompt_tokens} |".format(
                **item
            )
        )

    failures = [
        result for result in report.get("results") or []
        if result.get("status") == "error" or result.get("issues")
    ]
    if failures:
        lines.extend(["", "## Issues", ""])
        for result in failures[:50]:
            issue_text = "; ".join(result.get("issues") or [])
            if result.get("error"):
                issue_text = (issue_text + "; " if issue_text else "") + result["error"]
            lines.append(
                f"- `{result.get('case_id')}` / `{result.get('prompt_id')}` / `{result.get('model_id')}`: {issue_text}"
            )

    return "\n".join(lines) + "\n"


def run_benchmark(config_path: str | os.PathLike, **kwargs) -> dict[str, Any]:
    return BenchmarkRunner(config_path, **kwargs).run()
