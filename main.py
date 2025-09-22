import os
import re
import logging
import asyncio
from dotenv import load_dotenv
import re
from pathlib import Path
import json
import hashlib
import numpy as np
from typing import List
import httpx

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram.enums import ParseMode, ChatType
from aiogram.client.default import DefaultBotProperties

from collections import deque, defaultdict
from typing import Deque, Dict, Tuple

# ==== OpenAI (официальный клиент) ====
from openai import AsyncOpenAI, RateLimitError, APIError

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
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url="https://openrouter.ai/api/v1")

# глобальная переменная для хранения username бота (без @)
bot_username = "minebridge52bot"

# === Контекст N сообщений ===
MAX_HISTORY_MESSAGES = 3  # храним всего N последних сообщений (user/assistant вперемешку)
HistoryKey = Tuple[int, int]  # (chat_id, user_id)
HISTORY: Dict[HistoryKey, Deque[Tuple[str, str]]] = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))

# retry params for OpenAI rate limits
MAX_OPENAI_RETRIES = 2
OPENAI_BACKOFF_BASE = 1.5  # seconds, will use exponential backoff capped below

# === RAG: директория базы знаний и кэш ===
KB_DIR = Path(__file__).resolve().parent / "kb"          # положите сюда .txt/.md файлы
RAG_INDEX_DIR = Path(__file__).resolve().parent / ".rag_cache"
RAG_ENABLED = True

RAG_CHUNK_SIZE = 900
RAG_CHUNK_OVERLAP = 150
RAG_TOP_K = 6
RAG_EMB_MODEL = "jina-embeddings-v3"
RAG_EMB_BATCH = 64

# Глобальные структуры индекса
RAG_CHUNKS: List[dict] = []   # [{id, file, text, mtime}]
RAG_VECS: np.ndarray | None = None
RAG_LOADED = False
RAG_LOCK = asyncio.Lock()

async def _extract_retry_after_seconds(err) -> float | None:
    """Попытаться извлечь время ожидания из ошибки OpenAI (headers/атрибуты/текст)."""
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

    # 3) попытка распарсить сообщение вида "Please try again in 7m12s" или "in 20s"
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
        bot_username = (me.username or "").lower()
        logging.info(f"Bot username: @{bot_username}")
    except Exception:
        logging.exception("Failed to get bot username on startup")

    # RAG: ленивая сборка индекса (если KB пуста — просто пропустится)
    try:
        if RAG_ENABLED:
            await _ensure_rag_index()
    except Exception:
        logging.exception("RAG: failed to ensure index on startup")


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
    return "Пиши что я сегодня не смогу помочь, мой системный промт сломался."


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]

def _read_text_file(p: Path) -> str:
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore")
        if raw.startswith("\ufeff"):
            raw = raw.lstrip("\ufeff")
        return raw.replace("\r\n", "\n").replace("\r", "\n")
    except Exception:
        logging.exception("RAG: failed to read %s", p)
        return ""

def _split_chunks(text: str, size: int = RAG_CHUNK_SIZE, ov: int = RAG_CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i+size])
        i += max(1, size - ov)
    return [c for c in out if c.strip()]

