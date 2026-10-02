"""Базовый синхронный harness: messages -> model -> tools -> messages."""

import json
import time
from dataclasses import dataclass

from .clients import ModelTimeout
from .logging_utils import event as log_event
from .tools import dispatch_tool, tool_schemas


@dataclass
class RunResult:
    reason: str
    answer: str | None
    messages: list


def execute_tool(call, *, config, run_id="local", step=0):
    name = (call.get("function") or {}).get("name")
    arguments = (call.get("function") or {}).get("arguments")
    return dispatch_tool(
        name, arguments,
        config=config,
        run_id=run_id,
        step=step,
        call_id=call.get("id"),
    )


def tool_message(call_id, result):
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(result, ensure_ascii=False, allow_nan=False),
    }


SYSTEM_PROMPT = (
    "Ты учебный помощник с инструментами. "
    "Работай только внутри рабочей папки (workspace), её выбирает пользователь, а не ты. "
    "Для вычислений используй арифметические инструменты. "
    "Чтобы прочитать файл — read_file, чтобы увидеть содержимое папки — list_files. "
    "Создать новый файл — create_file (существующий не перезапишет). "
    "Создать папку, включая вложенные — create_directory. "
    "Перед правкой файла через replace_in_file обязательно прочитай его read_file "
    "и возьми из него ТОЧНЫЙ фрагмент old_text — меняй только то, что попросил пользователь. "
    "Фрагмент old_text должен встречаться в файле ровно один раз. "
    "Если инструмент вернул ошибку, объясни её; не выдумывай результат. "
    "Получив результаты, ответь по-русски."
)


def _load_history(config):
    """Вернуть список сообщений user/assistant из прошлых запусков."""
    limit = getattr(config, "history_turns", 0)
    if not isinstance(limit, int) or limit <= 0:
        return []
    from .history import load_turns
    try:
        return load_turns(config.workspace, limit)
    except Exception as error:
        # Память — вспомогательная функция: не роняем запуск из-за неё.
        log_event(
            run_id="local", step=0, stage="history", status="error",
            error={"type": type(error).__name__},
        )
        return []


def _save_history(config, question, answer):
    limit = getattr(config, "history_turns", 0)
    if not isinstance(limit, int) or limit <= 0 or not answer:
        return
    from .history import append_turn, trim
    try:
        append_turn(config.workspace, question, answer)
        trim(config.workspace, limit)
    except Exception as error:
        log_event(
            run_id="local", step=0, stage="history", status="error",
            error={"type": type(error).__name__},
        )


def run(ask_model, question, config=None):
    """Основной цикл. config — Config (если None, берётся глобальный)."""
    from .config import CONFIG as _GLOBAL
    if config is None:
        config = _GLOBAL

    run_id = "local"

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_load_history(config))
    messages.append({"role": "user", "content": question})

    steps = 0
    while True:
        config.validate()
        max_steps = config.max_steps
        tool_timeout = config.tool_timeout

        if steps >= max_steps:
            log_event(
                run_id=run_id, step=steps, stage="run", status="max_steps",
                details={"max_steps": max_steps, "history_len": len(messages)},
            )
            return RunResult(reason="max_steps", answer=None, messages=messages)
        steps += 1

        try:
            response = ask_model(messages=messages, tools=tool_schemas(config))
        except ModelTimeout as error:
            log_event(
                run_id=run_id, step=steps, stage="run", status="model_timeout",
                error={"type": type(error).__name__},
            )
            return RunResult(reason="model_timeout", answer=None, messages=messages)

        message = response["message"]
        reason = response["finish_reason"]
        if reason not in ("stop", "tool_calls"):
            log_event(
                run_id=run_id, step=steps, stage="response", status="error",
                error={"type": "RuntimeError", "reason": reason},
            )
            raise RuntimeError(f"Ответ модели не завершён: {reason}")

        messages.append(message)
        log_event(
            run_id=run_id, step=steps, stage="history", status="append",
            details={"role": message.get("role"), "history_len": len(messages)},
        )
        calls = message.get("tool_calls") or []

        if not calls:
            if reason != "stop" or not isinstance(message.get("content"), str):
                log_event(
                    run_id=run_id, step=steps, stage="response", status="error",
                    error={"type": "RuntimeError", "reason": "no_text"},
                )
                raise RuntimeError("Модель не вернула ни вызовов, ни финального текста")
            log_event(
                run_id=run_id, step=steps, stage="run", status="stop",
                details={"history_len": len(messages)},
            )
            _save_history(config, question, message["content"])
            return RunResult(reason="stop", answer=message["content"], messages=messages)

        for call in calls:
            call_id = call.get("id")
            name = (call.get("function") or {}).get("name")
            log_event(
                run_id=run_id, step=steps, stage="tool", status="start",
                call_id=call_id, details={"name": name},
            )
            started = time.monotonic()
            outcome = execute_tool(call, config=config, run_id=run_id, step=steps)
            is_error = outcome.get("ok") is False
            error_code = outcome.get("error") if is_error else None
            log_event(
                run_id=run_id, step=steps, stage="tool",
                status="error" if is_error else "ok",
                call_id=call_id,
                duration_ms=(time.monotonic() - started) * 1000.0,
                details={"name": name, "error": error_code},
            )
            result = {"error": error_code} if is_error else outcome["value"]
            messages.append(tool_message(call_id, result))
            log_event(
                run_id=run_id, step=steps, stage="history", status="append",
                call_id=call_id, details={"role": "tool", "history_len": len(messages)},
            )