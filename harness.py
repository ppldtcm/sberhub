"""Базовый синхронный harness: messages -> model -> tools -> messages."""

import json
import time

from clients import ModelTimeout
from config import CONFIG
from logging_utils import event as log_event
from tools import dispatch_tool, tool_schemas


def execute_tool(call, *, tool_timeout=None, run_id="local", step=0):
    """Проверить доступ по имени и выполнить вызов через dispatch_tool."""
    name = (call.get("function") or {}).get("name")
    arguments = (call.get("function") or {}).get("arguments")
    return dispatch_tool(
        name, arguments,
        tool_timeout=tool_timeout,
        run_id=run_id,
        step=step,
        call_id=call.get("id"),
    )


def tool_message(call_id, result):
    """Связать строковый результат с исходным вызовом модели."""
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(result, ensure_ascii=False, allow_nan=False),
    }


def run(ask_model, question, run_id="local"):
    """Лимиты и таймауты читаются из CONFIG в момент использования.

    Изменение CONFIG.max_steps, CONFIG.tool_timeout, CONFIG.allowed_tools
    или CONFIG.workspace во время работы влияет на дальнейшее поведение.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "Ты учебный помощник. Для вычислений используй инструменты. "
                "Получив результаты, ответь по-русски. Если инструмент вернул "
                "ошибку, объясни её; не выдумывай результат вычисления."
            ),
        },
        {"role": "user", "content": question},
    ]

    steps = 0
    while True:
        CONFIG.validate()
        max_steps = CONFIG.max_steps
        tool_timeout = CONFIG.tool_timeout

        if steps >= max_steps:
            log_event(
                run_id=run_id, step=steps, stage="run", status="max_steps",
                details={"max_steps": max_steps, "history_len": len(messages)},
            )
            return {"finish_reason": "max_steps", "content": None, "messages": messages}
        steps += 1

        try:
            response = ask_model(messages=messages, tools=tool_schemas())
        except ModelTimeout as error:
            log_event(
                run_id=run_id, step=steps, stage="run", status="model_timeout",
                error={"type": type(error).__name__},
            )
            return {"finish_reason": "model_timeout", "content": None, "messages": messages}

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
            return {"finish_reason": "stop", "content": message["content"], "messages": messages}

        for call in calls:
            call_id = call.get("id")
            name = (call.get("function") or {}).get("name")
            log_event(
                run_id=run_id, step=steps, stage="tool", status="start",
                call_id=call_id, details={"name": name},
            )
            started = time.monotonic()
            outcome = execute_tool(call, tool_timeout=tool_timeout, run_id=run_id, step=steps)
            is_error = outcome.get("ok") is False
            error_code = outcome.get("error") if is_error else None
            log_event(
                run_id=run_id, step=steps, stage="tool",
                status="error" if is_error else "ok",
                call_id=call_id,
                duration_ms=(time.monotonic() - started) * 1000.0,
                details={"name": name, "error": error_code},
            )
            # Даже при ошибке tool-сообщение привязано к исходному call_id.
            result = {"error": error_code} if is_error else outcome["value"]
            messages.append(tool_message(call_id, result))
            log_event(
                run_id=run_id, step=steps, stage="history", status="append",
                call_id=call_id, details={"role": "tool", "history_len": len(messages)},
            )