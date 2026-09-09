#!/usr/bin/env python3
"""Synchronize reviewed Mermaid knowledge fields without importing live secrets.

The live client.json contains generated credentials and provider-binding state
that must never be sourced from Git or printed. This tool copies only the three
reviewed content fields listed in SYNC_PATHS from the tracked template into an
existing Mermaid client.json. It defaults to a read-only dry run; applying an
update requires the tenant service to be stopped and explicitly acknowledged.
"""

from __future__ import annotations

import argparse
import copy
import ctypes
import fcntl
import hashlib
import json
import os
import stat
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPOSITORY_ROOT / "clients" / "mermaid" / "config" / "client.json"
SYNC_PATHS = (
    ("agent_persona", "freeform_notes"),
    ("agent_persona", "unsupported_attachment_handoff"),
    ("faq", "gluten_free"),
)

# The generated inode is deliberately not valid JSON until the exchange receipt
# proves that it displaced the target snapshot used to build the update. This
# makes an interrupted or raced apply fail closed instead of booting a stale
# credential/provider snapshot. The service must therefore be stopped for an
# apply (dry runs remain read-only and need no acknowledgement).
_UNCOMMITTED_STAGE = b"MERMAID_CONFIG_SYNC_INCOMPLETE\n"


def _load_object_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _get_path(document: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = document
    for part in path:
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"missing required source field {'.'.join(path)}")
        current = current[part]
    return current


