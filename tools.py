"""Обычные функции, их описания для модели и проверка входных данных."""

import json
import math
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path

from config import CONFIG


_ALL_OPERATIONS = {
    "add": "Сложить два числа a и b.",
    "subtract": "Вычесть b из a.",
    "multiply": "Умножить два числа a и b.",
    "divide": "Разделить a на b. При b=0 инструмент выдаёт ошибку.",
    "read_file": "Прочитать текстовый файл по пути path внутри рабочей папки и вернуть его содержимое.",
}

_PARAMETERS = {
    "add": {
        "properties": {
            "a": {"type": "number", "description": "Первое число"},
            "b": {"type": "number", "description": "Второе число"},
        },
        "required": ["a", "b"],
    },
    "subtract": {
        "properties": {
            "a": {"type": "number", "description": "Первое число"},
            "b": {"type": "number", "description": "Второе число"},
        },
        "required": ["a", "b"],
    },
    "multiply": {
        "properties": {
            "a": {"type": "number", "description": "Первое число"},
            "b": {"type": "number", "description": "Второе число"},
        },
        "required": ["a", "b"],
    },
    "divide": {
        "properties": {
            "a": {"type": "number", "description": "Первое число"},
            "b": {"type": "number", "description": "Второе число"},
        },
        "required": ["a", "b"],
    },
    "read_file": {
        "properties": {
            "path": {"type": "string", "description": "Относительный путь внутри рабочей папки"},
        },
        "required": ["path"],
    },
}


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    return a / b


class ToolError(Exception):
    """Контролируемая ошибка инструмента с машинно-читаемым кодом."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def _workspace_root():
    """Нормализованный workspace из единой конфигурации."""
    return CONFIG.resolved_workspace()


def _normalize_inside_workspace(raw_path):
    """Нормализовать путь и убедиться, что он внутри workspace.

    Возвращает абсолютный Path внутри workspace. Иначе — ToolError.
    Содержимое файла здесь не читается, поэтому ошибка ничего не раскрывает.
    """
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ToolError("invalid_path", "Путь должен быть непустой строкой")

    root = _workspace_root()

    candidate = Path(raw_path)
    # Абсолютные пути не принимаем: путь задаётся относительно workspace.
    if candidate.is_absolute():
        raise ToolError("path_outside_workspace", "Абсолютный путь запрещён")

    # Приводим к нормализованному виду без обращения к файловой системе.
    # resolve() без strict не требует существования файла, но убирает "..".
    try:
        normalized = (root / candidate).resolve(strict=False)
    except OSError:
        raise ToolError("invalid_path", "Не удалось нормализовать путь")

    # Проверка границы: normalized должен быть внутри root.
    try:
        normalized.relative_to(root)
    except ValueError:
        # Наружу не сообщаем, что именно за файл и есть ли он.
        raise ToolError("path_outside_workspace", "Путь вне рабочей папки")
    return normalized


def read_file(path):
    """Прочитать текстовый файл внутри workspace.

    Ошибки не раскрывают содержимое: коды говорят только о причине отказа.
    """
    target = _normalize_inside_workspace(path)

    # Запретные имена/расширения проверяем до открытия файла.
    if target.name in CONFIG.forbidden_names or target.suffix in CONFIG.forbidden_suffixes:
        raise ToolError("forbidden_path", "Чтение этого файла запрещено")

    try:
        resolved = target.resolve(strict=True)
    except FileNotFoundError:
        raise ToolError("file_not_found", "Файл не найден")
    except OSError:
        raise ToolError("file_error", "Не удалось открыть файл")

    # Повторная проверка границы: симлинк мог увести наружу.
    root = _workspace_root()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ToolError("path_outside_workspace", "Путь вне рабочей папки")

    if not resolved.is_file():
        raise ToolError("not_a_file", "Это не файл")

    limit = CONFIG.max_file_bytes
    if limit is not None:
        size = resolved.stat().st_size
        if size > limit:
            raise ToolError("file_too_large", f"Файл больше {limit} байт")

    try:
        return resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolError("not_text", "Файл не является текстом UTF-8")
    except OSError:
        raise ToolError("file_error", "Не удалось прочитать файл")


_FUNCTIONS = {
    "add": add,
    "subtract": subtract,
    "multiply": multiply,
    "divide": divide,
    "read_file": read_file,
}


def allowed_operations():
    """Текущий набор разрешённых операций из конфигурации."""
    return {name: _ALL_OPERATIONS[name] for name in CONFIG.allowed_tools if name in _ALL_OPERATIONS}


def tool_schemas():
    """Схемы для модели, пересчитанные из текущего CONFIG.allowed_tools."""
    schemas = []
    for name, description in allowed_operations().items():
        spec = _PARAMETERS[name]
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": spec["properties"],
                    "required": spec["required"],
                    "additionalProperties": False,
                },
            },
        })
    return schemas


def parse_arguments(name, raw):
    """JSON-строка -> словарь аргументов, проверенный по схеме инструмента."""
    if not isinstance(raw, str):
        raise ToolError("invalid_arguments", "arguments должен быть JSON-строкой")
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        raise ToolError("invalid_json", "arguments не является корректным JSON")
    if not isinstance(args, dict):
        raise ToolError("invalid_arguments", "Нужен объект с аргументами")

    spec = _PARAMETERS[name]
    extra = set(args) - set(spec["properties"])
    if extra:
        raise ToolError("unknown_fields", f"Лишние поля: {', '.join(sorted(extra))}")
    missing = set(spec["required"]) - set(args)
    if missing:
        raise ToolError("missing_fields", f"Не хватает полей: {', '.join(sorted(missing))}")

    for key, value in args.items():
        expected = spec["properties"][key]["type"]
        if expected == "number":
            if type(value) not in (int, float) or isinstance(value, bool):
                raise ToolError("invalid_type", f"Поле {key} должно быть числом")
            if isinstance(value, float) and not math.isfinite(value):
                raise ToolError("non_finite_argument", f"Поле {key} должно быть конечным числом")
        elif expected == "string":
            if not isinstance(value, str) or not value.strip():
                raise ToolError("invalid_type", f"Поле {key} должно быть непустой строкой")
    return args


def _run_with_timeout(func, args, tool_timeout):
    if tool_timeout is None or tool_timeout <= 0:
        return func(**args)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, **args)
    try:
        return future.result(timeout=tool_timeout)
    except FutureTimeout:
        future.cancel()
        return {"ok": False, "error": "tool_timeout"}
    finally:
        executor.shutdown(wait=False)


def dispatch_tool(name, arguments, *, tool_timeout=None, run_id="local", step=0, call_id=None):
    """Единая точка входа: доступ, аргументы, таймаут, выполнение."""
    if not isinstance(name, str) or name not in allowed_operations():
        return {"ok": False, "error": "unknown_tool"}
    try:
        args = parse_arguments(name, arguments)
    except ToolError as error:
        return {"ok": False, "error": error.code}

    if tool_timeout is None:
        tool_timeout = CONFIG.tool_timeout

    try:
        outcome = _run_with_timeout(_FUNCTIONS[name], args, tool_timeout)
    except ZeroDivisionError:
        return {"ok": False, "error": "division_by_zero"}
    except ToolError as error:
        # Контролируемая ошибка инструмента: код, без содержимого файла.
        return {"ok": False, "error": error.code}
    except Exception:
        return {"ok": False, "error": "tool_error"}

    if isinstance(outcome, dict) and outcome.get("ok") is False:
        return outcome
    value = outcome
    if isinstance(value, float) and not math.isfinite(value):
        return {"ok": False, "error": "non_finite_result"}
    return {"ok": True, "value": value}