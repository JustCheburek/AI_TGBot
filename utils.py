# utils.py
import re
import hashlib
import logging
import asyncio
from pathlib import Path
from typing import Tuple, Deque, Dict, List
from collections import defaultdict, deque

from aiogram import types
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter

import config
from bot_init import *

# ===== History storage =====
HistoryKey = Tuple[int, int]  # (chat_id, user_id)
HISTORY: Dict[HistoryKey, Deque[Tuple[str, str]]] = defaultdict(lambda: deque(maxlen=config.MAX_HISTORY_MESSAGES))

def _shorten(s: str, limit: int = 400) -> str:
    s = (s or "").strip()
    return (s[:limit] + "...") if len(s) > limit else s

def make_key(msg: types.Message) -> HistoryKey:
    return (msg.chat.id, msg.from_user.id)

def remember_user(key: HistoryKey, text: str) -> None:
    HISTORY[key].append(("user", _shorten(text)))

def remember_assistant(key: HistoryKey, text: str) -> None:
    HISTORY[key].append(("assistant", _shorten(text)))

def build_input_with_history(key: HistoryKey, user_text: str, name: str) -> str:
    lines: List[str] = []
    hist = HISTORY.get(key)
    if hist:
        lines.append("Контекст предыдущих сообщений (до 5):")
        for role, text in hist:
            who = "Пользователь" if role == "user" else "Ассистент"
            lines.append(f"{who}: {text}")
        lines.append("—")
    lines.append(f"Пользователь ({name}): {user_text}")
    lines.append("Ассистент:")
    return "\n".join(lines)

def hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]

def read_text_file(p: Path) -> str:
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore")
        if raw.startswith("\ufeff"):
            raw = raw.lstrip("\ufeff")
        return raw.replace("\r\n", "\n").replace("\r", "\n")
    except Exception:
        logging.exception("RAG: failed to read %s", p)
        return ""

def split_chunks(text: str, size: int, ov: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i+size])
        i += max(1, size - ov)
    return [c for c in out if c.strip()]

# system prompt loader
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_PROMPT_CACHE: dict = {}

def _read_txt_prompt(path: Path) -> str:
    mtime = path.stat().st_mtime
    cache_key = str(path)
    cached = _PROMPT_CACHE.get(cache_key)
    if cached and cached[0] == mtime:
        return cached[1]
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff")
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    _PROMPT_CACHE[cache_key] = (mtime, text)
    return text

def load_system_prompt_for_chat(chat: types.Chat) -> str:
    try:
        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            group_path = PROMPTS_DIR / f"{chat.id}.txt"
            if group_path.exists():
                return _read_txt_prompt(group_path)
        default_path = PROMPTS_DIR / "default.txt"
        return _read_txt_prompt(default_path)
    except FileNotFoundError:
        logging.warning("Prompt .txt file not found; using builtin fallback")
    except Exception as e:
        logging.exception("Failed to load .txt prompt: %s", e)
    return "Пиши что я сегодня не смогу помочь, мой системный промт сломался."

# helpers for safe telegram edits/sends
async def safe_edit_to(msg: types.Message, text: str, markdown: bool = True) -> bool:
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
            await asyncio.sleep(wait)
            backoff *= 2
            if attempt >= max_attempts:
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
    max_attempts = 4
    attempt = 0
    backoff = 1.0
    while True:
        try:
            return await base_message.reply(text, parse_mode=ParseMode.MARKDOWN)
        except TelegramRetryAfter as e:
            attempt += 1
            wait = getattr(e, "retry_after", backoff)
            await asyncio.sleep(wait)
            backoff *= 2
            if attempt >= max_attempts:
                return None
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logging.exception("Telegram send error: %s", e)
            return None
        except Exception:
            logging.exception("Unexpected error while sending message")
            return None

async def send_long_text(initial_msg: types.Message, base_message: types.Message, text: str):
    CHUNK = 4000
    if not text:
        await safe_edit_to(initial_msg, "*Не удалось получить ответ — попробуйте позже*")
        return
    parts = [text[i:i+CHUNK] for i in range(0, len(text), CHUNK)] or ["..."]
    await safe_edit_to(initial_msg, parts[0])
    for part in parts[1:]:
        await safe_send_reply(base_message, part)

# intent helpers (copied from original)
STATUS_INTENT_RE = re.compile(
    r'(?i)\b('
    r'статус сервера|сервер онлайн|сервер оффлайн|онлайн сервера|'
    r'сколько игроков|сколько людей на сервере'
    r')\b'
)

def should_answer(message: types.Message, bot_username: str) -> bool:
    text = (message.text or "").strip()
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot:
        return True
    if message.entities and text:
        for entity in message.entities:
            if entity.type == "mention":
                mention_text = text[entity.offset: entity.offset + entity.length]
                if mention_text.lstrip("@").lower() == bot_username:
                    return True
    BOT_ADDRESS_RE = re.compile(r'(?i)(?<!\w)(?:нейро-?бот|бот)(?!\w)')
    QUESTION_MARK_RE = re.compile(r'\?')
    INTERROGATIVE_RE = re.compile(
        r'(?i)\b('
        r'как|почему|зачем|где|когда|сколько|кто|что|какой|какая|какие|чем|куда|откуда|'
        r'можно ли|кто может помочь|кто поможет|подскаж(?:и|ите)|помогите|нужна помощь|help|помощь'
        r')\b'
    )
    COMMAND_RE = re.compile(
        r'(?i)\b('
        r'объясни|расскажи|скажи|подскажи|помоги|проверь|сделай|напиши|создай|найди|покажи|настрой'
        r')\b'
    )
    NOISE_RE = re.compile(r'^\s*(?:[^\w\s]|[\w]{1,2})\s*$')
    if not text or NOISE_RE.match(text):
        return False
    score = 0
    if BOT_ADDRESS_RE.search(text):
        score += 3
    if QUESTION_MARK_RE.search(text):
        score += 1
    if INTERROGATIVE_RE.search(text):
        score += 2
    if COMMAND_RE.search(text):
        score += 1
    if len(text) >= 25:
        score += 1
    return score >= 3

