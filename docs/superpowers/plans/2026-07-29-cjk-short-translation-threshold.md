# CJK Short Translation Threshold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise the short-translation threshold for CJK originals from `1.80` to `2.80` in both the manual error validator and automatic retry workflow.

**Architecture:** Keep the existing CJK detection and preset structure. Change the shared automatic CJK floor and the manual CJK preset together, while making the manual lower boundary inclusive so only ratios strictly below `2.80` are rejected.

**Tech Stack:** Python 3, unittest/pytest, PyQt validator logic.

---

### Task 1: Manual validator threshold

**Files:**
- Modify: `tests/test_validation_reanalysis.py`
- Modify: `gemini_translator/ui/dialogs/validation.py:2009-2014`
- Modify: `gemini_translator/ui/dialogs/validation.py:3639-3647`

- [ ] **Step 1: Write failing tests for the CJK preset and lower boundary**

Add `TranslationValidatorPage` to the existing validation import and add these tests to
`ValidationReanalysisTests`:

```python
from gemini_translator.ui.dialogs.validation import (
    TranslationValidatorDialog,
    TranslationValidatorPage,
)


def test_cjk_ratio_preset_uses_2_80_minimum(self):
    ratio_min, ratio_max, description = TranslationValidatorPage.RATIO_PRESETS[
        "Иероглифический (象 -> A)"
    ]

    self.assertEqual(ratio_min, 2.80)
    self.assertEqual(ratio_max, 6.50)
    self.assertIn("x2.8", description)


def test_ratio_equal_to_lower_bound_is_not_too_short(self):
    harness = _ValidationHarness()

    reasons, status = harness._calculate_status_for_data(
        {
            "has_cached_analysis": True,
            "len_orig": 1000,
            "len_trans": 2800,
            "ratio_value": 2.80,
        },
        override_bounds=(2.80, 6.50),
    )

    self.assertEqual(reasons, [])
    self.assertEqual(status, "neutral")
```

- [ ] **Step 2: Run the manual-validator tests and verify RED**

Run:

```bash
python -m pytest tests/test_validation_reanalysis.py \
  -k "cjk_ratio_preset_uses_2_80_minimum or ratio_equal_to_lower_bound_is_not_too_short" -v
```

Expected: both tests fail—one sees `1.80`, and the other reports
`Длина T/O (2.80x)`.

- [ ] **Step 3: Apply the minimal manual-validator change**

Change the preset and its description:

```python
"Иероглифический (象 -> A)": (
    2.80,
    6.50,
    "Ожидаемое перевод/оригинал для Zh/Jp/Ko -> Ru; если меньше x2.8, это уже подозрительно",
),
```

Make the static lower boundary inclusive:

```python
if not (ratio_min <= val < ratio_max):
    current_reasons.append(f"Длина T/O ({val:.2f}x)")
```

- [ ] **Step 4: Run the manual-validator tests and verify GREEN**

Run:

```bash
python -m pytest tests/test_validation_reanalysis.py \
  -k "cjk_ratio_preset_uses_2_80_minimum or ratio_equal_to_lower_bound_is_not_too_short" -v
```

Expected: `2 passed`.

### Task 2: Automatic retry threshold

**Files:**
- Modify: `tests/test_auto_workflow_helpers.py`
- Modify: `tests/test_auto_workflow_followup.py`
- Modify: `gemini_translator/core/auto_workflow_helpers.py:8`

- [ ] **Step 1: Change automatic CJK expectations to `2.80`**

In `tests/test_auto_workflow_helpers.py`, change the CJK assertion to:

```python
assert ratio_limit == 2.80
```

In the three CJK-limit tests in `tests/test_auto_workflow_followup.py`, change each
expectation to:

```python
self.assertEqual(ratio_limit, 2.80)
```

- [ ] **Step 2: Run the automatic-threshold tests and verify RED**

Run:

```bash
python -m pytest tests/test_auto_workflow_helpers.py \
  tests/test_auto_workflow_followup.py -k "auto_short_ratio" -v
```

Expected: four CJK tests fail because the implementation still returns `1.80`;
the alphabetic test passes.

- [ ] **Step 3: Raise the automatic CJK floor**

Change the constant in `gemini_translator/core/auto_workflow_helpers.py`:

```python
AUTO_CJK_SHORT_RATIO_LIMIT = 2.80
```

Keep `max(base_limit, AUTO_CJK_SHORT_RATIO_LIMIT)` unchanged so a user-selected
limit above `2.80` still wins.

- [ ] **Step 4: Run the automatic-threshold tests and verify GREEN**

Run:

```bash
python -m pytest tests/test_auto_workflow_helpers.py \
  tests/test_auto_workflow_followup.py -k "auto_short_ratio" -v
```

Expected: all selected tests pass.

### Task 3: Regression verification and commit

**Files:**
- Verify: `gemini_translator/ui/dialogs/validation.py`
- Verify: `gemini_translator/core/auto_workflow_helpers.py`
- Verify: `tests/test_validation_reanalysis.py`
- Verify: `tests/test_auto_workflow_helpers.py`
- Verify: `tests/test_auto_workflow_followup.py`

- [ ] **Step 1: Run all directly affected test modules**

Run:

```bash
python -m pytest tests/test_validation_reanalysis.py \
  tests/test_auto_workflow_helpers.py \
  tests/test_auto_workflow_followup.py -v
```

Expected: all tests pass with no errors.

- [ ] **Step 2: Run the complete test suite**

Run:

```bash
python -m pytest
```

Expected: the suite exits with status `0`.

- [ ] **Step 3: Check the patch**

Run:

```bash
git diff --check
git diff -- gemini_translator/ui/dialogs/validation.py \
  gemini_translator/core/auto_workflow_helpers.py \
  tests/test_validation_reanalysis.py \
  tests/test_auto_workflow_helpers.py \
  tests/test_auto_workflow_followup.py
```

Expected: no whitespace errors; the diff contains only the threshold, boundary,
description, and regression-test changes.

- [ ] **Step 4: Commit only the task files**

```bash
git add gemini_translator/ui/dialogs/validation.py \
  gemini_translator/core/auto_workflow_helpers.py \
  tests/test_validation_reanalysis.py \
  tests/test_auto_workflow_helpers.py \
  tests/test_auto_workflow_followup.py
git commit -m "Raise CJK short translation threshold"
```
