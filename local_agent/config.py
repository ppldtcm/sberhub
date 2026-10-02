"""Единая конфигурация harness: лимиты, таймауты, workspace и список инструментов."""

import os
from pathlib import Path

from .workspace import Workspace


ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _env_float(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_int(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def _env_nonneg_int(name, default):
    """Как _env_int, но допускает 0 (нужно для HISTORY_TURNS)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on", "да"):
        return True
    if value in ("0", "false", "no", "off", "нет"):
        return False
    return default


def _env_tools(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return list(default)
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or list(default)


def _default_workspace():
    """Fallback: корень — папка пакета (используется без CLI, напр. из run.py)."""
    return Workspace(Path(__file__).resolve().parent)


_DEFAULT_TOOLS = (
    "add", "subtract", "multiply", "divide",
    "read_file", "list_files", "replace_in_file",
    "create_file", "create_directory",
)


class Config:
    """Изменяемая конфигурация. Поля читаются в момент использования."""

    def __init__(
        self,
        *,
        max_steps=8,
        model_timeout=20.0,
        tool_timeout=None,
        watchdog=8,
        allowed_tools=_DEFAULT_TOOLS,
        workspace=None,
        max_file_bytes=64 * 1024,
        history_turns=10,
        log_stream=None,
        log_events=True,
    ):
        self.max_steps = max_steps
        self.model_timeout = model_timeout
        self.tool_timeout = tool_timeout
        self.watchdog = watchdog
        self.allowed_tools = list(allowed_tools)
        if workspace is None:
            self.workspace = _default_workspace()
        elif isinstance(workspace, Workspace):
            self.workspace = workspace
        else:
            self.workspace = Workspace(Path(workspace))
        self.max_file_bytes = max_file_bytes
        self.history_turns = history_turns
        self.log_stream = log_stream
        self.log_events = log_events

    @classmethod
    def from_env(cls, env_path=ENV_PATH, workspace_root=None):
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False, encoding="utf-8-sig")

        # По заданию 3-й недели workspace фиксируется из cwd CLI.
        # Переменная HARNESS_WORKSPACE сознательно не читается.
        if workspace_root is not None:
            workspace = Workspace(Path(workspace_root))
        else:
            workspace = None

        return cls(
            max_steps=_env_int("HARNESS_MAX_STEPS", 8),
            model_timeout=_env_float("HARNESS_MODEL_TIMEOUT", 20.0),
            tool_timeout=_env_float("HARNESS_TOOL_TIMEOUT", None),
            watchdog=_env_int("HARNESS_WATCHDOG", 8),
            allowed_tools=_env_tools("HARNESS_ALLOWED_TOOLS", _DEFAULT_TOOLS),
            workspace=workspace,
            history_turns=_env_nonneg_int("HISTORY_TURNS", 10),
            log_events=_env_bool("HARNESS_LOG_EVENTS", True),
        )

    def bind_workspace(self, root):
        """Зафиксировать workspace на весь запуск. Значение из .env не учитывается."""
        path = Path(root)
        if not path.is_absolute():
            path = path.resolve(strict=False)
        if not path.is_dir():
            raise ValueError(f"workspace не является папкой: {path}")
        self.workspace = Workspace(path)

    def resolved_workspace(self):
        """Path до корня workspace."""
        return self.workspace.root

    def snapshot(self):
        return {
            "max_steps": self.max_steps,
            "model_timeout": self.model_timeout,
            "tool_timeout": self.tool_timeout,
            "watchdog": self.watchdog,
            "allowed_tools": list(self.allowed_tools),
            "workspace": str(self.workspace.root),
            "max_file_bytes": self.max_file_bytes,
            "history_turns": self.history_turns,
        }

    def validate(self):
        if type(self.max_steps) is not int or self.max_steps < 1:
            raise ValueError("max_steps должен быть положительным целым")
        if self.model_timeout is not None and self.model_timeout <= 0:
            raise ValueError("model_timeout должен быть положительным или None")
        if self.tool_timeout is not None and self.tool_timeout <= 0:
            raise ValueError("tool_timeout должен быть положительным или None")
        if type(self.watchdog) is not int or self.watchdog < 1:
            raise ValueError("watchdog должен быть положительным целым")
        if not self.allowed_tools:
            raise ValueError("allowed_tools не может быть пустым")
        if self.max_file_bytes is not None and self.max_file_bytes < 0:
            raise ValueError("max_file_bytes не может быть отрицательным")
        if type(self.history_turns) is not int or self.history_turns < 0:
            raise ValueError("history_turns должен быть неотрицательным целым")
        if not isinstance(self.workspace, Workspace):
            raise ValueError("workspace должен быть объектом Workspace")


# Глобальный объект для обратной совместимости с run.py.
CONFIG = Config.from_env()