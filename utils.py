# utils.py
import re
import hashlib
import logging
from pathlib import Path
from typing import Tuple, Deque, Dict, List
from collections import defaultdict, deque
import time
from typing import Dict, Optional

from aiogram import types
from aiogram.enums import ChatType

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
            group_path = config.PROMPTS_DIR / f"{chat.id}.txt"
            if group_path.exists():
                return _read_txt_prompt(group_path)
        default_path = config.PROMPTS_DIR / "default.txt"
        return _read_txt_prompt(default_path)
    except FileNotFoundError:
        logging.warning("Prompt .txt file not found; using builtin fallback")
    except Exception as e:
        logging.exception("Failed to load .txt prompt: %s", e)
    return "Пиши что я сегодня не смогу помочь, мой системный промт сломался."

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
        score += 4
    if QUESTION_MARK_RE.search(text):
        score += 2
    if INTERROGATIVE_RE.search(text):
        score += 2
    if COMMAND_RE.search(text):
        score += 1
    if len(text) >= 25:
        score += 1
    return score >= 4

_USER_FREEZES: Dict[int, float] = {}

def _cleanup_freezes(now: Optional[float] = None) -> None:
    if now is None:
        now = time.time()
    expired = [uid for uid, ts in _USER_FREEZES.items() if ts <= now]
    for uid in expired:
        _USER_FREEZES.pop(uid, None)

def set_user_freeze(user_id: int, hours: int) -> float:
    expires_at = time.time() + hours * 3600
    _USER_FREEZES[user_id] = expires_at
    return expires_at

def clear_user_freeze(user_id: int) -> bool:
    return _USER_FREEZES.pop(user_id, None) is not None

def get_user_freeze(user_id: int) -> Optional[float]:
    _cleanup_freezes()
    expires_at = _USER_FREEZES.get(user_id)
    if expires_at is None:
        return None
    if expires_at <= time.time():
        _USER_FREEZES.pop(user_id, None)
        return None
    return expires_at

def is_user_frozen(user_id: int) -> bool:
    return get_user_freeze(user_id) is not None


def get_hour_string(hours: int) -> str:
    return f"{hours} час" if hours == 1 else f"{hours} часа"