import argparse
import html
import os
import re
import sys
from contextlib import redirect_stdout
from io import StringIO

from bot import format_kkt
from lookup import KKTInfo, find_all_kkt_by_owner_inn, normalize_inn


def format_kkt_cli(item: KKTInfo, number: int) -> str:
    """Преобразует Telegram HTML-карточку в читаемый консольный Markdown."""
    text = format_kkt(item, number)
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text)
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text)
    return html.unescape(text)


def _inn(value: str) -> str:
    try:
        return normalize_inn(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Получить из СБИС список всех касс владельца по ИНН.",
    )
    parser.add_argument("inn", type=_inn, help="ИНН владельца: 10 или 12 цифр")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.environ.get("SBIS_COOKIES", "").strip():
        print("Ошибка: в .env не задан SBIS_COOKIES", file=sys.stderr)
        return 1

    try:
        # Коллектор пишет служебную диагностику в stdout. Она не должна
        # смешиваться со списком карточек, предназначенным для пользователя.
        with redirect_stdout(StringIO()):
            result = find_all_kkt_by_owner_inn(args.inn)
    except Exception as error:
        print(f"Ошибка получения ККТ: {error}", file=sys.stderr)
        return 1

    if not result.cash_registers:
        if result.errors:
            print(
                "Кассы не получены из-за ошибок СБИС: " + "; ".join(result.errors),
                file=sys.stderr,
            )
            return 1
        print(f"Кассы по ИНН {args.inn} не найдены.")
        return 0

    print(
        "\n\n".join(
            format_kkt_cli(item, number)
            for number, item in enumerate(result.cash_registers, 1)
        )
    )
    if result.errors:
        print(
            "Предупреждение: часть данных не получена: " + "; ".join(result.errors),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
