"""Один запрос пользователя к harness с настоящим DeepSeek Flash."""

import argparse
import sys
import uuid

from clients import DeepSeekModel
from config import CONFIG
from diagnostics import describe_error
import harness
from logging_utils import event as log_event


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        usage='%(prog)s "запрос пользователя" [опции]',
        add_help=False,
    )
    parser.add_argument("question", nargs="?", help="Запрос пользователя в кавычках")
    parser.add_argument("--max-steps", type=int, default=None, help="Лимит шагов (по умолчанию из CONFIG)")
    parser.add_argument("--model-timeout", type=float, default=None, help="Таймаут запроса к модели, с")
    parser.add_argument("--tool-timeout", type=float, default=None, help="Таймаут выполнения инструмента, с")
    parser.add_argument("--watchdog", type=int, default=None, help="Жёсткий лимит запросов к API")
    parser.add_argument("--tools", default=None, help="Разрешённые инструменты через запятую")
    parser.add_argument("--workspace", default=None, help="Рабочая папка для read_file")
    parser.add_argument("-h", "--help", action="help", help="Показать справку")
    args = parser.parse_args(argv)

    if not args.question or not args.question.strip():
        print(
            'Ошибка: передайте непустой запрос, например: python run.py "Сложи 18 и 24"',
            file=sys.stderr,
        )
        return 2

    # Значения из CLI попадают в единую конфигурацию.
    if args.max_steps is not None:
        CONFIG.max_steps = args.max_steps
    if args.model_timeout is not None:
        CONFIG.model_timeout = args.model_timeout
    if args.tool_timeout is not None:
        CONFIG.tool_timeout = args.tool_timeout
    if args.watchdog is not None:
        CONFIG.watchdog = args.watchdog
    if args.tools is not None:
        CONFIG.allowed_tools = [item.strip() for item in args.tools.split(",") if item.strip()]
    if args.workspace is not None:
        CONFIG.workspace = args.workspace

    run_id = uuid.uuid4().hex[:12]
    model = None
    try:
        # Проверяем конфигурацию до первого запроса: понятная ошибка вместо
        # непредсказуемого поведения в середине работы.
        CONFIG.validate()
        CONFIG.resolved_workspace()

        log_event(
            run_id=run_id,
            step=0,
            stage="run",
            status="start",
            details=CONFIG.snapshot(),
        )

        model = DeepSeekModel(run_id=run_id)
        result = harness.run(model, args.question, run_id=run_id)

        if result["finish_reason"] == "max_steps":
            print(
                f"Остановлено: достигнут лимит шагов ({CONFIG.max_steps}). "
                "Модель не получила новый запрос.",
                file=sys.stderr,
            )
            return 3

        if result["finish_reason"] == "model_timeout":
            print(
                f"Остановлено: модель не ответила за {CONFIG.model_timeout} с. "
                "Повторный запрос не отправлялся.",
                file=sys.stderr,
            )
            return 4

        if result["finish_reason"] != "stop":
            raise RuntimeError(f"Неизвестная причина завершения: {result['finish_reason']}")

        answer = result["content"]
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("Программа завершилась без текстового ответа.")

        print(f"Ответ: {answer}")
        return 0

    except Exception as error:
        log_event(
            run_id=run_id,
            step=0,
            stage="run",
            status="error",
            error={"type": type(error).__name__},
        )
        print(f"Ошибка: {describe_error(error)}", file=sys.stderr)
        return 1

    finally:
        if model is not None:
            model.close()


if __name__ == "__main__":
    sys.exit(main())