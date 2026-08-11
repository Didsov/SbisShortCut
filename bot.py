import asyncio
import html
import time
from collections.abc import Awaitable, Callable
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    ForceReply,
    FSInputFile,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from config.settings import Settings, load_settings
from excel_export import export_kkt
from lookup import KKTInfo, KKTLookupResult, find_all_kkt_by_owner_inn


SEARCH_BUTTON = "🔎 Поиск по ИНН"
MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=SEARCH_BUTTON)]],
    resize_keyboard=True,
    is_persistent=True,
)


class SearchForm(StatesGroup):
    waiting_inn = State()


def normalize_inn(value: str) -> str:
    inn = "".join(str(value).split())
    if not inn.isdigit() or len(inn) not in {10, 12}:
        raise ValueError("ИНН должен содержать 10 или 12 цифр")
    return inn


class WhitelistStore:
    """Белый список из `.env`, без файлового или иного хранилища."""

    def __init__(self, allowed_user_ids: frozenset[int]) -> None:
        self.allowed_user_ids = allowed_user_ids

    def is_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_user_ids


class AccessMiddleware(BaseMiddleware):
    def __init__(self, whitelist: WhitelistStore) -> None:
        self.whitelist = whitelist

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        sender = event.from_user
        if sender is None:
            return None
        if not self.whitelist.is_allowed(sender.id):
            await event.answer(
                "Доступ запрещён.\n"
                f"Ваш Telegram User ID: {sender.id}\n"
                "Передайте этот ID администратору бота."
            )
            return None
        return await handler(event, data)


def format_kkt(item: KKTInfo, number: int) -> str:
    def shown(value: str | None) -> str:
        text = str(value).strip() if value else "—"
        return f"<code>{html.escape(text)}</code>"

    return "\n".join(
        (
            f"<b>Касса №{number}</b>",
            "",
            f"<b>ИНН владельца:</b> {shown(item.owner_inn)}",
            f"<b>Владелец:</b> {shown(item.owner_name)}",
            f"<b>Модель:</b> {shown(item.model)}",
            f"<b>РНМ:</b> {shown(item.reg_number)}",
            f"<b>Заводской номер:</b> {shown(item.manufacturer_number)}",
            f"<b>Срок ФН:</b> {shown(item.fn_end_date)}",
            f"<b>Срок ОФД:</b> {shown(item.ofd_end_date)}",
            "",
            f"<b>Адрес точки продаж:</b> {shown(item.sales_point_address)}",
        )
    )


class BotService:
    def __init__(self, cooldown: float = 5.0) -> None:
        self.cooldown = cooldown
        self._last_lookup: dict[int, float] = {}
        self._active_users: set[int] = set()

    async def search(self, message: Message, inn: str) -> None:
        sender = message.from_user
        if sender is None:
            return
        now = time.monotonic()
        if sender.id in self._active_users:
            await message.answer("Ваш предыдущий поиск ещё выполняется.", reply_markup=MENU)
            return
        previous = self._last_lookup.get(sender.id, 0.0)
        if now - previous < self.cooldown:
            await message.answer("Подождите несколько секунд перед повтором.", reply_markup=MENU)
            return

        self._last_lookup[sender.id] = now
        self._active_users.add(sender.id)
        status = await message.answer("Получаю ККТ из СБИС…", reply_markup=MENU)
        loop = asyncio.get_running_loop()

        async def update_status(text: str) -> None:
            """Преобразует aiogram awaitable в настоящую coroutine."""
            await status.edit_text(text)

        def report(text: str) -> None:
            future = asyncio.run_coroutine_threadsafe(update_status(text), loop)
            try:
                future.result(timeout=15)
            except Exception:
                pass

        try:
            result = await asyncio.to_thread(
                find_all_kkt_by_owner_inn,
                inn,
                status_callback=report,
            )
            total = len(result.cash_registers)
            if total == 0:
                text = "Действующих касс в ОФД СБИС нет."
                try:
                    await status.edit_text(text)
                except TelegramAPIError:
                    await message.answer(text, reply_markup=MENU)
                return

            if total > 6:
                summary = f"<b>Найдено касс: {total} шт.</b>\nПолный список — в файле."
                try:
                    await status.edit_text(summary, parse_mode="HTML")
                except TelegramAPIError:
                    await message.answer(summary, parse_mode="HTML")
                with TemporaryDirectory(prefix="inn_bot_") as directory:
                    output = Path(directory) / f"kkt_{inn}.xlsx"
                    await asyncio.to_thread(
                        export_kkt,
                        result.cash_registers,
                        output,
                    )
                    await message.answer_document(
                        FSInputFile(output, filename=output.name),
                        caption=f"Кассы по ИНН {inn}: {total} шт.",
                        reply_markup=MENU,
                    )
                return

            try:
                await status.edit_text(
                    f"<b>Найдено касс: {total} шт.</b>",
                    parse_mode="HTML",
                )
            except TelegramAPIError:
                pass

            for index, item in enumerate(result.cash_registers, 1):
                await message.answer(
                    format_kkt(item, index),
                    parse_mode="HTML",
                    reply_markup=MENU if index == total else None,
                )
        except Exception as error:
            print(f"Ошибка поиска по ИНН: {type(error).__name__}: {error}")
            await message.answer(
                "Не удалось получить данные из СБИС. Повторите запрос позже.",
                reply_markup=MENU,
            )
        finally:
            self._active_users.discard(sender.id)


