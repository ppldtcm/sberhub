"""Структурированный журнал событий harness в JSON Lines.

Одно событие — одна строка JSON. Ключи, токены и другие секреты не пишутся:
поле `messages`/`tools` сокращается, значения неизвестных полей не выводятся.
"""

import json
import os
import sys
import time


_SECRET_KEYS = {
    "api_key", "apikey", "authorization", "token", "access_token",
    "secret", "password", "cookie",
}

_REDACT_MESSAGE_FIELDS = {"content", "arguments", "reasoning_content"}

_MAX_STR = 200


def _redact(value, *, key=None):
    if key is not None and key.lower() in _SECRET_KEYS:
        return "[секрет скрыт]"
    if isinstance(value, dict):
        return {k: _redact(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[: _MAX_STR - 3] + "..."
    return value


def _redact_messages(messages):
    out = []
    for message in messages or []:
        item = {"role": message.get("role")}
        if "content" in message:
            item["content"] = _redact(message.get("content"))
        if "tool_call_id" in message:
            item["tool_call_id"] = message["tool_call_id"]
        if "tool_calls" in message:
            item["tool_calls"] = [
                {"id": call.get("id"), "name": (call.get("function") or {}).get("name")}
                for call in message["tool_calls"]
            ]
        out.append(item)
    return out


def _redact_tools(tools):
    out = []
    for tool in tools or []:
        function = tool.get("function") or {}
        out.append({
            "type": tool.get("type"),
            "name": function.get("name"),
            "description": _redact(function.get("description")),
        })
    return out


def event(*, run_id, step, stage, status, call_id=None, duration_ms=None,
          error=None, details=None, stream=None):
    """Сформировать и записать одно событие в JSON Lines."""
    if stream is None:
        stream = sys.stderr
    record = {
        "ts": time.time(),
        "run_id": run_id,
        "step": step,
        "stage": stage,
        "status": status,
    }
    if call_id is not None:
        record["call_id"] = call_id
    if duration_ms is not None:
        record["duration_ms"] = round(duration_ms, 3)
    if error is not None:
        record["error"] = _redact(error)
    if details is not None:
        record["details"] = _redact(details)
    line = json.dumps(record, ensure_ascii=False, allow_nan=False)
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        line = line.replace(key, "[секрет скрыт]")
    stream.write(line + "\n")
    stream.flush()
    return record