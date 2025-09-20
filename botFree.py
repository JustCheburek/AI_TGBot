import os
import re
import logging
import asyncio
from dotenv import load_dotenv
from pathlib import Path
import inspect

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram.enums import ParseMode, ChatType
from aiogram.client.default import DefaultBotProperties

from collections import deque, defaultdict
from typing import Deque, Dict, Tuple

# ==== g4f (неофициальный клиент) ====
from g4f.client import AsyncClient as G4FAsyncClient

# === Загрузка переменных окружения ===
load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
# OPENAI_API_KEY больше не обязателен при использовании g4f
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # необязательный, не используется
CHANNEL = "@MineBridgeOfficial"

if not BOT_TOKEN:
    raise SystemExit("Set BOT_TOKEN in .env")

# === Инициализация ===
# Включаем Markdown по умолчанию
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)  # <— Markdown включён
)
dp = Dispatcher()
g4f_client = G4FAsyncClient()

# глобальная переменная для хранения username бота (без @)
bot_username = "minebridge52bot"

# === Контекст N сообщений ===
MAX_HISTORY_MESSAGES = 8  # храним всего N последних сообщений (user/assistant вперемешку)
HistoryKey = Tuple[int, int]  # (chat_id, user_id)
HISTORY: Dict[HistoryKey, Deque[Tuple[str, str]]] = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))

# retry params for g4f
MAX_G4F_RETRIES = 2
G4F_BACKOFF_BASE = 1.5  # seconds, exponential backoff with cap

STALL_TIMEOUT = 15.0   # сек. без дельт — считаем, что провайдер подвис
FALLBACK_CHUNK = 200   # размер дельты при fallback (non-stream)

_ZW_RE = re.compile(r"[\u200b\u200c\u200d\u2060\uFEFF]")          # zero-width + BOM
_NBSP_RE = re.compile(r"[\u00A0\u202F\u2007]")                    # неразрывные/узкие пробелы
_WS_FIX_RE = re.compile(r"[ \t]{2,}")                             # лишние пробелы
_NEWLINE_RE = re.compile(r"\r\n?")                                # \r\n / \r -> \n

_MD2_SPECIALS = r"_*[]()~`>#+-=|{}.!"
_MD2_ESC_RE = re.compile("([" + re.escape(_MD2_SPECIALS) + "])")

def escape_markdown_v2(text: str) -> str:
    # Экранируем спецсимволы MarkdownV2
    return _MD2_ESC_RE.sub(r"\\\1", text)

def sanitize_for_tg(text: str) -> str:
    if not isinstance(text, str):
        text = str(text or "")
    # нормализуем переводы строк
    text = _NEWLINE_RE.sub("\n", text)
    # убираем zero-width
    text = _ZW_RE.sub("", text)
    # приводим «нестандартные» пробелы к обычному
    text = _NBSP_RE.sub(" ", text)
    # иногда провайдеры склеивают «слово.\nСлово» без пробела — добавим, если нужно
    text = re.sub(r"(\S)(\n)(\S)", r"\1\n\3", text)
    # схлопнем чрезмерные пробелы, но не трогаем одиночные
    #text = _WS_FIX_RE.sub("  ", text)  # оставляем максимум два, чтобы Markdown не «схлопывал» абзацы
    return text.strip()

CHAT_LOCKS: Dict[int, asyncio.Lock] = {}

def _get_chat_lock(chat_id: int) -> asyncio.Lock:
    lock = CHAT_LOCKS.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        CHAT_LOCKS[chat_id] = lock
    return lock

