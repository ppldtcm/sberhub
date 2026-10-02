"""История диалога между запусками: JSONL в <workspace>/.kerman/history.jsonl."""

import json
from pathlib import Path
import os
import tempfile

from .workspace import Workspace


HISTORY_DIR = ".kerman"
HISTORY_FILE = "history.jsonl"


def _path(workspace: Workspace) -> Path:
    return workspace.root / HISTORY_DIR / HISTORY_FILE


def load_turns(workspace: Workspace, limit: int) -> list[dict]:
    """Вернуть последние `limit` пар (user, assistant) в хронологическом порядке."""
    path = _path(workspace)
    if not path.is_file():
        return []
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # Битый хвост после падения — пропускаем, не роняем запуск.
                continue
            if record.get("role") in ("user", "assistant") and isinstance(record.get("content"), str):
                records.append(record)
    return records[-2 * limit:] if limit > 0 else []


def append_turn(workspace: Workspace, question: str, answer: str) -> None:
    """Дописать пару вопрос-ответ. Создаёт .kerman/ при необходимости."""
    path = _path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Append-only: две строки, каждая — валидный JSON.
    with path.open("a", encoding="utf-8") as stream:
        for role, content in (("user", question), ("assistant", answer)):
            stream.write(json.dumps(
                {"role": role, "content": content},
                ensure_ascii=False,
            ) + "\n")


def trim(workspace: Workspace, limit: int) -> None:
    """Оставить в файле только последние limit пар. Атомарно."""
    if limit <= 0:
        return
    path = _path(workspace)
    if not path.is_file():
        return
    records = load_turns(workspace, limit)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, path)