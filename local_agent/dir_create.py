"""Создание директории внутри workspace, включая вложенные."""

from pathlib import Path
import os

from .workspace import FileToolError, as_workspace


def create_directory(path, *, workspace):
    if not isinstance(path, str) or not path.strip():
        raise FileToolError("invalid_arguments")
    scope = as_workspace(workspace)

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
    if target == scope.root:
        raise FileToolError("invalid_arguments")

    existed = target.is_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        # На пути есть файл, а не каталог.
        raise FileToolError("path_is_file")
    except NotADirectoryError:
        raise FileToolError("not_a_directory")
    except OSError:
        raise FileToolError("tool_error")

    return {
        "path": target.relative_to(scope.root).as_posix(),
        "created": not existed,
        "already_existed": existed,
    }