def build_router(service: BotService, whitelist: WhitelistStore) -> Router:
    router = Router(name="inn_lookup")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.outer_middleware(AccessMiddleware(whitelist))

    @router.message(Command(commands=["start", "help", "menu"]))
    async def menu(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Отправьте ИНН владельца из 10 или 12 цифр.\n"
            "Бот получит его ККТ напрямую из СБИС.",
            reply_markup=MENU,
        )

    @router.message(Command("whoami"))
    async def whoami(message: Message) -> None:
        if message.from_user:
            await message.answer(f"Ваш Telegram User ID: {message.from_user.id}")

    @router.message(Command("inn"))
    async def inn_command(message: Message, state: FSMContext, command: CommandObject) -> None:
        if command.args:
            try:
                await state.clear()
                await service.search(message, normalize_inn(command.args))
            except ValueError:
                await message.answer("ИНН должен содержать 10 или 12 цифр.")
        else:
            await state.set_state(SearchForm.waiting_inn)
            await message.answer("Введите ИНН:", reply_markup=ForceReply(selective=True))

    @router.message(F.text == SEARCH_BUTTON)
    async def search_button(message: Message, state: FSMContext) -> None:
        await state.set_state(SearchForm.waiting_inn)
        await message.answer("Введите ИНН:", reply_markup=ForceReply(selective=True))

    @router.message(SearchForm.waiting_inn, F.text)
    async def waiting_inn(message: Message, state: FSMContext) -> None:
        try:
            inn = normalize_inn(message.text or "")
        except ValueError:
            await message.answer("ИНН должен содержать 10 или 12 цифр.")
            return
        await state.clear()
        await service.search(message, inn)

    @router.message(F.text)
    async def automatic_search(message: Message, state: FSMContext) -> None:
        try:
            inn = normalize_inn(message.text or "")
        except ValueError:
            await message.answer("Отправьте ИНН из 10 или 12 цифр.", reply_markup=MENU)
            return
        await state.clear()
        await service.search(message, inn)

    return router


async def run(settings: Settings) -> None:
    whitelist = WhitelistStore(settings.allowed_user_ids)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(BotService(), whitelist))

    async with Bot(token=settings.telegram_token) as telegram_bot:
        await telegram_bot.set_my_commands(
            [
                BotCommand(command="start", description="Открыть меню"),
                BotCommand(command="inn", description="Найти ККТ по ИНН"),
                BotCommand(command="whoami", description="Показать Telegram ID"),
            ]
        )
        await dispatcher.start_polling(
            telegram_bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            tasks_concurrency_limit=4,
        )


def main() -> None:
    settings = load_settings()
    if settings.log_path is None:
        asyncio.run(run(settings))
        return

    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.log_path.open("a", encoding="utf-8", buffering=1) as stream:
        with redirect_stdout(stream), redirect_stderr(stream):
            print(f"\n=== Запуск {datetime.now().astimezone().isoformat()} ===")
            asyncio.run(run(settings))


if __name__ == "__main__":
    main()
