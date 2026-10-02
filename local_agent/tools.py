"""Обычные функции, их описания для модели и проверка входных данных."""

import json
import math
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from .config import CONFIG
from .workspace import FileToolError
from .file_browser import list_files as _list_files_impl
from .file_edit import replace_in_file as _replace_in_file_impl, WriteGuard
from .file_create import create_file as _create_file_impl
from .dir_create import create_directory as _create_directory_impl


_ALL_OPERATIONS = {
    "add": "Сложить два числа a и b.",
    "subtract": "Вычесть b из a.",
    "multiply": "Умножить два числа a и b.",
    "divide": "Разделить a на b. При b=0 инструмент выдаёт ошибку.",
    "read_file": "Прочитать текстовый файл по пути path внутри рабочей папки и вернуть его содержимое.",
    "list_files": (
        "Показать содержимое папки внутри рабочей папки. "
        "path — относительный путь (по умолчанию текущая папка). "
        "recursive=True — обходить подпапки. limit — максимум элементов (1..2000)."
    ),
    "replace_in_file": (
        "Заменить ровно одно вхождение old_text на new_text в текстовом файле внутри рабочей папки. "
        "Перед вызовом прочитай файл через read_file и возьми точный фрагмент. "
        "Если old_text встречается 0 или ≥2 раз — инструмент откажет и файл не изменится. "
        "Возвращает diff и признак changed."
    ),
    "create_file": (
        "Создать новый текстовый файл внутри рабочей папки. "
        "Существующий файл не перезаписывает — вернёт ошибку file_already_exists. "
        "Родительская папка должна существовать, иначе not_a_directory "
        "(создать папку — через create_directory)."
    ),
    "create_directory": (
        "Создать директорию внутри рабочей папки, включая вложенные. "
        "Если каталог уже существует — вернёт already_existed=True и не упадёт. "
        "Если на пути лежит файл — вернёт path_is_file."
    ),
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
    "list_files": {
        "properties": {
            "path": {"type": "string", "description": "Относительный путь внутри рабочей папки"},
            "recursive": {"type": "boolean", "description": "Обходить ли подпапки"},
            "limit": {"type": "integer", "description": "Максимум элементов (1..2000)"},
        },
        "required": [],
    },
    "replace_in_file": {
        "properties": {
            "path": {"type": "string", "description": "Относительный путь к файлу внутри рабочей папки"},
            "old_text": {"type": "string", "description": "Точный фрагмент, который надо заменить (ровно одно вхождение)"},
            "new_text": {"type": "string", "description": "Новый текст на месте old_text"},
        },
        "required": ["path", "old_text", "new_text"],
    },
    "create_file": {
        "properties": {
            "path": {"type": "string", "description": "Относительный путь нового файла внутри рабочей папки"},
            "content": {"type": "string", "description": "Содержимое файла (UTF-8)"},
        },
        "required": ["path", "content"],
    },
    "create_directory": {
        "properties": {
            "path": {"type": "string", "description": "Относительный путь каталога внутри рабочей папки"},
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


def read_file(path, *, workspace):
    """Прочитать текстовый файл внутри workspace."""
    return workspace.read_text(path)


_FUNCTIONS = {
    "add": add,
    "subtract": subtract,
    "multiply": multiply,
    "divide": divide,
    # read_file / list_files / replace_in_file / create_file / create_directory
    # обрабатываются отдельно — им нужен workspace (и guard для write-инструментов).
}


def allowed_operations(config=None):
    cfg = config if config is not None else CONFIG
    return {name: _ALL_OPERATIONS[name] for name in cfg.allowed_tools if name in _ALL_OPERATIONS}


def tool_schemas(config=None):
    """Схемы для модели, пересчитанные из текущего config.allowed_tools."""
    schemas = []
    for name, description in allowed_operations(config).items():
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
        elif expected == "integer":
            if type(value) is not int or isinstance(value, bool):
                raise ToolError("invalid_type", f"Поле {key} должно быть целым числом")
        elif expected == "boolean":
            if type(value) is not bool:
                raise ToolError("invalid_type", f"Поле {key} должно быть булевым")
        elif expected == "string":
            if not isinstance(value, str):
                raise ToolError("invalid_type", f"Поле {key} должно быть строкой")
    return args


def _run_with_timeout(func, kwargs, tool_timeout):
    if tool_timeout is None or tool_timeout <= 0:
        return func(**kwargs)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, **kwargs)
    try:
        return future.result(timeout=tool_timeout)
    except FutureTimeout:
        future.cancel()
        return {"ok": False, "error": "tool_timeout"}
    finally:
        executor.shutdown(wait=False)


def _run_write_with_timeout(func, kwargs, tool_timeout):
    """Обёртка для write-инструментов: связывает WriteGuard с таймаутом.

    При таймауте вызываем guard.cancel(): это (а) помечает guard отменённым,
    (б) возвращает результат уже завершённой записи, если она успела случиться
    до отмены. Тогда мы честно сообщаем модели, что файл мог измениться.
    """
    guard = WriteGuard(seconds=tool_timeout)
    kwargs = dict(kwargs, guard=guard)
    if tool_timeout is None or tool_timeout <= 0:
        return func(**kwargs)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, **kwargs)
    try:
        return future.result(timeout=tool_timeout)
    except FutureTimeout:
        late_result = guard.cancel()
        return {
            "ok": False,
            "error": "tool_timeout",
            "write_completed": late_result is not None,
        }
    finally:
        executor.shutdown(wait=False)


def dispatch_tool(name, arguments, *, tool_timeout=None, run_id="local",
                  step=0, call_id=None, config=None):
    """Единая точка входа: доступ, аргументы, таймаут, выполнение."""
    cfg = config if config is not None else CONFIG

    if not isinstance(name, str) or name not in allowed_operations(cfg):
        return {"ok": False, "error": "unknown_tool"}
    try:
        args = parse_arguments(name, arguments)
    except ToolError as error:
        return {"ok": False, "error": error.code}

    if tool_timeout is None:
        tool_timeout = cfg.tool_timeout

    try:
        if name == "read_file":
            value = _run_with_timeout(
                read_file,
                {"path": args["path"], "workspace": cfg.workspace},
                tool_timeout,
            )
        elif name == "list_files":
            kwargs = {
                "path": args.get("path") or ".",
                "recursive": args.get("recursive", False),
                "limit": args.get("limit", 200),
                "workspace": cfg.workspace,
            }
            value = _run_with_timeout(_list_files_impl, kwargs, tool_timeout)
        elif name == "replace_in_file":
            kwargs = {
                "path": args["path"],
                "old_text": args["old_text"],
                "new_text": args["new_text"],
                "workspace": cfg.workspace,
            }
            value = _run_write_with_timeout(_replace_in_file_impl, kwargs, tool_timeout)
            # WriteGuard сам возвращает dict с ok=False при tool_timeout.
            if isinstance(value, dict) and value.get("ok") is False:
                return value
        elif name == "create_file":
            kwargs = {
                "path": args["path"],
                "content": args["content"],
                "workspace": cfg.workspace,
            }
            value = _run_write_with_timeout(_create_file_impl, kwargs, tool_timeout)
            if isinstance(value, dict) and value.get("ok") is False:
                return value
        elif name == "create_directory":
            kwargs = {
                "path": args["path"],
                "workspace": cfg.workspace,
            }
            value = _run_with_timeout(_create_directory_impl, kwargs, tool_timeout)
        else:
            value = _run_with_timeout(_FUNCTIONS[name], args, tool_timeout)
    except ZeroDivisionError:
        return {"ok": False, "error": "division_by_zero"}
    except ToolError as error:
        return {"ok": False, "error": error.code}
    except FileToolError as error:
        return {"ok": False, "error": error.code}
    except PermissionError:
        return {"ok": False, "error": "file_access_denied"}
    except Exception:
        return {"ok": False, "error": "tool_error"}

    if isinstance(value, dict) and value.get("ok") is False:
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return {"ok": False, "error": "non_finite_result"}
    return {"ok": True, "value": value}