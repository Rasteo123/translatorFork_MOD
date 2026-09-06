"""Hash-verified backups and reversal bookkeeping for automatic chapter repairs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from ..utils.io_utils import atomic_write_bytes
from ._common import validate_nonempty_string as _validate_nonempty_string

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_METADATA_VERSION = "translation_qa_repair_store.v1"


class RepairStoreError(RuntimeError):
    """Raised when repair bookkeeping cannot be trusted or completed."""


class ManualEditConflict(RepairStoreError):
    """Raised when reverting would silently discard a later human edit."""


@dataclass(frozen=True, slots=True)
class RepairBackup:
    """One original chapter snapshot captured before its first automatic edit."""

    chapter_id: str
    session_id: str
    path: Path
    chapter_path: Path
    sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class AppliedRepair:
    """One committed structural repair and everything needed to reverse it."""

    patch_id: str
    chapter_id: str
    session_id: str
    chapter_path: Path
    backup_path: Path
    before_sha256: str
    after_sha256: str
    inserted_text: str
    status: str = "applied"


@dataclass(frozen=True, slots=True)
class UndoResult:
    """Outcome of one reversal request, safe to report without further context."""

    status: str
    chapters: tuple[str, ...] = ()
    patches: tuple[str, ...] = ()
    detail: str = ""


class RepairStore:
    """Own the on-disk record of every automatic repair and its reversal.

    Backups are written once per chapter and session, before the first edit, so a
    reversal restores the chapter as the translator produced it. Every restore is
    hash-verified in both directions: the backup must still match what was saved,
    and the chapter must still match what the repair produced. Anything else is a
    human edit and is never overwritten.
    """

    def __init__(self, root: Path | str, session_id: str) -> None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise RepairStoreError("session_id must be a nonempty string")
        if not isinstance(root, (str, os.PathLike)) or not str(root).strip():
            raise RepairStoreError("root must be a filesystem path")
        self.root = Path(root)
        self.session_id = session_id.strip()

    def backup_chapter(self, chapter_id: str, chapter_path: Path | str) -> RepairBackup:
        """Capture the pre-repair bytes once per chapter and session."""
        chapter_id = _identity(chapter_id, "chapter_id")
        path = Path(chapter_path)
        existing = self._load_metadata(chapter_id)
        if existing is not None and existing.get("backup", {}).get("sha256"):
            backup = existing["backup"]
            return RepairBackup(
                chapter_id=chapter_id,
                session_id=str(backup.get("session_id", self.session_id)),
                path=Path(backup["path"]),
                chapter_path=Path(backup.get("chapter_path", path)),
                sha256=str(backup["sha256"]),
                created_at=str(backup.get("created_at", "")),
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RepairStoreError(f"chapter is not readable: {exc}") from exc

        directory = self._chapter_dir(chapter_id)
        directory.mkdir(parents=True, exist_ok=True)
        backup_path = directory / f"{_safe_name(self.session_id)}.html"
        atomic_write_bytes(backup_path, data)
        backup = RepairBackup(
            chapter_id=chapter_id,
            session_id=self.session_id,
            path=backup_path,
            chapter_path=path,
            sha256=content_digest(data),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._save_metadata(
            chapter_id,
            {
                "version": _METADATA_VERSION,
                "chapter_id": chapter_id,
                "backup": {
                    "session_id": backup.session_id,
                    "path": str(backup.path),
                    "chapter_path": str(backup.chapter_path),
                    "sha256": backup.sha256,
                    "created_at": backup.created_at,
                },
                "applied": [],
                "reversals": [],
            },
        )
        return backup

    def record_applied(self, applied: AppliedRepair) -> None:
        """Persist one committed repair so a restart never repeats it."""
        if not isinstance(applied, AppliedRepair):
            raise RepairStoreError("applied must be an AppliedRepair")
        metadata = self._load_metadata(applied.chapter_id)
        if metadata is None:
            raise RepairStoreError("cannot record a repair without a backup")
        entries = list(metadata.get("applied", []))
        if any(entry.get("patch_id") == applied.patch_id for entry in entries):
            return
        entries.append(
            {
                "patch_id": applied.patch_id,
                "session_id": applied.session_id,
                "chapter_path": str(applied.chapter_path),
                "before_sha256": applied.before_sha256,
                "after_sha256": applied.after_sha256,
                "inserted_text": applied.inserted_text,
                "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        metadata["applied"] = entries
        self._save_metadata(applied.chapter_id, metadata)

    def applied_patch_ids(self, chapter_id: str) -> tuple[str, ...]:
        """Return every patch already committed for one chapter."""
        metadata = self._load_metadata(_identity(chapter_id, "chapter_id"))
        if metadata is None:
            return ()
        return tuple(
            str(entry.get("patch_id", ""))
            for entry in metadata.get("applied", [])
            if entry.get("patch_id")
        )

    def repaired_chapter_ids(self, session_id: str | None = None) -> tuple[str, ...]:
        """Return chapters with unreverted repairs, newest applications last."""
        if not self.root.exists():
            return ()
        chapters: list[str] = []
        for directory in sorted(self.root.iterdir()):
            metadata = _read_json(directory / "repairs.json")
            if not isinstance(metadata, dict):
                continue
            applied = metadata.get("applied") or []
            if not applied:
                continue
            if session_id is not None and all(
                entry.get("session_id") != session_id for entry in applied
            ):
                continue
            chapter_id = str(metadata.get("chapter_id", ""))
            if chapter_id:
                chapters.append(chapter_id)
        return tuple(chapters)

    def undo_chapter(self, chapter_id: str) -> UndoResult:
        """Restore one chapter to its pre-repair bytes without calling any model."""
        chapter_id = _identity(chapter_id, "chapter_id")
        metadata = self._load_metadata(chapter_id)
        if metadata is None:
            return UndoResult("nothing_to_undo")
        applied = list(metadata.get("applied", []))
        backup = metadata.get("backup") or {}
        if not applied or not backup.get("sha256"):
            return UndoResult("nothing_to_undo")

        backup_path = Path(backup["path"])
        try:
            backup_data = backup_path.read_bytes()
        except OSError as exc:
            raise RepairStoreError(f"backup is not readable: {exc}") from exc
        if content_digest(backup_data) != backup["sha256"]:
            raise RepairStoreError("backup content does not match its recorded hash")

        chapter_path = Path(applied[-1].get("chapter_path") or backup["chapter_path"])
        try:
            current = chapter_path.read_bytes()
        except OSError as exc:
            raise RepairStoreError(f"chapter is not readable: {exc}") from exc
        if content_digest(current) != applied[-1].get("after_sha256"):
            return UndoResult(
                "manual_edit_conflict",
                chapters=(chapter_id,),
                detail="chapter changed after the last automatic repair",
            )

        atomic_write_bytes(chapter_path, backup_data)
        patches = tuple(str(entry.get("patch_id", "")) for entry in applied)
        reversals = list(metadata.get("reversals", []))
        reversals.append(
            {
                "patches": list(patches),
                "restored_sha256": backup["sha256"],
                "reverted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        metadata["reversals"] = reversals
        metadata["applied"] = []
        self._save_metadata(chapter_id, metadata)
        return UndoResult("restored", chapters=(chapter_id,), patches=patches)

    def undo_session(self, session_id: str) -> UndoResult:
        """Reverse every chapter this session repaired, newest first."""
        session_id = _identity(session_id, "session_id")
        chapters = self.repaired_chapter_ids(session_id)
        if not chapters:
            return UndoResult("nothing_to_undo")
        restored: list[str] = []
        patches: list[str] = []
        conflicts: list[str] = []
        for chapter_id in reversed(chapters):
            result = self.undo_chapter(chapter_id)
            if result.status == "restored":
                restored.append(chapter_id)
                patches.extend(result.patches)
            elif result.status == "manual_edit_conflict":
                conflicts.append(chapter_id)
        if not restored:
            return UndoResult(
                "manual_edit_conflict" if conflicts else "nothing_to_undo",
                chapters=tuple(conflicts),
            )
        return UndoResult(
            "restored",
            chapters=tuple(restored),
            patches=tuple(patches),
            detail=(
                "manual edits kept in: " + ", ".join(conflicts) if conflicts else ""
            ),
        )

    def _chapter_dir(self, chapter_id: str) -> Path:
        """Вернуть каталог бэкапов для chapter_id, устойчивый к коллизиям _safe_name.

        _safe_name() необратимо схлопывает разные chapter_id (например, не-ASCII
        имена файлов внутри EPUB) в одно и то же имя каталога. Если основной
        каталог уже занят метаданными ДРУГОЙ главы, используем каталог с
        детерминированным хеш-суффиксом — так каждая коллизирующая глава получает
        собственный бэкап вместо отказа чинить и потери всего результата QA.
        Не коллизирующие главы (подавляющее большинство) имя каталога не меняют,
        поэтому уже созданные на диске бэкапы остаются читаемыми как прежде.
        """
        chapter_id = _identity(chapter_id, "chapter_id")
        primary = self.root / _safe_name(chapter_id)
        primary_metadata = _read_json(primary / "repairs.json")
        if not isinstance(primary_metadata, dict) or primary_metadata.get("chapter_id") == chapter_id:
            return primary
        suffix = hashlib.sha256(chapter_id.encode("utf-8")).hexdigest()[:8]
        fallback = self.root / f"{_safe_name(chapter_id)[:111]}-{suffix}"
        fallback_metadata = _read_json(fallback / "repairs.json")
        if isinstance(fallback_metadata, dict) and fallback_metadata.get("chapter_id") != chapter_id:
            # Последний рубеж: даже каталог с хеш-суффиксом занят метаданными
            # ТРЕТЬЕЙ главы (двойная коллизия — практически невероятна для
            # настоящего sha256, но не исключена при вырожденных подменах).
            # Дальше подставлять уже некуда — честнее отказать, чем подменить бэкап.
            raise RepairStoreError(
                "repair metadata directory collision could not be resolved for "
                f"chapter_id {chapter_id!r}: both the primary and hash-suffixed "
                "backup directories belong to other chapters"
            )
        return fallback

    def _metadata_path(self, chapter_id: str) -> Path:
        return self._chapter_dir(chapter_id) / "repairs.json"

    def _load_metadata(self, chapter_id: str) -> dict | None:
        chapter_id = _identity(chapter_id, "chapter_id")
        metadata = _read_json(self._metadata_path(chapter_id))
        if metadata is None:
            return None
        if not isinstance(metadata, dict) or metadata.get("version") != _METADATA_VERSION:
            raise RepairStoreError("repair metadata is corrupted or unsupported")
        if metadata.get("chapter_id") != chapter_id:
            # Последний рубеж: даже каталог с хеш-суффиксом из _chapter_dir занят
            # метаданными другой главы (двойная коллизия хешей — практически
            # невероятно). Использовать эти метаданные значило бы подменить
            # чужой бэкап и затем затереть эту главу при undo.
            raise RepairStoreError(
                "repair metadata belongs to a different chapter_id "
                f"({metadata.get('chapter_id')!r} != {chapter_id!r}); "
                "this is a directory-name collision after sanitizing chapter_id"
            )
        return metadata

    def _save_metadata(self, chapter_id: str, metadata: dict) -> None:
        chapter_id = _identity(chapter_id, "chapter_id")
        path = self._metadata_path(chapter_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(
            path,
            json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
        )


def _identity(value: object, field_name: str) -> str:
    try:
        return _validate_nonempty_string(value, field_name)
    except ValueError as exc:
        raise RepairStoreError(str(exc)) from exc


def _safe_name(value: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", value).strip("._") or "unnamed"
    return cleaned[:120]


def content_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> object | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RepairStoreError(f"repair metadata is not readable: {exc}") from exc
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise RepairStoreError("repair metadata is not valid JSON") from exc


