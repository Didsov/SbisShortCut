import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
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
from kkt_lookup import KKTInfo, KKTLookupResult, find_all_kkt_by_owner_inn
from storage import database
from storage.export_excel import export_kkt_lookup_excel


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
    """Постоянный whitelist; администраторы берутся только из `.env`."""

    def __init__(self, path: Path, admins: frozenset[int]) -> None:
        self.path = path
        self.admins = admins
        self._users: set[int] = set()
        self._lock = RLock()
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            self._users = {int(value) for value in payload["allowed_user_ids"]}
            self._users.difference_update(admins)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admins

    def is_allowed(self, user_id: int) -> bool:
        with self._lock:
            return user_id in self.admins or user_id in self._users

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    {"allowed_user_ids": sorted(self._users)},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def add(self, user_id: int) -> bool:
        with self._lock:
            if self.is_allowed(user_id):
                return False
            self._users.add(user_id)
            try:
                self._save()
            except Exception:
                self._users.remove(user_id)
                raise
            return True

    def remove(self, user_id: int) -> bool:
        if self.is_admin(user_id):
            raise PermissionError("Администратора нельзя удалить")
        with self._lock:
            if user_id not in self._users:
                return False
            self._users.remove(user_id)
            try:
                self._save()
            except Exception:
                self._users.add(user_id)
                raise
            return True

    def all(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(sorted(self.admins | self._users))


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
        return str(value).strip() if value else "—"

    return "\n".join(
        (
            f"Касса {number}",
            f"ИНН владельца: {shown(item.owner_inn)}",
            f"ФИО владельца: {shown(item.owner_name)}",
            f"Модель кассы: {shown(item.model)}",
            f"Рег. номер: {shown(item.reg_number)}",
            f"Заводской номер: {shown(item.manufacturer_number)}",
            f"Срок ФН: {shown(item.fn_end_date)}",
            f"Срок ОФД: {shown(item.ofd_end_date)}",
        )
    )


def format_result(result: KKTLookupResult, limit: int = 3900) -> tuple[str, int]:
    total = len(result.cash_registers)
    if not total:
        return f"ИНН владельца: {result.owner_inn}\nПодходящие ККТ не найдены.", 0

    sections: list[str] = []
    shown_count = 0
    for index, item in enumerate(result.cash_registers, 1):
        candidate = [*sections, format_kkt(item, index)]
        footer = f"\nПоказано: {index} из {total}."
        if len("\n\n".join(candidate) + footer) > limit:
            break
        sections = candidate
        shown_count = index
    return "\n\n".join(sections) + f"\n\nПоказано: {shown_count} из {total}.", shown_count


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

        def report(text: str) -> None:
            future = asyncio.run_coroutine_threadsafe(status.edit_text(text), loop)
            try:
                future.result(timeout=15)
            except Exception:
                pass

        try:
            result = await asyncio.to_thread(
                find_all_kkt_by_owner_inn,
                inn,
                verbose=False,
                save_to_database=True,
                status_callback=report,
            )
            preview, shown_count = format_result(result)
            try:
                await status.edit_text(preview)
            except TelegramAPIError:
                await message.answer(preview)

            if shown_count < len(result.cash_registers):
                with TemporaryDirectory(prefix="inn_bot_") as directory:
                    output = Path(directory) / f"kkt_{inn}.xlsx"
                    await asyncio.to_thread(
                        export_kkt_lookup_excel,
                        cash_registers=result.cash_registers,
                        output_path=output,
                    )
                    await message.answer_document(
                        FSInputFile(output, filename=output.name),
                        caption=f"Все ККТ по ИНН {inn}.",
                        reply_markup=MENU,
                    )
        except Exception as error:
            print(f"Ошибка поиска по ИНН: {type(error).__name__}: {error}")
            await message.answer(
                "Не удалось получить данные из СБИС. Повторите запрос позже.",
                reply_markup=MENU,
            )
        finally:
            self._active_users.discard(sender.id)


def parse_user_id(value: str | None) -> int:
    text = str(value or "").strip()
    if not text.isdigit() or int(text) <= 0:
        raise ValueError
    return int(text)


def build_router(service: BotService, whitelist: WhitelistStore) -> Router:
    router = Router(name="inn_lookup")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.outer_middleware(AccessMiddleware(whitelist))

    async def require_admin(message: Message) -> bool:
        sender = message.from_user
        if sender is not None and whitelist.is_admin(sender.id):
            return True
        await message.answer("Команда доступна только администратору.")
        return False

    @router.message(Command(commands=["start", "help", "menu"]))
    async def menu(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Отправьте ИНН владельца из 10 или 12 цифр.\n"
            "Бот получит его ККТ из СБИС и сохранит их в локальную базу.",
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

    @router.message(Command("allow"))
    async def allow(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        try:
            user_id = parse_user_id(command.args)
            added = await asyncio.to_thread(whitelist.add, user_id)
        except ValueError:
            await message.answer("Использование: /allow TELEGRAM_USER_ID")
            return
        await message.answer("Пользователь добавлен." if added else "Доступ уже разрешён.")

    @router.message(Command("deny"))
    async def deny(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        try:
            user_id = parse_user_id(command.args)
            removed = await asyncio.to_thread(whitelist.remove, user_id)
        except ValueError:
            await message.answer("Использование: /deny TELEGRAM_USER_ID")
            return
        except PermissionError:
            await message.answer("Администратора нельзя удалить.")
            return
        await message.answer("Пользователь удалён." if removed else "Пользователь не найден.")

    @router.message(Command("whitelist"))
    async def whitelist_command(message: Message) -> None:
        if await require_admin(message):
            await message.answer("Разрешённые Telegram ID:\n" + "\n".join(map(str, whitelist.all())))

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
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    database.configure_database(settings.database_path)
    database.init_db()
    whitelist = WhitelistStore(settings.whitelist_path, settings.admin_user_ids)
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

