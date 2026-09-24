"""Единая конфигурация harness: лимиты, таймауты, workspace и список разрешённых инструментов.

Значения читаются через свойства в момент использования, а не кэшируются
при старте. Поэтому изменение `CONFIG.max_steps` (или другого поля) в
середине работы влияет на дальнейшее поведение программы.
"""

import os
from pathlib import Path


ENV_PATH = Path(__file__).resolve().with_name(".env")


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


def _env_tools(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return list(default)
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or list(default)


def _default_workspace():
    return Path(__file__).resolve().parent


class Config:
    """Изменяемая конфигурация. Поля читаются в момент использования."""

    def __init__(
        self,
        *,
        max_steps=8,
        model_timeout=20.0,
        tool_timeout=None,
        watchdog=8,
        allowed_tools=("add", "subtract", "multiply", "divide", "read_file"),
        workspace=None,
        max_file_bytes=64 * 1024,
        forbidden_names=(".env",),
        forbidden_suffixes=(".key", ".pem"),
        log_stream=None,
    ):
        self.max_steps = max_steps
        self.model_timeout = model_timeout
        self.tool_timeout = tool_timeout
        self.watchdog = watchdog
        self.allowed_tools = list(allowed_tools)
        self.workspace = Path(workspace) if workspace is not None else _default_workspace()
        self.max_file_bytes = max_file_bytes
        self.forbidden_names = set(forbidden_names)
        self.forbidden_suffixes = set(forbidden_suffixes)
        self.log_stream = log_stream

    @classmethod
    def from_env(cls, env_path=ENV_PATH):
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False, encoding="utf-8-sig")

        raw_workspace = os.environ.get("HARNESS_WORKSPACE")
        workspace = Path(raw_workspace) if raw_workspace else _default_workspace()

        return cls(
            max_steps=_env_int("HARNESS_MAX_STEPS", 8),
            model_timeout=_env_float("HARNESS_MODEL_TIMEOUT", 20.0),
            tool_timeout=_env_float("HARNESS_TOOL_TIMEOUT", None),
            watchdog=_env_int("HARNESS_WATCHDOG", 8),
            allowed_tools=_env_tools(
                "HARNESS_ALLOWED_TOOLS",
                ("add", "subtract", "multiply", "divide", "read_file"),
            ),
            workspace=workspace,
        )

    def resolved_workspace(self):
        """Нормализованный абсолютный путь workspace. Ошибка, если его нет."""
        path = self.workspace
        if not path.is_absolute():
            path = _default_workspace() / path
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError(f"workspace не является папкой: {resolved}")
        return resolved

    def snapshot(self):
        """Словарь для журнала: текущие значения без секретов."""
        return {
            "max_steps": self.max_steps,
            "model_timeout": self.model_timeout,
            "tool_timeout": self.tool_timeout,
            "watchdog": self.watchdog,
            "allowed_tools": list(self.allowed_tools),
            "workspace": str(self.workspace),
            "max_file_bytes": self.max_file_bytes,
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


# Глобальный объект, который читают harness, clients и tools.
CONFIG = Config.from_env()