async def _extract_retry_after_seconds(err) -> float | None:
    """Попытаться извлечь время ожидания из ошибки (headers/атрибуты/текст). Работает и для g4f, если провайдер вернул подсказку."""
    # 1) retry-after в headers (если есть)
    try:
        headers = getattr(err, "headers", None) or {}
        if headers:
            ra = headers.get("retry-after") or headers.get("Retry-After")
            if ra:
                try:
                    return float(ra)
                except Exception:
                    pass
    except Exception:
        pass

    # 2) retry_after атрибут
    try:
        ra = getattr(err, "retry_after", None)
        if ra is not None:
            return float(ra)
    except Exception:
        pass

    # 3) парсинг текста вида "Please try again in 7m12s" или "in 20s"
    try:
        msg = str(err)
        m = re.search(r'(\d+)\s*m(?:in)?\s*(\d+)\s*s', msg)
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
        m2 = re.search(r'in\s*(\d+)\s*s', msg)
        if m2:
            return int(m2.group(1))
        m3 = re.search(r'(\d+)\s*seconds', msg)
        if m3:
            return int(m3.group(1))
    except Exception:
        pass

    return None

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
        for role, text in hist:
            who = "Игрок" if role == "user" else "Ассистент"
            lines.append(f"{who}: {text}")
        lines.append("—")  # разделитель
    lines.append(f"Игрок ({name}): {user_text}")
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

# === Загрузка системного промта из .txt ===
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_PROMPT_CACHE: Dict[str, Tuple[float, str]] = {}

def _read_txt_prompt(path: Path) -> str:
    """
    Читает текстовый файл промта как есть (UTF-8), кэширует по mtime.
    Подрезает BOM и лишние пробелы по краям.
    """
    mtime = path.stat().st_mtime  # FileNotFoundError пробросится выше — поймаем выше
    cache_key = str(path)
    cached = _PROMPT_CACHE.get(cache_key)
    if cached and cached[0] == mtime:
        return cached[1]

    raw = path.read_text(encoding="utf-8")
    # убрать BOM, нормализовать переводы строк, обрезать края
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff")
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()

    _PROMPT_CACHE[cache_key] = (mtime, text)
    return text

def load_system_prompt_for_chat(chat: types.Chat) -> str:
    """
    Для групп/супергрупп сначала пробуем prompts/<chat_id>.txt,
    иначе — prompts/default.txt. При любой ошибке — встроенный fallback.
    """
    try:
        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            group_path = PROMPTS_DIR / f"{chat.id}.txt"
            if group_path.exists():
                return _read_txt_prompt(group_path)
        # fallback: default.txt
        default_path = PROMPTS_DIR / "default.txt"
        return _read_txt_prompt(default_path)
    except FileNotFoundError:
        logging.warning("Prompt .txt file not found; using builtin fallback")
    except Exception as e:
        logging.exception("Failed to load .txt prompt: %s", e)

    # Встроенный запасной промт
    return "Я сегодня не смогу помочь, мой системный промт сломался."


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

def _chunk_text(chunk) -> str | None:
    """
    Достаём текст из чанка. НЕ отбрасываем чисто пробельные дельты —
    g4f часто присылает пробел отдельным чанком.
    """
    # OpenAI-совместимые структуры
    try:
        choices = getattr(chunk, "choices", None)
        if isinstance(choices, (list, tuple)) and choices:
            ch0 = choices[0]

            # delta.*
            delta = getattr(ch0, "delta", None)
            if delta is not None:
                # пропускаем tool_calls
                if getattr(delta, "tool_calls", None):
                    return None
                txt = getattr(delta, "content", None)
                if isinstance(txt, str) and txt is not None:
                    return txt  # ВАЖНО: не .strip()

            # message.content
            msg = getattr(ch0, "message", None)
            if isinstance(msg, dict):
                txt = msg.get("content")
                if isinstance(txt, str) and txt is not None:
                    return txt
            elif msg is not None:
                txt = getattr(msg, "content", None)
                if isinstance(txt, str) and txt is not None:
                    return txt

            # text
            txt = getattr(ch0, "text", None)
            if isinstance(txt, str) and txt is not None:
                return txt
    except Exception:
        pass

    # Некоторые провайдеры просто шлют строки
    if isinstance(chunk, str):
        # отфильтруем явные метаданные, если провайдер зачем-то присылает их строкой
        if "object='chat.completion" in chunk or "provider=" in chunk:
            return None
        return chunk  # даже если это просто " "

    return None


