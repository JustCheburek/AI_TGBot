import os
import re
import logging
import asyncio
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter

# ==== g4f (правильный импорт) ====
from g4f.client import AsyncClient

# === Загрузка переменных окружения ===
load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL = "@MineBridgeOfficial"

if not BOT_TOKEN:
    raise SystemExit("Set BOT_TOKEN in .env")

# === Инициализация ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
g4f_client = AsyncClient()

# глобальная переменная для хранения username бота (без @)
bot_username = "MineBridgeRegistrationBot"


def getPromt(name: str, promt: str) -> str:
    """Формирует подсказку для модели.
    name — имя пользователя (или username), promt — сам запрос.
    """
    return f"""Ты бот, который отвечает на вопросы пользователей о Minecraft сервере MineBridge (сайт - майнбридж.рф).
Вот запрос от {name}:
{promt}
"""


async def on_startup():
    global bot_username
    try:
        me = await bot.get_me()
        # сохраняем имя без '@' в нижнем регистре
        bot_username = (me.username or "").lower()
        logging.info(f"Bot username: @{bot_username}")
    except Exception:
        logging.exception("Failed to get bot username on startup")


# === Подписка ===
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL, user_id=user_id)
        return member.status in ("creator", "administrator", "member", "restricted")
    except (TelegramForbiddenError, TelegramBadRequest):
        return False
    except Exception:
        logging.exception("Error checking subscription")
        return False


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if await is_subscribed(message.from_user.id):
        await message.answer("Майнкрафт сервер временно оффлайн.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться", url=f"https://t.me/{CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton(text="Проверить подписку", callback_data="check_subscription")]
    ])
    await message.answer(
        "Для доступа нужен канал @MineBridgeOfficial — подпишитесь и нажмите «Проверить подписку».",
        reply_markup=kb
    )


@dp.callback_query()
async def callback_any(query: types.CallbackQuery):
    if query.data != "check_subscription":
        await query.answer()
        return
    if await is_subscribed(query.from_user.id):
        await query.message.answer("Майнкрафт сервер временно оффлайн.")
        await query.answer()
    else:
        await query.answer("Подписка не найдена. Убедитесь, что подписаны на канал.", show_alert=True)


# === GPT-ответ ===
async def ask_g4f(user_text: str, name: str) -> str | None:
    """Вызываем g4f с таймаутом — чтобы бот не зависал навсегда.

    Возвращаем содержимое ответа или None при ошибке/таймауте.
    """
    try:
        prompt = user_text.strip()
        if not prompt:
            return None
        if len(prompt) > 300:
            prompt = prompt[:300] + "..."

        system_prompt = getPromt(name or "user", prompt)

        logging.info("Calling g4f for user '%s' prompt='%s'", name, (prompt[:80] + '...') if len(prompt) > 80 else prompt)

        # Оборачиваем запрос в asyncio.wait_for — защищаемся от вечного ожидания
        try:
            resp = await asyncio.wait_for(
                g4f_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": system_prompt}],
                    web_search=False
                ),
                timeout=60.0,  # <- при необходимости увеличить
            )
            print(resp)
        except asyncio.TimeoutError:
            logging.warning("g4f request timed out")
            return None

        # Пробуем аккуратно извлечь текст
        try:
            return resp.choices[0].message.content
        except Exception:
            return str(resp)
    except Exception:
        logging.exception("g4f error")
        return None


# === Проверка упоминания бота или ответа ===
def is_mentioned_or_reply(message: types.Message) -> bool:
    if message.reply_to_message and message.reply_to_message.from_user.is_bot:
        return True

    # Проверка на сущности-mention (например @BotName)
    if message.entities and message.text:
        for entity in message.entities:
            if entity.type == "mention":
                mention_text = message.text[entity.offset: entity.offset + entity.length]
                if mention_text.lstrip("@").lower() == bot_username:
                    return True

    # ищем слово 'бот' окружённое пробелами
    if message.text:
        if re.search(r"бот", message.text.lower()):
            return True

    return False


