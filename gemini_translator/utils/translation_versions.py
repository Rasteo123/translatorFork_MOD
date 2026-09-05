import os


VALIDATED_SUFFIX = "_validated.html"
IGNORED_VERSION_SUFFIXES = {"filtered"}


def _version_file_score(translated_folder, suffix, rel_path):
    full_path = os.path.join(translated_folder, str(rel_path).replace("/", os.sep))
    try:
        stat_result = os.stat(full_path)
        return (1, stat_result.st_mtime, stat_result.st_size, str(suffix), str(rel_path))
    except OSError:
        return (0, -1.0, -1, str(suffix), str(rel_path))


def select_target_translation_version(versions, translated_folder):
    if not versions:
        return None, False

    validated_rel_path = versions.get(VALIDATED_SUFFIX)
    if validated_rel_path:
        # Файл validated может значиться в карте версий, но быть удалён с диска
        # (вручную, сбоем синхронизации и т.п.). В этом случае нельзя молча
        # считать главу «готовой» — нужно откатиться к другим версиям, как это
        # уже делает sort_translation_versions_for_epub_build.
        validated_score = _version_file_score(translated_folder, VALIDATED_SUFFIX, validated_rel_path)
        if validated_score[0]:
            return validated_rel_path, True

    candidates = [
        (suffix, rel_path)
        for suffix, rel_path in versions.items()
        if suffix != VALIDATED_SUFFIX and suffix not in IGNORED_VERSION_SUFFIXES and rel_path
    ]
    if not candidates:
        return None, False

    return max(candidates, key=lambda item: _version_file_score(translated_folder, item[0], item[1]))[1], False


def sort_translation_versions_for_epub_build(versions, translated_folder):
    if not versions:
        return []

    candidates = []
    for suffix, rel_path in versions.items():
        if suffix in IGNORED_VERSION_SUFFIXES or not rel_path:
            continue
        full_path = os.path.join(translated_folder, str(rel_path).replace("/", os.sep))
        score = _version_file_score(translated_folder, suffix, rel_path)
        candidates.append({
            "suffix": suffix,
            "rel_path": rel_path,
            "filepath": full_path,
            "is_validated": suffix == VALIDATED_SUFFIX,
            "_score": score,
        })

    sorted_candidates = sorted(
        candidates,
        key=lambda item: (
            item["is_validated"] and bool(item["_score"][0]),
            item["_score"],
        ),
        reverse=True,
    )
    for item in sorted_candidates:
        item.pop("_score", None)
    return sorted_candidates


def select_epub_build_translation_version(versions, translated_folder):
    sorted_versions = sort_translation_versions_for_epub_build(versions, translated_folder)
    if not sorted_versions:
        return None
    return sorted_versions[0]["rel_path"]
