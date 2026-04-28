import hashlib
import os


WINDOWS_SAFE_MAX_PATH = 240
SAFE_FILENAME_MAX = 240
HASH_LEN = 10


def _path_len(path: str) -> int:
    try:
        return len(os.path.abspath(path))
    except (OSError, TypeError, ValueError):
        return len(str(path))


def _needs_shortening(path: str, filename: str, max_path: int = WINDOWS_SAFE_MAX_PATH) -> bool:
    if len(filename) > SAFE_FILENAME_MAX:
        return True
    if os.name == "nt" and _path_len(path) > max_path:
        return True
    return False


def _shorten_stem(stem: str, suffix: str, max_filename_len: int, digest_source: str) -> str:
    digest = hashlib.sha1(digest_source.encode("utf-8", "surrogatepass")).hexdigest()[:HASH_LEN]
    marker = f"__{digest}"
    max_stem_len = max(1, max_filename_len - len(suffix))

    if len(stem) <= max_stem_len:
        return stem

    keep_len = max(1, max_stem_len - len(marker))
    shortened = stem[:keep_len].rstrip(" ._-")
    if not shortened:
        shortened = stem[:keep_len]
    return f"{shortened}{marker}"


def build_translated_output_path(
    output_folder: str,
    original_internal_path: str,
    file_suffix: str,
    *,
    max_path: int = WINDOWS_SAFE_MAX_PATH,
) -> str:
    """Return a stable translated-file path, shortening long EPUB stems on Windows.

    Some EPUB chapters use very long English titles as filenames. Appending a
    provider suffix can push the final project path past the classic Windows
    MAX_PATH boundary, where Python may raise FileNotFoundError while opening
    the output file. The shortened name preserves the beginning of the chapter
    stem plus a hash, and the project map keeps the full original association.
    """
    normalized_internal_path = str(original_internal_path).replace("\\", "/")
    internal_dir = os.path.dirname(normalized_internal_path)
    chapter_stem = os.path.splitext(os.path.basename(normalized_internal_path))[0]
    destination_dir = (
        os.path.join(output_folder, internal_dir.replace("/", os.sep))
        if internal_dir
        else output_folder
    )

    filename = f"{chapter_stem}{file_suffix}"
    full_path = os.path.join(destination_dir, filename)
    if not _needs_shortening(full_path, filename, max_path=max_path):
        return full_path

    available_by_path = SAFE_FILENAME_MAX
    if os.name == "nt":
        destination_len = _path_len(destination_dir)
        available_by_path = max(1, max_path - destination_len - 1)

    max_filename_len = max(1, min(SAFE_FILENAME_MAX, available_by_path))
    shortened_stem = _shorten_stem(
        chapter_stem,
        file_suffix,
        max_filename_len,
        digest_source=f"{normalized_internal_path}\0{file_suffix}",
    )
    return os.path.join(destination_dir, f"{shortened_stem}{file_suffix}")


def build_translated_relative_path(
    project_folder: str,
    original_internal_path: str,
    file_suffix: str,
    *,
    max_path: int = WINDOWS_SAFE_MAX_PATH,
) -> str:
    full_path = build_translated_output_path(
        project_folder,
        original_internal_path,
        file_suffix,
        max_path=max_path,
    )
    return os.path.relpath(full_path, project_folder).replace("\\", "/")
