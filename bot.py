import os
import re
import logging
import asyncio
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram.enums import ParseMode, ChatType
from aiogram.client.default import DefaultBotProperties

from collections import deque, defaultdict
from typing import Deque, Dict, Tuple

# ==== OpenAI (официальный клиент) ====
from openai import AsyncOpenAI

# === Загрузка переменных окружения ===
load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHANNEL = "@MineBridgeOfficial"

if not BOT_TOKEN:
    raise SystemExit("Set BOT_TOKEN in .env")
if not OPENAI_API_KEY:
    raise SystemExit("Set OPENAI_API_KEY in .env")

# === Инициализация ===
# Включаем Markdown по умолчанию
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)  # <— Markdown включён
)
dp = Dispatcher()
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# глобальная переменная для хранения username бота (без @)
bot_username = "MineBridgeRegistrationBot"

# === Контекст 8 сообщений ===
MAX_HISTORY_MESSAGES = 8  # храним всего 5 последних сообщений (user/assistant вперемешку)
HistoryKey = Tuple[int, int]  # (chat_id, user_id)
HISTORY: Dict[HistoryKey, Deque[Tuple[str, str]]] = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))

def _shorten(s: str, limit: int = 300) -> str:
    s = (s or "").strip()
    return (s[:limit] + "...") if len(s) > limit else s

def make_key(msg: types.Message) -> HistoryKey:
    return (msg.chat.id, msg.from_user.id)

def build_input_with_history(key: HistoryKey, user_text: str, name: str) -> str:
    """Готовим вход для модели: короткий контекст + текущий вопрос."""
    lines: list[str] = []
    hist = HISTORY.get(key)
    if hist:
        lines.append("Контекст предыдущих сообщений (до 5):")
        for role, text in hist:
            who = "Пользователь" if role == "user" else "Ассистент"
            lines.append(f"{who}: {text}")
        lines.append("—")  # разделитель
    lines.append(f"Пользователь ({name}): {user_text}")
    lines.append("Ассистент:")
    print(lines)
    return "\n".join(lines)

def remember_user(key: HistoryKey, text: str) -> None:
    HISTORY[key].append(("user", _shorten(text)))

def remember_assistant(key: HistoryKey, text: str) -> None:
    HISTORY[key].append(("assistant", _shorten(text)))


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
        await message.answer("Майнкрафт сервер *временно оффлайн*.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться", url=f"https://t.me/{CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton(text="Проверить подписку", callback_data="check_subscription")]
    ])
    await message.answer(
        "Для доступа нужен канал @MineBridgeOfficial — подпишитесь и нажмите «*Проверить подписку*».",
        reply_markup=kb
    )


@dp.callback_query()
async def callback_any(query: types.CallbackQuery):
    if query.data != "check_subscription":
        await query.answer()
        return
    if await is_subscribed(query.from_user.id):
        await query.message.answer("Майнкрафт сервер *временно оффлайн*.")
        await query.answer()
    else:
        await query.answer("Подписка не найдена. Убедитесь, что подписаны на канал.", show_alert=True)


# === GPT-ответ через OpenAI (не-стрим, как fallback) ===
async def ask_openai(user_text: str, name: str, conv_key: HistoryKey) -> str | None:
    """Запрос к OpenAI без стрима. Возвращает текст или None при ошибке."""
    try:
        prompt = (user_text or "").strip()
        if not prompt:
            return None
        prompt = _shorten(prompt)  # легкая отсечка длины

        # Сначала соберём вход с контекстом, затем запомним реплику пользователя
        input_with_ctx = build_input_with_history(conv_key, prompt, name)
        remember_user(conv_key, prompt)

        logging.info(
            "Calling OpenAI (non-stream) for user '%s' prompt='%s'",
            name, (prompt[:80] + '...') if len(prompt) > 80 else prompt
        )

        resp = await openai_client.responses.create(
            model="gpt-4o-mini",
            instructions="Бот, отвечающий на вопросы пользователей о Minecraft сервере MineBridge. Сайт: майнбридж.рф",
            input=input_with_ctx,
            temperature=0.5,
        )
        text = resp.output_text
        if text:
            remember_assistant(conv_key, text)
        return text
    except Exception:
        logging.exception("OpenAI error (non-stream)")
        return None