@dp.message()
async def auto_reply(message: types.Message):
    if not message.text:
        return

    user_id = message.from_user.id

    if not await is_subscribed(user_id):
        await message.answer("Подпишитесь на @MineBridgeOfficial чтобы пользоваться ботом.")
        return

    if message.text.strip().lower() in ("status", "сервер", "статус", "offline", "офлайн"):
        await message.reply("Майнкрафт сервер временно оффлайн.")
        return

    if not is_mentioned_or_reply(message):
        logging.info("Пропущено сообщение без упоминания бота или ответа на бота.")
        return

    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception:
        pass

    # Создаем сообщение-ответ
    sent_msg = await message.reply("⏳ Печатаю...")

    # Параметры для retry/backoff
    max_attempts = 4

    async def safe_edit(text: str):
        attempt = 0
        backoff = 1.0
        while True:
            try:
                await sent_msg.edit_text(text)
                return True
            except TelegramRetryAfter as e:
                attempt += 1
                wait = e.retry_after if hasattr(e, "retry_after") else backoff
                logging.warning("TelegramRetryAfter on edit: waiting %s seconds (attempt %d)", wait, attempt)
                await asyncio.sleep(wait)
                backoff *= 2
                if attempt >= max_attempts:
                    logging.error("Max attempts reached for edit; aborting edit.")
                    return False
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                logging.exception("Telegram edit error (forbidden/bad request): %s", e)
                return False
            except Exception:
                logging.exception("Unexpected error while editing message")
                return False

    async def safe_send_reply(text: str):
        """Отправить reply с backoff (для частей ответа после edit)."""
        attempt = 0
        backoff = 1.0
        while True:
            try:
                await message.reply(text)
                return True
            except TelegramRetryAfter as e:
                attempt += 1
                wait = e.retry_after if hasattr(e, "retry_after") else backoff
                logging.warning("TelegramRetryAfter on send: waiting %s seconds (attempt %d)", wait, attempt)
                await asyncio.sleep(wait)
                backoff *= 2
                if attempt >= max_attempts:
                    logging.error("Max attempts reached for send; aborting send.")
                    return False
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                logging.exception("Telegram send error (forbidden/bad request): %s", e)
                return False
            except Exception:
                logging.exception("Unexpected error while sending message")
                return False

    try:
        logging.info("Requesting g4f for message from %s", message.from_user.id)
        # вызываем не-стримовую функцию с именем пользователя
        username = (message.from_user.username or f"{message.from_user.first_name}")
        resp = await ask_g4f(message.text, username)

        if resp is None:
            await safe_edit("Не удалось получить ответ — попробуйте позже.")
            return

        resp_text = str(resp).strip()
        if not resp_text:
            await safe_edit("Не удалось получить ответ — попробуйте позже.")
            return

        # Telegram limit — используем 4000 символов для безопасности
        CHUNK = 4000
        if len(resp_text) <= CHUNK:
            await safe_edit(resp_text)
        else:
            # Сначала редактируем основное сообщение первой частью
            first = resp_text[:CHUNK]
            ok = await safe_edit(first)
            # отправляем оставшееся как дополнительные сообщения по частям
            if ok:
                rest = resp_text[CHUNK:]
                for i in range(0, len(rest), CHUNK):
                    part = rest[i:i+CHUNK]
                    sent_ok = await safe_send_reply(part)
                    if not sent_ok:
                        logging.error("Failed to send continuation part starting at %d", CHUNK + i)
                        break

    except Exception:
        logging.exception("Ошибка в auto_reply (non-stream) loop")
        try:
            await safe_edit("Произошла внутренняя ошибка — попробуйте позже.")
        except Exception:
            pass


# === Завершение работы ===
async def shutdown():
    try:
        await g4f_client.aclose()
    except Exception:
        pass
    try:
        await bot.session.close()
    except Exception:
        pass


# === Запуск ===
async def main():
    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())
