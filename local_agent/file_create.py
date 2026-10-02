"""Создание нового файла внутри workspace без перезаписи существующего."""

import os
from pathlib import Path
import stat
import tempfile

from .workspace import FileToolError, MAX_FILE_BYTES, as_workspace
from .file_edit import WriteGuard


def create_file(path, content, *, workspace, guard=None):
    if (not isinstance(path, str) or not path.strip()
            or not isinstance(content, str)):
        raise FileToolError("invalid_arguments")
    if "\x00" in content:
        raise FileToolError("binary_file")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise FileToolError("file_too_large")

    scope = as_workspace(workspace)
    guard = guard or WriteGuard()
    guard.check()

    # resolve(...) бросит path_not_found, если файла ещё нет.
    # Нам нужен именно «ещё не существует», поэтому проверяем родителя
    # и целевое имя отдельно.
    target = _resolve_new(scope, path)
    parent = target.parent
    if not parent.is_dir():
        raise FileToolError("not_a_directory")

    # Двойная проверка на гонку: файл мог появиться между resolve и replace.
    if target.exists():
        raise FileToolError("file_already_exists")

    guard.check()
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".kerman-", dir=parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
        os.chmod(temporary, 0o644)

        def commit():
            # Ещё раз: не перезаписываем существующий файл.
            if target.exists():
                raise FileToolError("file_already_exists")
            guard.check()
            # os.link + unlink = атомарное «создать, только если нет».
            # os.replace перезапишет — это нам не подходит.
            os.link(temporary, target)
            temporary.unlink()

        return guard.commit(commit, {
            "path": target.relative_to(scope.root).as_posix(),
            "created": True,
            "bytes": len(encoded),
        })
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _resolve_new(scope, path):
    """Разрешить путь, которого ещё нет, оставшись внутри workspace."""
    raw = Path(path)
    if raw.drive and not raw.is_absolute():
        raise PermissionError("file_access_denied")
    if os.name == "nt":
        parts = raw.parts[1:] if raw.anchor else raw.parts
        if any(":" in part or Path(part).is_reserved() or
               (part not in (".", "..") and part.rstrip(" .") != part)
               for part in parts):
            raise PermissionError("file_access_denied")
    if any(part.casefold() == ".env" for part in raw.parts):
        raise PermissionError("file_access_denied")
    candidate = raw if raw.is_absolute() else scope.root / raw
    target = candidate.resolve(strict=False)
    if not target.is_relative_to(scope.root):
        raise PermissionError("file_access_denied")
    if any(part.casefold() == ".env" for part in target.relative_to(scope.root).parts):
        raise PermissionError("file_access_denied")
    return target