async def stream_g4f(user_text: str, name: str, conv_key: HistoryKey, sys_prompt: str):
    prompt = (user_text or "").strip()
    if not prompt:
        return
    prompt = _shorten(prompt)
    input_with_ctx = build_input_with_history(conv_key, prompt, name)
    remember_user(conv_key, prompt)

    logging.info("Calling g4f (stream=True) for user '%s' prompt='%s'",
                 name, (prompt[:80] + '...') if len(prompt) > 80 else prompt)

    attempt = 0
    full_parts: list[str] = []

    async def _aiter_with_timeout(obj, timeout: float):
        """
        Итерируем async/sync поток с таймаутом между элементами.
        Если timeout достигнут — бросаем asyncio.TimeoutError.
        """
        # async generator
        if hasattr(obj, "__aiter__"):
            it = obj.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(it.__anext__(), timeout=timeout)
                except StopAsyncIteration:
                    break
                yield chunk
            return
        # sync iterator
        if hasattr(obj, "__iter__"):
            start = asyncio.get_running_loop().time()
            for ch in obj:
                yield ch
                await asyncio.sleep(0)
                start = asyncio.get_running_loop().time()
            return
        # одиночное значение
        yield obj

    while True:
        try:
            # 1) пробуем реальный стрим
            resp_stream = g4f_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": input_with_ctx},
                ],
                temperature=0.5,
                stream=True,
            )
            if inspect.isawaitable(resp_stream):
                resp_stream = await resp_stream

            try:
                async for chunk in _aiter_with_timeout(resp_stream, STALL_TIMEOUT):
                    piece = _chunk_text(chunk)
                    if piece is None:
                        continue
                    full_parts.append(piece)
                    yield piece
            except asyncio.TimeoutError:
                logging.warning("g4f stream stalled > %.1fs; switching to non-stream fallback", STALL_TIMEOUT)

                # 2) fallback: обычный completion без стрима
                resp_full = await g4f_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": input_with_ctx},
                    ],
                    temperature=0.5,
                    stream=False,
                )

                # универсально достаём текст
                final_text = ""
                try:
                    final_text = resp_full.choices[0].message.content or ""
                except Exception:
                    final_text = getattr(resp_full, "content", "") or getattr(resp_full, "text", "") or (str(resp_full) if resp_full is not None else "")

                final_text = final_text or ""
                if final_text:
                    # раздаём дельты, чтобы UI остался «стримовым»
                    for i in range(0, len(final_text), FALLBACK_CHUNK):
                        yield final_text[i:i+FALLBACK_CHUNK]
                        await asyncio.sleep(0)
                    full_parts.append(final_text)
                # выходим из retry-цикла
                break

            # 3) стрим завершился сам — финал
            break

        except Exception as e:
            attempt += 1
            if attempt > MAX_G4F_RETRIES:
                logging.exception("g4f stream: max retries reached")
                raise
            wait = await _extract_retry_after_seconds(e) or min(G4F_BACKOFF_BASE * (2 ** (attempt - 1)), 60)
            logging.warning("g4f stream error, retry %d/%d after %.1fs: %s",
                            attempt, MAX_G4F_RETRIES, wait, e)
            await asyncio.sleep(wait)

    # сохранить итог в HISTORY
    final_text_joined = "".join(full_parts).strip()
    if final_text_joined:
        remember_assistant(conv_key, final_text_joined)


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

    # === ВАЖНО: требуем упоминание только в группах/супергруппах ===
    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    if is_group and not is_mentioned_or_reply(message):
        logging.info("Пропущено сообщение без упоминания бота или ответа на бота (группа).")
        return
    # В личке (private) — всегда отвечаем

    # --- вспомогательные функции с backoff/троттлингом ---
    max_attempts = 4

    async def safe_edit_to(msg: types.Message, text: str, markdown: bool = False) -> bool:
        attempt = 0
        backoff = 1.0
        text = sanitize_for_tg(text)
        while True:
            try:
                if markdown:
                    # финальный красивый рендер
                    await msg.edit_text(escape_markdown_v2(text), parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await msg.edit_text(text, parse_mode=None)
                return True
            except TelegramRetryAfter as e:
                attempt += 1
                wait = getattr(e, "retry_after", backoff)
                logging.warning("TelegramRetryAfter on edit: waiting %s seconds (attempt %d)", wait, attempt)
                await asyncio.sleep(wait)
                backoff *= 2
                if attempt >= 4:
                    logging.error("Max attempts reached for edit; aborting edit.")
                    return False
            except TelegramBadRequest as e:
                # если и MarkdownV2 не прокатил из-за неэкранированного — упадём в plain
                if markdown:
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

    async def safe_send_reply(text: str):
        attempt = 0
        backoff = 1.0
        text = sanitize_for_tg(text)
        while True:
            try:
                # в стриме только plain
                return await message.reply(text, parse_mode=None)
            except TelegramRetryAfter as e:
                attempt += 1
                wait = getattr(e, "retry_after", backoff)
                logging.warning("TelegramRetryAfter on send: waiting %s seconds (attempt %d)", wait, attempt)
                await asyncio.sleep(wait)
                backoff *= 2
                if attempt >= 4:
                    logging.error("Max attempts reached for send; aborting send.")
                    return None
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                logging.exception("Telegram send error: %s", e)
                return None
            except Exception:
                logging.exception("Unexpected error while sending message")
                return None

    async with _get_chat_lock(message.chat.id):
        try:
            # (опционально) показать "печатает..."
            try:
                await bot.send_chat_action(chat_id=message.chat.id, action="typing")
            except Exception:
                pass

            # Сообщение-заглушка
            sent_msg = await message.reply("⏳ *Печатаю...*")

            # Загрузка системного промта из .txt
            sys_prompt = load_system_prompt_for_chat(message.chat)

            # Параметры троттлинга для стрима
            CHUNK = 4000               # лимит Telegram для Markdown с запасом
            SEND_MIN_CHARS = 100       # минимум новых символов, чтобы делать edit
            SEND_MIN_SECONDS = 1.2     # минимум секунд между edit'ами одного сообщения

            # Попробуем псевдострим от g4f
            try:
                loop = asyncio.get_running_loop()
                monotonic = loop.time

                active_msg = sent_msg        # текущее сообщение, которое редактируем
                current_chunk_text = ""      # текст для текущего сообщения
                last_sent_len = 0            # сколько уже "зафиксировано" в active_msg
                last_edit_ts = monotonic()   # когда в последний раз редактировали

                username = (message.from_user.username or f"{message.from_user.first_name}")

                async for delta in stream_g4f(message.text, username, make_key(message), sys_prompt):
                    if not isinstance(delta, str):
                        continue  # защита от странных типов
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
                        await safe_edit_to(active_msg, current_chunk_text, markdown=False)
                        last_sent_len = len(current_chunk_text)
                        last_edit_ts = now

                # стрим завершился — финальный аккуратный апдейт с Markdown
                if current_chunk_text:
                    await safe_edit_to(active_msg, current_chunk_text, markdown=True)
                else:
                    await safe_send_reply("*Не удалось получить ответ — попробуйте позже*")
                    return

            except Exception:
                logging.exception("Streaming failed")
                await safe_edit_to(active_msg, "*Что-то пошло не так* ⚠️", markdown=True)
                return

        except Exception:
            logging.exception("Ошибка в auto_reply")
            try:
                await safe_edit_to(active_msg, "*Что-то пошло не так* ⚠️", markdown=True)
            except Exception:
                pass


# === Завершение работы ===
async def shutdown():
    try:
        # g4f явного закрытия не требует; оставлено на случай изменений
        if hasattr(g4f_client, "close") and asyncio.iscoroutinefunction(g4f_client.close):
            await g4f_client.close()
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
