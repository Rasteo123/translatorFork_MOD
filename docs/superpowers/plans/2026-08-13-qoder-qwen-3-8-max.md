# Qoder Qwen3.8-Max Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `Qwen3.8-Max` to the selectable models of the existing Qoder provider.

**Architecture:** Keep the current static provider catalog and add one model entry beside `Qwen3.7-Max`. The existing `QoderApiHandler` already forwards a configured model ID to `QoderAgentOptions.model`, so no handler change is required.

**Tech Stack:** Python 3, JSON, pytest

**Spec:** `docs/superpowers/specs/2026-08-13-qoder-qwen-3-8-max-design.md`

## Global Constraints

- The display name and model ID are exactly `Qwen3.8-Max`.
- Reuse the current `Qwen3.7-Max` limits: `rpm=30`, `rpd=0`, `context_length=200000`, `max_concurrent_requests=1`, and `max_output_tokens=16384`.
- Keep `needs_chunking=true` and `min_thinking_budget=false`.
- Do not add dynamic model discovery or change `QoderApiHandler`.

---

### Task 1: Register and verify Qwen3.8-Max

**Files:**
- Modify: `tests/test_qoder_handler.py:344`
- Modify: `config/api_providers.json:1367`

**Interfaces:**
- Consumes: `api_config.initialize_configs()` and `api_config.api_providers()["qoder"]`.
- Produces: `provider["models"]["Qwen3.8-Max"]` with ID `Qwen3.8-Max` and the agreed static limits.

- [ ] **Step 1: Add the expected model ID to the existing Qoder configuration test**

In `test_qoder_provider_config_and_factory_registration`, add the new ID to the expected set immediately after `Qwen3.7-Max`:

```python
    assert set(model["id"] for model in provider["models"].values()) == {
        "lite",
        "efficient",
        "auto",
        "performance",
        "ultimate",
        "Qwen3.7-Max",
        "Qwen3.8-Max",
        "Qwen3.7-Plus",
        "DeepSeek-V4-Pro",
        "DeepSeek-V4-Flash",
        "GLM-5.2",
        "Kimi-K2.7-Code",
        "MiniMax-M3",
    }
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_qoder_handler.py::test_qoder_provider_config_and_factory_registration -q
```

Expected: FAIL because the actual Qoder model ID set does not yet contain `Qwen3.8-Max`.

- [ ] **Step 3: Add the minimal provider configuration**

In `config/api_providers.json`, add this entry after `Qwen3.7-Max`:

```json
      "Qwen3.8-Max": {
        "id": "Qwen3.8-Max",
        "rpm": 30,
        "rpd": 0,
        "needs_chunking": true,
        "context_length": 200000,
        "max_concurrent_requests": 1,
        "max_output_tokens": 16384,
        "min_thinking_budget": false
      },
```

- [ ] **Step 4: Run focused and regression checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_qoder_handler.py tests/test_qoder_lazy_sdk_import.py -q
.venv/bin/python -m json.tool config/api_providers.json >/dev/null
git diff --check
```

Expected: all Qoder tests pass, the JSON parser exits with status 0, and `git diff --check` prints nothing.

- [ ] **Step 5: Commit the implementation**

```bash
git add config/api_providers.json tests/test_qoder_handler.py
git commit -m "feat(qoder): add Qwen3.8-Max model"
```