def _set_path(document: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current: Any = document
    for part in path[:-1]:
        child = current.get(part) if isinstance(current, dict) else None
        if not isinstance(child, dict):
            raise ValueError(f"target parent field {'.'.join(path[:-1])} is not an object")
        current = child
    current[path[-1]] = copy.deepcopy(value)


def merge_reviewed_fields(
    source: dict[str, Any],
    target: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    if source.get("slug") != "mermaid" or target.get("slug") != "mermaid":
        raise ValueError("source and target must both be the Mermaid tenant")
    updated = copy.deepcopy(target)
    changed: list[str] = []
    for path in SYNC_PATHS:
        source_value = _get_path(source, path)
        if path == ("agent_persona", "unsupported_attachment_handoff"):
            if (
                not isinstance(source_value, dict)
                or set(source_value) != {"enabled", "reply"}
                or type(source_value.get("enabled")) is not bool
                or not isinstance(source_value.get("reply"), str)
                or not source_value["reply"].strip()
                or len(source_value["reply"]) > 4096
            ):
                raise ValueError(
                    "source field agent_persona.unsupported_attachment_handoff "
                    "must contain only a boolean enabled and non-empty reply"
                )
        elif not isinstance(source_value, str) or not source_value.strip():
            raise ValueError(f"source field {'.'.join(path)} must be a non-empty string")
        try:
            target_value = _get_path(target, path)
        except ValueError:
            target_value = None
        if target_value != source_value:
            _set_path(updated, path, source_value)
            changed.append(".".join(path))
    return updated, changed


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _no_follow_flag(label: str) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise ValueError(f"safe no-follow {label} access is unavailable")
    return no_follow


def _stable_stat_signature(metadata: os.stat_result) -> tuple[int, ...]:
    """Return metadata that changes on replacement or in-place content edits."""
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_regular_file_no_follow(
    path: Path,
    *,
    label: str,
) -> tuple[bytes, os.stat_result]:
    """Read one regular file without following its final path component."""
    flags = os.O_RDONLY | _no_follow_flag(label)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} must be a regular file, not a symlink") from exc
    try:
        metadata_before = os.fstat(descriptor)
        if not stat.S_ISREG(metadata_before.st_mode):
            raise ValueError(f"{label} must be a regular file, not a symlink")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read()
            metadata_after = os.fstat(stream.fileno())
        if (
            _stable_stat_signature(metadata_before)
            != _stable_stat_signature(metadata_after)
            or len(raw) != metadata_after.st_size
        ):
            raise ValueError(f"{label} changed while it was being read")
        return raw, metadata_after
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@contextmanager
def _canonical_client_json_lock(target_path: Path):
    """Use the exact sidecar lock shared by runtime and Nr3 config writers."""
    lock_path = target_path.with_suffix(target_path.suffix + ".lock")
    flags = os.O_RDWR | os.O_CREAT | _no_follow_flag("client.json lock")
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ValueError("client.json lock must be a regular file, not a symlink") from exc
    locked = False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("client.json lock must be a regular file, not a symlink")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _assert_target_unchanged(
    target_path: Path,
    expected_stat: os.stat_result,
    expected_bytes: bytes,
) -> None:
    """Safely reject non-cooperating replacement or in-place target edits."""
    try:
        current, current_stat = _read_regular_file_no_follow(
            target_path,
            label="target",
        )
    except ValueError as exc:
        raise RuntimeError("target changed during sync; refusing replacement") from exc
    if (
        current_stat.st_dev != expected_stat.st_dev
        or current_stat.st_ino != expected_stat.st_ino
        or current != expected_bytes
    ):
        raise RuntimeError("target changed during sync; refusing replacement")


_EntrySnapshot = tuple[str, int, int, int, int, int, int, int, int, bytes]


def _entry_snapshot_from_metadata(
    kind: str,
    metadata: os.stat_result,
    payload: bytes,
) -> _EntrySnapshot:
    return (
        kind,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        payload,
    )


def _same_entry_across_exchange(
    first: _EntrySnapshot | None,
    second: _EntrySnapshot | None,
) -> bool:
    """Compare one inode across rename, which legitimately advances ctime."""
    if first is None or second is None:
        return False
    return first[:8] == second[:8] and first[9:] == second[9:]


def _capture_entry_snapshot(path: Path) -> _EntrySnapshot | None:
    """Capture one directory entry without following its final symlink.

    The snapshot includes inode identity and contents for regular files. A
    symlink snapshot includes the link text, never the linked file's contents.
    Returning ``None`` means the entry moved while it was being inspected.
    """
    try:
        before = os.lstat(path)
    except OSError:
        return None
    kind = "other"
    payload = b""
    if stat.S_ISREG(before.st_mode):
        try:
            payload, metadata = _read_regular_file_no_follow(
                path,
                label="atomic exchange entry",
            )
        except ValueError:
            return None
        if _stable_stat_signature(metadata) != _stable_stat_signature(before):
            return None
        kind = "regular"
    elif stat.S_ISLNK(before.st_mode):
        try:
            payload = os.fsencode(os.readlink(path))
        except OSError:
            return None
        kind = "symlink"
    try:
        after = os.lstat(path)
    except OSError:
        return None
    if _stable_stat_signature(after) != _stable_stat_signature(before):
        return None
    return _entry_snapshot_from_metadata(kind, after, payload)


class _AtomicExchangePreconditionError(RuntimeError):
    """An exchange input changed before the exchange syscall."""


class _AtomicExchangeReceiptError(RuntimeError):
    """The exchange ran, but its post-syscall receipt could not be captured."""


class _AtomicExchangeNoMutationError(RuntimeError):
    """The platform rejected exchange without changing either directory entry."""


def _exchange_directory_entries(first_path: Path, second_path: Path) -> None:
    """Invoke the platform's atomic directory-entry exchange primitive."""
    libc = ctypes.CDLL(None, use_errno=True)
    first = os.fsencode(first_path)
    second = os.fsencode(second_path)
    try:
        if sys.platform == "linux":
            exchange = libc.renameat2
            exchange.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            exchange.restype = ctypes.c_int
            result = exchange(-100, first, -100, second, 0x2)
        elif sys.platform == "darwin":
            exchange = libc.renamex_np
            exchange.argtypes = (
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            exchange.restype = ctypes.c_int
            result = exchange(first, second, 0x2)
        else:
            raise AttributeError
    except AttributeError as exc:
        raise _AtomicExchangeNoMutationError(
            "safe atomic config exchange is unavailable; target was not changed"
        ) from exc
    if result != 0:
        error_number = ctypes.get_errno()
        raise _AtomicExchangeNoMutationError(
            "safe atomic config exchange failed; target was not changed"
        ) from OSError(error_number, os.strerror(error_number))


def _atomic_exchange(
    first_path: Path,
    second_path: Path,
    *,
    expected_first: _EntrySnapshot | None = None,
    expected_second: _EntrySnapshot | None = None,
) -> dict[str, _EntrySnapshot | bool | None]:
    """Atomically exchange two directory entries without an overwrite window.

    Python does not expose the platform exchange flags. Linux ``renameat2`` and
    macOS ``renamex_np`` do, and both leave the paths untouched on failure. A
    platform/filesystem without this primitive must fail closed rather than
    fall back to check-then-``replace``. The returned before/after receipt lets
    callers prove which entries were exchanged before accepting the result.
    """
    before_first = _capture_entry_snapshot(first_path)
    before_second = _capture_entry_snapshot(second_path)
    if before_first is None or before_second is None:
        raise _AtomicExchangePreconditionError(
            "safe atomic config exchange inputs were unstable; target was not changed"
        )
    if (
        (expected_first is not None and before_first != expected_first)
        or (expected_second is not None and before_second != expected_second)
    ):
        raise _AtomicExchangePreconditionError(
            "safe atomic config exchange precondition failed; target was not changed"
        )
    _exchange_directory_entries(first_path, second_path)
    try:
        after_first = _capture_entry_snapshot(first_path)
        after_second = _capture_entry_snapshot(second_path)
    except BaseException as exc:
        # The exchange has already returned successfully. Distinguish this from
        # a precondition failure so callers preserve the displaced entry.
        raise _AtomicExchangeReceiptError(
            "safe atomic config exchange receipt is unavailable"
        ) from exc
    return {
        "before_first": before_first,
        "before_second": before_second,
        "after_first": after_first,
        "after_second": after_second,
        "coherent": (
            _same_entry_across_exchange(after_first, before_second)
            and _same_entry_across_exchange(after_second, before_first)
        ),
    }


def _write_backup(backup_dir: Path, original: bytes) -> Path:
    if not backup_dir.is_absolute():
        raise ValueError("backup directory must be an absolute path outside the repository")
    backup_dir = backup_dir.resolve()
    repository = REPOSITORY_ROOT.resolve()
    if backup_dir == repository or repository in backup_dir.parents:
        raise ValueError("backup directory must be outside the repository")
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise ValueError("backup directory must be a real directory")
    backup_dir.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_path = backup_dir / f"client.json.before-content-sync-{stamp}"
    descriptor = os.open(
        backup_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        backup_path.unlink(missing_ok=True)
        raise
    _fsync_directory(backup_dir)
    return backup_path


def _overwrite_open_file(descriptor: int, payload: bytes) -> os.stat_result:
    """Replace one already-open regular inode and durably flush its contents."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.ftruncate(descriptor, 0)
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:  # pragma: no cover - defensive OS contract guard
            raise OSError("short write while committing synchronized config")
        remaining = remaining[written:]
    os.fsync(descriptor)
    return os.fstat(descriptor)


def sync(
    source_path: Path,
    target_path: Path,
    backup_dir: Path,
    *,
    apply: bool,
    service_stopped: bool = False,
    expected_sha256: str | None = None,
    merge_function=None,
) -> tuple[list[str], Path | None]:
    """Merge reviewed fields, requiring an offline service for any mutation.

    The service-stopped contract prevents readers and non-cooperating in-place
    writers from observing or changing the deliberately invalid commit inode.
    In-repository config writers additionally serialize on the canonical lock.
    """
    if apply and not service_stopped:
        raise ValueError(
            "apply requires service_stopped=True; stop the tenant service first"
        )
    if os.path.abspath(source_path) == os.path.abspath(target_path):
        raise ValueError("source and target must be different files")
    source_bytes, source_stat = _read_regular_file_no_follow(
        source_path,
        label="source",
    )
    source = _load_object_bytes(source_bytes, "source")

    def synchronize_snapshot() -> tuple[list[str], Path | None]:
        target_bytes, target_stat = _read_regular_file_no_follow(
            target_path,
            label="target",
        )
        if expected_sha256 is not None and hashlib.sha256(target_bytes).hexdigest() != expected_sha256:
            raise ValueError("Protected config CAS mismatch")
        if (
            source_stat.st_dev == target_stat.st_dev
            and source_stat.st_ino == target_stat.st_ino
        ):
            raise ValueError("source and target must be different files")
        if stat.S_IMODE(target_stat.st_mode) != 0o600:
            raise ValueError("target client.json must have mode 0600 before sync")
        target = _load_object_bytes(target_bytes, "target")
        updated, changed = (merge_function or merge_reviewed_fields)(source, target)
        if not apply or not changed:
            return changed, None

        backup_path = _write_backup(backup_dir, target_bytes)
        rendered = (
            json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        stage_path: Path | None = None
        stage_descriptor: int | None = None
        clean_stage_on_error = True
        try:
            stage_descriptor, stage_name = tempfile.mkstemp(
                prefix=f".{target_path.name}.content-sync.",
                dir=target_path.parent,
            )
            stage_path = Path(stage_name)
            # Keep the original descriptor open across the exchange. The staged
            # inode remains intentionally invalid until the receipt proves no
            # non-cooperating writer won the target name before the syscall.
            with os.fdopen(os.dup(stage_descriptor), "wb") as stage:
                stage.write(_UNCOMMITTED_STAGE)
                stage.flush()
                os.fchmod(stage.fileno(), 0o600)
                os.fchown(stage.fileno(), target_stat.st_uid, target_stat.st_gid)
                os.fsync(stage.fileno())

            _fsync_directory(target_path.parent)

            _assert_target_unchanged(target_path, target_stat, target_bytes)
            stage_snapshot = _capture_entry_snapshot(stage_path)
            if stage_snapshot is None:
                raise RuntimeError(
                    "staging file changed during sync; refusing replacement"
                )
            target_snapshot = _entry_snapshot_from_metadata(
                "regular",
                target_stat,
                target_bytes,
            )
            # Preserve-by-default before entering any code path that can invoke
            # the syscall. Even KeyboardInterrupt/SystemExit immediately after
            # a successful exchange must not unlink the stage pathname, which
            # then contains the displaced live configuration.
            clean_stage_on_error = False
            try:
                exchange = _atomic_exchange(
                    stage_path,
                    target_path,
                    expected_first=stage_snapshot,
                    expected_second=target_snapshot,
                )
            except _AtomicExchangePreconditionError as exc:
                clean_stage_on_error = True
                raise RuntimeError(
                    "target changed during sync; refusing replacement"
                ) from exc
            except _AtomicExchangeNoMutationError:
                clean_stage_on_error = True
                raise
            except _AtomicExchangeReceiptError as exc:
                _fsync_directory(target_path.parent)
                raise RuntimeError(
                    "config exchange receipt was unavailable; live target is "
                    "uncommitted and the recovery file was preserved"
                ) from exc

            # From this point the exchange syscall returned successfully. Any
            # later exception must retain the displaced entry for recovery.

            displaced_snapshot = exchange.get("after_first")
            installed_snapshot = exchange.get("after_second")
            exchange_is_stable = bool(exchange.get("coherent")) and (
                displaced_snapshot is not None
                and installed_snapshot is not None
                and _capture_entry_snapshot(stage_path) == displaced_snapshot
                and _capture_entry_snapshot(target_path) == installed_snapshot
            )
            if not exchange_is_stable:
                clean_stage_on_error = False
                _fsync_directory(target_path.parent)
                raise RuntimeError(
                    "target or staging entry changed during sync; live target "
                    "is uncommitted and the recovery file was preserved"
                )

            if (
                _same_entry_across_exchange(
                    displaced_snapshot,
                    target_snapshot,
                )
                and _same_entry_across_exchange(
                    installed_snapshot,
                    stage_snapshot,
                )
            ):
                committed_stat = _overwrite_open_file(stage_descriptor, rendered)
                committed_snapshot = _entry_snapshot_from_metadata(
                    "regular",
                    committed_stat,
                    rendered,
                )
                if _capture_entry_snapshot(target_path) != committed_snapshot:
                    clean_stage_on_error = False
                    _fsync_directory(target_path.parent)
                    raise RuntimeError(
                        "target changed while synchronized config was committed; "
                        "recovery file was preserved"
                    )
                stage_path.unlink()
                stage_path = None
                _fsync_directory(target_path.parent)
                return changed, backup_path

            # Any other stable receipt is evidence of a non-cooperating write in
            # the remaining capture-to-syscall window. The generated live inode
            # is still invalid, so never exchange either entry again: doing so
            # could roll an older provider snapshot over the latest live value.
            clean_stage_on_error = False
            _fsync_directory(target_path.parent)
            raise RuntimeError(
                "target changed during sync; live target is uncommitted and "
                "the recovery file was preserved"
            )
        finally:
            if stage_path is not None and clean_stage_on_error:
                stage_path.unlink(missing_ok=True)
            elif stage_path is not None:
                # Best-effort durability for recovery on asynchronous
                # interruption; never mask the original failure.
                try:
                    _fsync_directory(target_path.parent)
                except OSError:
                    pass
            if stage_descriptor is not None:
                os.close(stage_descriptor)

    if not apply:
        # Dry-run remains filesystem read-only. Its snapshot may become stale,
        # but it cannot overwrite a concurrent writer.
        return synchronize_snapshot()
    with _canonical_client_json_lock(target_path):
        return synchronize_snapshot()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the three reviewed fields; without this flag, perform a dry run",
    )
    parser.add_argument(
        "--service-stopped",
        action="store_true",
        help=(
            "acknowledge that the tenant service is stopped; required with --apply"
        ),
    )
    args = parser.parse_args()

    if args.apply and not args.service_stopped:
        parser.error("--apply requires --service-stopped")
    if args.service_stopped and not args.apply:
        parser.error("--service-stopped is valid only with --apply")

    changed, backup_path = sync(
        args.source,
        args.target,
        args.backup_dir,
        apply=args.apply,
        service_stopped=args.service_stopped,
    )
    mode = "applied" if args.apply else "dry-run"
    fields = ", ".join(changed) if changed else "none"
    print(f"{mode}: changed fields: {fields}")
    if backup_path is not None:
        print(f"protected backup: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
