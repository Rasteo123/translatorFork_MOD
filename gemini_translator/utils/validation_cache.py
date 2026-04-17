import hashlib
import os


VALIDATION_CACHE_SCHEMA_VERSION = 1

SERIALIZED_RESULT_FIELDS = (
    "combined_deviation",
    "critical_reasons",
    "detected_keys",
    "deviation_type",
    "is_cjk_original",
    "largest_paragraph",
    "len_orig",
    "len_trans",
    "ratio_value",
    "repeat_data",
    "simplification_stats",
    "structural_errors",
    "untranslated_words",
)

TUPLE_RESULT_FIELDS = {"repeat_data", "simplification_stats"}


def build_text_hash(text):
    if text is None:
        text = ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_file_fingerprint(path):
    if not path or not os.path.exists(path):
        return {}

    stat = os.stat(path)
    return {
        "size": int(stat.st_size),
        "mtime_ns": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
    }


def build_detector_signature(word_exceptions):
    normalized_words = sorted(
        {
            str(word).strip().lower()
            for word in (word_exceptions or [])
            if str(word).strip()
        }
    )
    return build_text_hash("\n".join(normalized_words))


def serialize_result_data(result_data):
    payload = {}
    for field in SERIALIZED_RESULT_FIELDS:
        if field not in result_data:
            continue

        value = result_data[field]
        if field == "detected_keys":
            if isinstance(value, set):
                value = sorted(value)
            elif isinstance(value, (list, tuple)):
                value = sorted(value)
            else:
                value = []
        elif field in TUPLE_RESULT_FIELDS and isinstance(value, tuple):
            value = list(value)

        payload[field] = value

    return payload


def restore_result_data(serialized_result):
    restored = {}
    for key, value in (serialized_result or {}).items():
        if key == "detected_keys":
            restored[key] = set(value or [])
        elif key in TUPLE_RESULT_FIELDS and isinstance(value, list):
            restored[key] = tuple(value)
        else:
            restored[key] = value

    return restored


def build_snapshot_entry(result_data, content_hash, relative_path=None):
    entry = {
        "content_hash": content_hash,
        "result": serialize_result_data(result_data),
    }
    if relative_path:
        entry["relative_path"] = relative_path.replace("\\", "/")
    return entry


def build_snapshot_payload(original_epub_fingerprint, detector_signature, chapters):
    return {
        "schema_version": VALIDATION_CACHE_SCHEMA_VERSION,
        "source": {
            "original_epub": original_epub_fingerprint or {},
            "detector_signature": detector_signature or "",
        },
        "chapters": chapters or {},
    }


def is_snapshot_compatible(snapshot_payload, original_epub_fingerprint, detector_signature):
    if not isinstance(snapshot_payload, dict):
        return False

    if snapshot_payload.get("schema_version") != VALIDATION_CACHE_SCHEMA_VERSION:
        return False

    source = snapshot_payload.get("source") or {}
    cached_epub = source.get("original_epub") or {}
    cached_signature = source.get("detector_signature") or ""

    return (
        cached_epub == (original_epub_fingerprint or {})
        and cached_signature == (detector_signature or "")
    )