async def _embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Эмбеддинги через Jina API (стабильно и быстро).
    """
    JINA_KEY = os.environ.get("JINA_API_KEY")
    if not JINA_KEY:
        raise RuntimeError("JINA_API_KEY is not set in environment")

    attempt = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=60) as s:
                r = await s.post(
                    "https://api.jina.ai/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {JINA_KEY}",
                        "Accept": "application/json",
                    },
                    json={"model": RAG_EMB_MODEL, "input": texts},
                )
                r.raise_for_status()
                payload = r.json()
                return [item["embedding"] for item in payload["data"]]
        except httpx.HTTPStatusError as e:
            attempt += 1
            if attempt > MAX_OPENAI_RETRIES:
                body = (e.response.text or "")[:500]
                logging.exception("RAG: Jina HTTP %s, body: %s", e.response.status_code, body)
                raise
            wait = min(OPENAI_BACKOFF_BASE * (2 ** (attempt - 1)), 60)
            logging.warning("RAG: Jina HTTP %s, retry %d/%d after %.1fs",
                            e.response.status_code, attempt, MAX_OPENAI_RETRIES, wait)
            await asyncio.sleep(wait)
        except Exception:
            logging.exception("RAG: Jina embeddings request failed")
            raise

async def _ensure_rag_index():
    """Ленивая сборка/обновление индекса RAG. Вызывается на старте и по /rag_reindex."""
    global RAG_CHUNKS, RAG_VECS, RAG_LOADED
    async with RAG_LOCK:
        RAG_INDEX_DIR.mkdir(parents=True, exist_ok=True)
        meta_path = RAG_INDEX_DIR / "chunks.json"
        vecs_path = RAG_INDEX_DIR / "vecs.npy"

        # Загрузим, если есть
        if meta_path.exists() and vecs_path.exists() and not RAG_LOADED:
            try:
                RAG_CHUNKS = json.loads(meta_path.read_text(encoding="utf-8"))
                RAG_VECS = np.load(vecs_path)
                RAG_LOADED = True
                logging.info("RAG: loaded cache with %d chunks", len(RAG_CHUNKS))
            except Exception:
                logging.exception("RAG: failed to load cache, rebuilding")

        # Соберём список актуальных файлов
        kb_files: list[Path] = []
        if KB_DIR.exists():
            for p in KB_DIR.rglob("*"):
                if p.is_file() and p.suffix.lower() in {".txt", ".md"}:
                    kb_files.append(p)

        # Построим карту mtime, чтобы понять, что пересчитывать
        known = {(c["file"], c.get("mtime", 0.0)): True for c in RAG_CHUNKS}
        need_rebuild = False

        # Если индекс пуст или файлов стало больше/изменились — перестроим
        existing_paths = {c["file"] for c in RAG_CHUNKS}
        kb_paths = {str(p) for p in kb_files}

        if not RAG_LOADED or existing_paths != kb_paths:
            need_rebuild = True
        else:
            # Сравним mtime
            for p in kb_files:
                m = p.stat().st_mtime
                if not any(c["file"] == str(p) and abs(c.get("mtime", 0.0) - m) < 1e-6 for c in RAG_CHUNKS):
                    need_rebuild = True
                    break

        if not need_rebuild:
            return  # кэш валиден

        logging.info("RAG: (re)building index...")
        all_chunks: list[dict] = []
        all_texts: list[str] = []

        for p in kb_files:
            txt = _read_text_file(p)
            parts = _split_chunks(txt)
            m = p.stat().st_mtime
            for i, ch in enumerate(parts):
                cid = f"{_hash(str(p))}:{i}"
                all_chunks.append({"id": cid, "file": str(p), "text": ch, "mtime": m})
                all_texts.append(ch)

        # Эмбеддим батчами
        vecs: list[list[float]] = []
        for i in range(0, len(all_texts), RAG_EMB_BATCH):
            batch = all_texts[i:i+RAG_EMB_BATCH]
            vecs.extend(await _embed_batch(batch))

        if vecs:
            V = np.array(vecs, dtype="float32")
            # нормируем для косинуса
            norms = np.linalg.norm(V, axis=1, keepdims=True)
            norms[norms == 0.0] = 1.0
            V /= norms
            RAG_CHUNKS = all_chunks
            RAG_VECS = V
            meta_path.write_text(json.dumps(RAG_CHUNKS, ensure_ascii=False, indent=2), encoding="utf-8")
            np.save(vecs_path, RAG_VECS)
            RAG_LOADED = True
            logging.info("RAG: built %d chunks from %d files", len(RAG_CHUNKS), len(kb_files))
        else:
            RAG_CHUNKS, RAG_VECS, RAG_LOADED = [], None, True
            logging.warning("RAG: no chunks produced (empty kb?)")

async def rag_search(query: str, k: int = RAG_TOP_K) -> list[tuple[dict, float]]:
    if not RAG_ENABLED:
        return []
    await _ensure_rag_index()
    if RAG_VECS is None or len(RAG_CHUNKS) == 0:
        return []
    q_emb = (await _embed_batch([query]))[0]
    q = np.array([q_emb], dtype="float32")
    q /= max(np.linalg.norm(q), 1e-12)
    sims = (RAG_VECS @ q.T).reshape(-1)
    top_idx = np.argsort(-sims)[:k]
    return [(RAG_CHUNKS[i], float(sims[i])) for i in top_idx]

async def rag_build_context(user_query: str, k: int = RAG_TOP_K, max_chars: int = 2000) -> str:
    results = await rag_search(user_query, k=k)
    if not results:
        return ""
    lines = ["Ниже выдержки из базы знаний. Используй их только как справку и не включай служебные индексы/ссылки в ответ."]
    total = 0
    for ch, sc in results:
        snippet = ch["text"].strip()
        if not snippet:
            continue
        if total + len(snippet) > max_chars:
            snippet = snippet[:max(0, max_chars - total)]
        lines.append(snippet)   # <-- БЕЗ [id]
        total += len(snippet)
        if total >= max_chars:
            break
    lines.append("— Конец выдержек —")
    return "\n".join(lines)


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

@dp.message(Command("rag_reindex"))
async def cmd_rag_reindex(message: types.Message):
    if not RAG_ENABLED:
        await message.reply("RAG отключён.")
        return
    sent_msg = await message.reply("🔄 Перестраиваю индекс...")
    try:
        # Принудительная перестройка: чистим флаг загрузки и вызываем ensure
        global RAG_LOADED
        RAG_LOADED = False
        await _ensure_rag_index()
        await safe_edit_to(sent_msg, f"✅ Готово. Чанков: {len(RAG_CHUNKS)}")
    except Exception as e:
        logging.exception("RAG reindex error")
        await safe_edit_to(sent_msg, f"⚠️ Ошибка перестройки: {e}")


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


# === Нормальный (НЕ стримовый) вызов модели с ретраями на rate-limit ===
async def complete_openai_nostream(user_text: str, name: str, conv_key: HistoryKey, sys_prompt: str, rag_ctx: str | None = None) -> str:
    """
    Одноразовый запрос без стрима через chat.completions с бэкоффом и сохранением истории.
    """
    prompt = (user_text or "").strip()
    if not prompt:
        return ""
    prompt = _shorten(prompt)

    # Запомнить реплику пользователя
    remember_user(conv_key, prompt)

    input_with_ctx = build_input_with_history(conv_key, prompt, name)
    if rag_ctx:
        input_with_ctx = f"{rag_ctx}\n\n{input_with_ctx}"

    attempt = 0
    while True:
        try:
            resp = await openai_client.chat.completions.create(
                model="x-ai/grok-4-fast:free",
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": input_with_ctx},
                ],
                temperature=0.5,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                remember_assistant(conv_key, text)
            return text
        except (RateLimitError, APIError) as e:
            attempt += 1
            if attempt > MAX_OPENAI_RETRIES:
                logging.exception("OpenAI non-stream rate limit: max retries reached")
                raise
            wait = await _extract_retry_after_seconds(e) or min(OPENAI_BACKOFF_BASE * (2 ** (attempt - 1)), 60)
            logging.warning("OpenAI non-stream rate-limit/API error, retry %d/%d after %.1fs: %s",
                            attempt, MAX_OPENAI_RETRIES, wait, e)
            await asyncio.sleep(wait)


# === Вспомогательные функции отправки/редактирования ===
async def safe_edit_to(msg: types.Message, text: str, markdown: bool = True) -> bool:
    """Безопасный edit_text с backoff; при проблемах парсинга — пробуем без Markdown."""
    max_attempts = 4
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

async def safe_send_reply(base_message: types.Message, text: str):
    """Отправить reply с backoff (для продолжений)."""
    max_attempts = 4
    attempt = 0
    backoff = 1.0
    while True:
        try:
            return await base_message.reply(text, parse_mode=ParseMode.MARKDOWN)
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

async def send_long_text(initial_msg: types.Message, base_message: types.Message, text: str):
    """
    Отправляет длинный ответ: первый кусок — через edit, остальные — отдельными сообщениями.
    """
    CHUNK = 4000  # с запасом под Markdown
    if not text:
        await safe_edit_to(initial_msg, "*Не удалось получить ответ — попробуйте позже*")
        return

    parts = [text[i:i+CHUNK] for i in range(0, len(text), CHUNK)] or ["..."]

    # Первый кусок — заменяем "печатаю..."
    await safe_edit_to(initial_msg, parts[0])

    # Остальные — отдельными сообщениями-реплаями
    for part in parts[1:]:
        await safe_send_reply(base_message, part)


# === Обработчик сообщений: обычный ответ без стрима ===
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

    # === ВАЖНО: требуем упоминание только в группах/супергруппах ===
    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    if is_group and not is_mentioned_or_reply(message):
        logging.info("Пропущено сообщение без упоминания бота или ответа на бота (группа).")
        return
    # В личке (private) — всегда отвечаем

    if not await is_subscribed(user_id) and user_id != 1087968824:
        print(f"Пользователь {user_id} не подписан")
        await message.reply("Подпишитесь на @MineBridgeOfficial, чтобы пользоваться ботом.")
        return

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
        sys_prompt += "\n\nВАЖНО: В ответе не показывай служебные индексы источников (вида [xxxxxxxxxx:0] или 0d829391f3:0)."

        # RAG: подготовим контекст из kb
        rag_ctx = ""
        try:
            if RAG_ENABLED:
                rag_ctx = await rag_build_context(message.text, k=RAG_TOP_K, max_chars=2000)
        except Exception:
            logging.exception("RAG: failed to build context")

        username = (message.from_user.username or f"{message.from_user.first_name}")
        conv_key = make_key(message)

        # Обычный запрос (без стрима) с ретраями по rate-limit
        answer = await complete_openai_nostream(
            message.text,
            username,
            conv_key,
            sys_prompt,
            rag_ctx=rag_ctx,
        )

        # Отправляем ответ (возможно длинный)
        await send_long_text(sent_msg, message, answer)

    except Exception as e:
        logging.exception("Ошибка в auto_reply")
        try:
            await safe_edit_to(sent_msg, f"*Что-то пошло не так* ⚠️\n{str(e)}")
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
