import os


VALIDATED_SUFFIX = "_validated.html"


def select_target_translation_version(versions, translated_folder):
    if not versions:
        return None, False

    validated_rel_path = versions.get(VALIDATED_SUFFIX)
    if validated_rel_path:
        return validated_rel_path, True

    candidates = [
        (suffix, rel_path)
        for suffix, rel_path in versions.items()
        if suffix != VALIDATED_SUFFIX and rel_path
    ]
    if not candidates:
        return None, False

    def candidate_score(item):
        suffix, rel_path = item
        full_path = os.path.join(translated_folder, str(rel_path).replace("/", os.sep))
        try:
            stat_result = os.stat(full_path)
            return (1, stat_result.st_mtime, stat_result.st_size, suffix)
        except OSError:
            return (0, -1.0, -1, suffix)

    return max(candidates, key=candidate_score)[1], False