# === GPT-стрим с троттлингом ===
async def stream_openai(user_text: str, name: str, conv_key: HistoryKey):
    """
    Асинхронный генератор: отдаёт дельты текста (строки) по мере генерации модели.
    Бросает исключение при ошибке (сверху поймаем и упадём на fallback).
    """
    prompt = (user_text or "").strip()
    if not prompt:
        return

    prompt = _shorten(prompt)
    input_with_ctx = build_input_with_history(conv_key, prompt, name)
    remember_user(conv_key, prompt)

    logging.info(
        "Calling OpenAI (stream) for user '%s' prompt='%s'",
        name, (prompt[:80] + '...') if len(prompt) > 80 else prompt
    )

    # официальный стрим SDK
    async with openai_client.responses.stream(
        model="gpt-4o-mini",
        instructions="Бот, отвечающий на вопросы пользователей о Minecraft сервере MineBridge. Сайт: майнбридж.рф.",
        input=input_with_ctx,
        temperature=0.5,
    ) as stream:
        full_text_parts: list[str] = []
        async for event in stream:
            if event.type == "response.output_text.delta":
                delta = event.delta or ""
                full_text_parts.append(delta)
                yield delta
            elif event.type == "response.error":
                raise RuntimeError(getattr(event, "error", "OpenAI streaming error"))
        # Финальный ответ
        final_resp = await stream.get_final_response()
        final_text = "".join(full_text_parts) if full_text_parts else getattr(final_resp, "output_text", "") or ""
        if final_text.strip():
            remember_assistant(conv_key, final_text)


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

    # ищем слово 'бот'
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
        await message.answer("Подпишитесь на @MineBridgeOfficial, чтобы пользоваться ботом.")
        return

    if message.text.strip().lower() in ("status", "сервер", "статус", "offline", "офлайн"):
        await message.reply("Майнкрафт сервер *временно оффлайн*.")
        return

    # === ВАЖНО: требуем упоминание только в группах/супергруппах ===
    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    if is_group and not is_mentioned_or_reply(message):
        logging.info("Пропущено сообщение без упоминания бота или ответа на бота (группа).")
        return
    # В личке (private) — всегда отвечаем

    # --- вспомогательные функции с backoff/троттлингом ---
    max_attempts = 4

    async def safe_edit_to(msg: types.Message, text: str, markdown: bool = True) -> bool:
        """Безопасный edit_text с backoff; при проблемах парсинга — пробуем без Markdown."""
        attempt = 0
        backoff = 1.0
        while True:
            try:
                await msg.edit_text(text, parse_mode=(ParseMode.MARKDOWN if markdown else None))
                return True
            except TelegramRetryAfter as e:
                attempt += 1
                wait = getattr(e, "retry_after", backoff)
                logging.warning("TelegramRetryAfter on edit: waiting %s seconds (attempt %d)", wait, attempt)
                await asyncio.sleep(wait)
                backoff *= 2
                if attempt >= max_attempts:
                    logging.error("Max attempts reached for edit; aborting edit.")
                    return False
            except TelegramBadRequest as e:
                # если Markdown ломается на частичных ответах — пробуем без parse_mode
                if markdown and "can't parse entities" in str(e).lower():
                    markdown = False
                    continue
                logging.exception("Telegram edit error (bad request): %s", e)
                return False
            except TelegramForbiddenError as e:
                logging.exception("Telegram edit forbidden: %s", e)
                return False
            except Exception:
                logging.exception("Unexpected error while editing message")
                return False

    async def safe_edit(text: str):
        # совместимость со старым кодом
        return await safe_edit_to(sent_msg, text, markdown=True)

    async def safe_send_reply(text: str):
        """Отправить reply с backoff (для частей ответа после edit)."""
        attempt = 0
        backoff = 1.0
        while True:
            try:
                return await message.reply(text, parse_mode=ParseMode.MARKDOWN)
            except TelegramRetryAfter as e:
                attempt += 1
                wait = getattr(e, "retry_after", backoff)
                logging.warning("TelegramRetryAfter on send: waiting %s seconds (attempt %d)", wait, attempt)
                await asyncio.sleep(wait)
                backoff *= 2
                if attempt >= max_attempts:
                    logging.error("Max attempts reached for send; aborting send.")
                    return None
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                logging.exception("Telegram send error: %s", e)
                return None
            except Exception:
                logging.exception("Unexpected error while sending message")
                return None

    try:
        # (опционально) показать "печатает..."
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        except Exception:
            pass

        # Сообщение-заглушка
        sent_msg = await message.reply("⏳ *Печатаю...*")

        # Параметры троттлинга для стрима
        CHUNK = 4000               # лимит Telegram для Markdown с запасом
        SEND_MIN_CHARS = 220       # минимум новых символов, чтобы делать edit
        SEND_MIN_SECONDS = 1.2     # минимум секунд между edit'ами одного сообщения

        # Попробуем стрим
        try:
            loop = asyncio.get_running_loop()
            monotonic = loop.time

            active_msg = sent_msg        # текущее сообщение, которое редактируем
            current_chunk_text = ""      # текст для текущего сообщения
            last_sent_len = 0            # сколько уже "зафиксировано" в active_msg
            last_edit_ts = monotonic()   # когда в последний раз редактировали

            username = (message.from_user.username or f"{message.from_user.first_name}")

            async for delta in stream_openai(message.text, username, make_key(message)):
                if not delta:
                    continue
                current_chunk_text += delta

                # если переполнили лимит сообщения — финализируем этот чанк и создаём новый
                while len(current_chunk_text) > CHUNK:
                    first_part = current_chunk_text[:CHUNK]
                    rest = current_chunk_text[CHUNK:]

                    # финализируем текущий active_msg (Markdown может быть валидным уже сейчас)
                    await safe_edit_to(active_msg, first_part, markdown=True)

                    # создаём новое сообщение сразу с содержимым остатка (чтобы не делать лишний edit)
                    new_msg = await safe_send_reply(rest if rest.strip() else "...")
                    if new_msg is None:
                        # если не удалось отправить продолжение — просто выходим из стрима
                        raise RuntimeError("Failed to send continuation message")

                    active_msg = new_msg
                    current_chunk_text = rest
                    last_sent_len = len(rest)  # уже отправили целиком как новое сообщение
                    last_edit_ts = monotonic()

                # троттлим частоту edit'ов: по времени И по размеру дельты
                now = monotonic()
                need_edit = (
                    (len(current_chunk_text) - last_sent_len >= SEND_MIN_CHARS) and
                    (now - last_edit_ts >= SEND_MIN_SECONDS)
                )

                if need_edit:
                    # Во время стрима лучше без Markdown, чтобы не падать на незакрытых форматах
                    ok = await safe_edit_to(active_msg, current_chunk_text, markdown=False)
                    if ok:
                        last_sent_len = len(current_chunk_text)
                        last_edit_ts = now

            # стрим завершился — финальный аккуратный апдейт с Markdown
            if current_chunk_text:
                await safe_edit_to(active_msg, current_chunk_text, markdown=True)
            else:
                await safe_edit("Не удалось получить ответ — попробуйте позже.")
                return

        except Exception:
            logging.exception("Streaming failed; falling back to non-stream response")
            # Fallback: однократный запрос
            username = (message.from_user.username or f"{message.from_user.first_name}")
            resp = await ask_openai(message.text, username, make_key(message))

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
                first = resp_text[:CHUNK]
                ok = await safe_edit(first)
                if ok:
                    rest = resp_text[CHUNK:]
                    # отправляем оставшееся как дополнительные сообщения по частям
                    for i in range(0, len(rest), CHUNK):
                        part = rest[i:i + CHUNK]
                        new_msg = await safe_send_reply(part)
                        if new_msg is None:
                            logging.error("Failed to send continuation part starting at %d", CHUNK + i)
                            break

    except Exception:
        logging.exception("Ошибка в auto_reply (stream loop)")
        try:
            await sent_msg.edit_text("Произошла внутренняя ошибка — попробуйте позже.", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass


# === Завершение работы ===
async def shutdown():
    try:
        # У OpenAI клиента явное закрытие не требуется; оставлено на случай изменений
        if hasattr(openai_client, "close") and asyncio.iscoroutinefunction(openai_client.close):
            await openai_client.close()
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
