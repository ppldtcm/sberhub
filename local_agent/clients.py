"""Адаптер настоящего DeepSeek Chat Completions API."""

from copy import deepcopy
import os
from pathlib import Path
import time

from .config import CONFIG
from .logging_utils import event as log_event


DEFAULT_MODEL = "deepseek-flash"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class RequestLimitExceeded(RuntimeError):
    """Внешняя страховка от бесконечных платных запросов при отладке."""


class ModelTimeout(RuntimeError):
    """Модель не ответила за отведённое время. Повторный запрос не делается."""


class DeepSeekModel:
    def __init__(self, model=DEFAULT_MODEL, *, client=None, run_id="local", config=None):
        self.config = config if config is not None else CONFIG

        if client is None:
            from dotenv import load_dotenv

            load_dotenv(ENV_PATH, override=False, encoding="utf-8-sig")
            api_key = os.environ.get("DEEPSEEK_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "Заполните DEEPSEEK_API_KEY в .env рядом с clients.py "
                    "(образец: .env.example)"
                )
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com",
                timeout=self.config.model_timeout,
                max_retries=0,
            )
        self.client = client
        self.model = model
        self.run_id = run_id
        self.calls = 0
        self.last_response = None
        self._step = 0

    def __call__(self, *, messages, tools):
        watchdog = self.config.watchdog
        model_timeout = self.config.model_timeout
        self.config.validate()

        if self.calls >= watchdog:
            log_event(
                run_id=self.run_id, step=self._step, stage="request", status="skipped",
                error={"type": "RequestLimitExceeded", "watchdog": watchdog},
            )
            raise RequestLimitExceeded(
                f"Достигнут клиентский лимит: {watchdog} запросов. Работа не завершена."
            )
        self.calls += 1
        self._step += 1
        step = self._step

        log_event(
            run_id=self.run_id, step=step, stage="request", status="start",
            details={"model": self.model, "watchdog": watchdog, "model_timeout": model_timeout},
        )
        started = time.monotonic()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                stream=False,
                max_tokens=1024,
                timeout=model_timeout,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as error:
            duration_ms = (time.monotonic() - started) * 1000.0
            log_event(
                run_id=self.run_id, step=step, stage="request", status="error",
                duration_ms=duration_ms,
                error={
                    "type": type(error).__name__,
                    "status_code": getattr(error, "status_code", None),
                    "is_timeout": type(error).__name__ == "APITimeoutError",
                },
            )
            if type(error).__name__ == "APITimeoutError":
                raise ModelTimeout(
                    f"Модель не ответила за {model_timeout} с. Повторный запрос не отправляется."
                ) from error
            raise

        choice = response.choices[0]
        message = {"role": "assistant", "content": choice.message.content}
        if choice.message.tool_calls:
            message["tool_calls"] = [
                call.model_dump(exclude_none=True) for call in choice.message.tool_calls
            ]
        reply = {"message": message, "finish_reason": choice.finish_reason}
        self.last_response = deepcopy(reply)

        log_event(
            run_id=self.run_id, step=step, stage="response", status="ok",
            duration_ms=(time.monotonic() - started) * 1000.0,
            details={
                "finish_reason": reply["finish_reason"],
                "tool_calls": len(message.get("tool_calls") or []),
            },
        )
        return reply

    def close(self):
        self.client.close()