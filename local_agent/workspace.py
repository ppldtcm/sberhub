"""Здесь собраны проверки путей для файловых инструментов.

Создайте один Workspace в начале запуска и передавайте его файловым инструментам.
Проверки рассчитаны на обычную работу с локальным проектом. Для изоляции
произвольного кода и защиты от одновременной подмены дерева нужны средства ОС.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import stat


MAX_FILE_BYTES = 1024 * 1024

# Служебные имена внутри workspace, недоступные агенту:
# .env — секреты, .kerman — память между запусками.
_SERVICE_NAMES = {".env", ".kerman"}


class FileToolError(Exception):
    """Передавайте модели код ожидаемой ошибки через свой обработчик результатов."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Workspace:
    root: Path

    def __post_init__(self):
        root = Path(self.root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("workspace: нужен существующий каталог")
        object.__setattr__(self, "root", root)

    @classmethod
    def from_cwd(cls):
        return cls(Path.cwd())

    def resolve(self, path, *, kind="any"):
        if not isinstance(path, str) or not path.strip() or "\x00" in path:
            raise FileToolError("invalid_path")
        raw = Path(path)
        # В Windows C:foo зависит от текущей папки диска C:. Такие пути отклоняем.
        if raw.drive and not raw.is_absolute():
            raise PermissionError("file_access_denied")
        if os.name == "nt":
            parts = raw.parts[1:] if raw.anchor else raw.parts
            # Отклоняем специальные имена Windows и дополнительные потоки NTFS.
            if any(":" in part or Path(part).is_reserved() or
                   (part not in (".", "..") and part.rstrip(" .") != part)
                   for part in parts):
                raise PermissionError("file_access_denied")
        if any(part.casefold() in _SERVICE_NAMES for part in raw.parts):
            raise PermissionError("file_access_denied")
        candidate = raw if raw.is_absolute() else self.root / raw
        try:
            target = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise FileToolError("invalid_path") from error
        if not target.is_relative_to(self.root):
            raise PermissionError("file_access_denied")
        relative = target.relative_to(self.root)
        if any(part.casefold() in _SERVICE_NAMES for part in relative.parts):
            raise PermissionError("file_access_denied")
        try:
            info = target.stat()
        except FileNotFoundError as error:
            raise FileToolError("path_not_found") from error
        if stat.S_ISREG(info.st_mode):
            # У одного файла могут быть имена и внутри workspace, и снаружи.
            # Поэтому файлы с несколькими жёсткими ссылками здесь запрещаем.
            if info.st_nlink > 1:
                raise PermissionError("file_access_denied")
            actual = "file"
        elif stat.S_ISDIR(info.st_mode):
            actual = "directory"
        else:
            raise FileToolError("unsupported_file_type")
        if kind != "any" and actual != kind:
            raise FileToolError("not_a_file" if kind == "file" else "not_a_directory")
        return target

    def read_bytes(self, path):
        target = self.resolve(path, kind="file")
        with target.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            current = self.resolve(path, kind="file")
            if current != target or not os.path.samestat(opened, current.stat()):
                raise FileToolError("file_changed")
            if opened.st_size > MAX_FILE_BYTES:
                raise FileToolError("file_too_large")
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise FileToolError("file_too_large")
        if self.resolve(path, kind="file") != target:
            raise FileToolError("file_changed")
        return target, data

    def read_text(self, path):
        _, data = self.read_bytes(path)
        try:
            text = data.decode("utf-8")
        except UnicodeError as error:
            raise FileToolError("invalid_utf8") from error
        if "\x00" in text:
            raise FileToolError("binary_file")
        return text


def as_workspace(workspace):
    return workspace if isinstance(workspace, Workspace) else Workspace(Path(workspace))


def read_file(path, *, workspace):
    return as_workspace(workspace).read_text(path)