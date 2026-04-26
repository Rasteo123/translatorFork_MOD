# Prompt/model benchmarks

This folder contains JSON configs for comparing prompt variants and model
choices on fixed translation snippets.

Dry prompt build:

```powershell
python -m gemini_translator.scripts.prompt_benchmark benchmarks\prompt_benchmark.example.json --prompt-only
```

Run live models:

```powershell
$env:GEMINI_API_KEY = "..."
python -m gemini_translator.scripts.prompt_benchmark benchmarks\prompt_benchmark.example.json --models gemini-flash-env
```

Each run writes:

- `results.json` with full machine-readable results;
- `results.csv` for spreadsheet comparison;
- `summary.md` with prompt/model ranking and detected issues;
- `prompts/*.txt` when `--prompt-only` or `--save-prompts` is enabled.

Config basics:

- `cases` are fixed source fragments, optional references, glossary entries, and checks.
- `prompts` can use `builtin: "default"` or inline/path templates.
- `models` point to existing provider ids from `config/api_providers.json`.
- `checks.required`, `checks.forbidden`, `checks.placeholders`, and `checks.min_similarity`
  tune deterministic quality gates per case.

Full Russian user guide: [`docs/user-guide/13-prompt-benchmark.md`](../docs/user-guide/13-prompt-benchmark.md).
