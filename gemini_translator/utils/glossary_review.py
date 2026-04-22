import re
import unicodedata


TRANSLATION_WRAPPER_PAIRS = {
    "[": "]",
    "(": ")",
    "{": "}",
    '"': '"',
    "'": "'",
    "\u00ab": "\u00bb",
    "\u201c": "\u201d",
    "\u201e": "\u201c",
    "\u2018": "\u2019",
    "\u201a": "\u2019",
}


def normalized_case_key(text: str) -> str:
    return unicodedata.normalize("NFC", str(text or ""))


def strip_translation_wrappers(text: str) -> str:
    stripped = str(text or "").strip()
    while len(stripped) >= 2:
        expected_closer = TRANSLATION_WRAPPER_PAIRS.get(stripped[0])
        if expected_closer != stripped[-1]:
            break
        inner = stripped[1:-1].strip()
        if not inner:
            break
        stripped = inner
    return stripped


def normalize_translation_review_key(text: str) -> str:
    normalized = normalized_case_key(text).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = strip_translation_wrappers(normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.casefold().replace("\u0451", "\u0435")


def classify_translation_review_change(old_values, new_value: str):
    new_value = str(new_value or "").strip()
    normalized_new = normalize_translation_review_key(new_value)
    exact_difference = False
    semantic_difference = False

    for old_value in old_values:
        old_value = str(old_value or "").strip()
        if old_value != new_value:
            exact_difference = True
        if normalize_translation_review_key(old_value) != normalized_new:
            semantic_difference = True

    cosmetic_only_change = exact_difference and not semantic_difference
    meaningful_change = semantic_difference
    return meaningful_change, cosmetic_only_change
