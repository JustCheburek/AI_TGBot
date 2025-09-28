# utils.py
from html import escape
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

# ===== Per-user short history (диалоги пользователь↔ассистент) =====
HistoryKey = Tuple[int, int]  # (chat_id, user_id)
HISTORY: Dict[HistoryKey, Deque[Tuple[str, str]]] = defaultdict(
    lambda: deque(maxlen=config.DM_MAX_MESSAGES)
)

# ===== Per-chat raw history (последние сообщения чата) =====
# Храним только необходимые поля, чтобы не тащить целый Message.
# (author, is_bot, text)
ChatLine = Tuple[str, bool, str]
CHAT_LOGS: Dict[int, Deque[ChatLine]] = defaultdict(
    lambda: deque(maxlen=config.GROUP_MAX_MESSAGES)
)
STICKER_LOGS: Dict[int, Deque[str]] = defaultdict(lambda: deque(maxlen=50))
_USER_FREEZES: Dict[int, float] = {}

# ===== Generic helpers reused by TG and DS =====
def shorten(s: str, limit: int = 400) -> str:
    """Public helper to shorten text; reuses internal _shorten."""
    return _shorten(s, limit)


def _shorten(s: str, limit: int = 400) -> str:
    """RU: Обрезает пробелы и длинные строки, добавляя многоточие."""
    s = (s or "").strip()
    return (s[:limit] + "...") if len(s) > limit else s

def make_key(msg: types.Message) -> HistoryKey:
    """RU: Формирует ключ истории на основе chat_id и user_id."""
    return (msg.chat.id, msg.from_user.id)

def remember_user(key: HistoryKey, text: str) -> None:
    """RU: Сохраняет краткую версию последнего сообщения пользователя."""
    HISTORY[key].append(("user", _shorten(text)))

def remember_assistant(key: HistoryKey, text: str) -> None:
    """RU: Сохраняет краткий ответ ассистента для контекста."""
    HISTORY[key].append(("assistant", _shorten(text)))

def build_input_with_history(key: HistoryKey, user_text: str, name: str) -> str:
    """RU: Собирает короткую историю чата вместе с новым текстом пользователя."""
    lines: List[str] = []
    hist = HISTORY.get(key)
    if hist:
        lines.append(f"Контекст предыдущих сообщений (до {config.DM_MAX_MESSAGES}):")
        for role, text in hist:
            who = "Пользователь" if role == "user" else "Ассистент"
            lines.append(f"{who}: {text}")
        lines.append("Конец контекста")
    lines.append(f"Пользователь ({name}): {user_text}")
    lines.append("Ассистент:")
    return "\n".join(lines)

def build_input_from_logs(chat_or_channel_id: int, user_text: str, name: str) -> str:
    """Build input using recent shared CHAT_LOGS (works for TG and DS)."""
    lines: List[str] = []
    thread: List[ChatLine] = list(CHAT_LOGS.get(chat_or_channel_id, deque()))[-config.GROUP_MAX_MESSAGES:]
    if thread:
        lines.append("Недавний разговор (последние сообщения):")
        for author, is_bot, text in thread:
            if not text:
                continue
            who = "Помощник" if is_bot else author
            lines.append(f"{who}: {text}")
        lines.append("Конец контекста")
    lines.append(f"Пользователь ({name}): {user_text}")
    lines.append("Ответ:")
    return "\n".join(lines)

def save_chat_line(chat_or_channel_id: int, author: str, text: str, is_bot: bool = False) -> None:
    """Append a line to CHAT_LOGS generically (platform-agnostic)."""
    t = (text or "").strip()
    if not t:
        return
    CHAT_LOGS[chat_or_channel_id].append(((author or ""), bool(is_bot), _shorten(t)))

# ====== СОХРАНЕНИЕ СООБЩЕНИЙ ЧАТА ======

def _author_from(msg: types.Message) -> str:
    """RU: Получает отображаемое имя автора из входящего сообщения."""
    user = getattr(msg, "from_user", None)
    if not user:
        return "неизвестно"
    return (getattr(user, "username", None) or getattr(user, "first_name", "") or "безымянный")

def save_incoming_message(message: types.Message) -> None:
    """RU: Записывает сообщение пользователя в буфер транскрипта чата."""
    chat_id = message.chat.id
    text = (message.text or "").strip()
    author = _author_from(message)
    is_bot = bool(getattr(message.from_user, "is_bot", False))
    if not text:
        if message.sticker:
            text = f"Стикер: {message.sticker.file_id}"
        elif message.photo:
            text = f"Фото: {message.photo[-1].file_id}"
        elif message.document:
            text = f"Документ: {message.document.file_id}"
        elif message.voice:
            text = f"Голосовое сообщение: {message.voice.file_id}"
        elif message.video:
            text = f"Видео: {message.video.file_id}"
        elif message.audio:
            text = f"Аудио: {message.audio.file_id}"
        else:
            return
    CHAT_LOGS[chat_id].append((author, is_bot, _shorten(text)))

def save_incoming_sticker(message: types.Message) -> None:
    """RU: Сохраняет file_id присланного стикера для дальнейшего копирования."""
    try:
        st = getattr(message, "sticker", None)
        if not st:
            return
        fid = getattr(st, "file_id", None)
        if not fid:
            return
        chat_id = message.chat.id
        STICKER_LOGS[chat_id].append(fid)
    except Exception:
        logging.exception("failed to save incoming sticker")

def get_last_sticker(chat_id: int) -> Optional[str]:
    """RU: Возвращает последний известный стикер file_id для чата (если есть)."""
    dq = STICKER_LOGS.get(chat_id)
    if not dq:
        return None
    try:
        return dq[-1] if len(dq) else None
    except Exception:
        return None

def save_outgoing_message(chat_id: int, text: str, bot_display_name: str = "Ассистент") -> None:
    """Track what the bot answered so the transcript stays balanced."""
    if not text:
        return
    CHAT_LOGS[chat_id].append((bot_display_name, True, _shorten(text)))

async def build_input_from_chat_thread(
    message: types.Message,
    user_text: str,
    name: str
) -> str:
    # RU: Формирует вход для LLM из треда чата на основе последних сообщений
    """
    Формируем контекст из последних max_messages сообщений чата,
    сохранённых локально в CHAT_LOGS (а не через reply_to_message и не через get_chat_history).
    """
    lines: List[str] = []
    chat_id = message.chat.id

    # Берём последние max_messages сохранённых записей
    thread: List[ChatLine] = list(CHAT_LOGS.get(chat_id, deque()))[-config.GROUP_MAX_MESSAGES:]

    if thread:
        lines.append("Контекст беседы среди разных игроков:")
        for author, is_bot, text in thread:
            if not text:
                continue
            role = "Ассистент" if is_bot else author
            lines.append(f"{role}: {text}")
        lines.append("Конец контекста")

    lines.append(f"Пользователь ({name}): {user_text}")
    lines.append("Ассистент:")
    return "\n".join(lines)

def hash(s: str) -> str:
    """Helper that keeps short, deterministic hashes for filenames and IDs."""
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]

# system prompt loader
_PROMPT_CACHE: dict = {}

def _read_txt_prompt(path: Path) -> str:
    """Cache-aware reader for prompt override files stored on disk."""
    mtime = path.stat().st_mtime
    cache_key = str(path)
    cached = _PROMPT_CACHE.get(cache_key)
    if cached and cached[0] == mtime:
        return cached[1]
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff")
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    
    text += "\n\nПоддерживаются теги [[photo:...]] и [[sticker:...]] (file_id/alias/last)."
    text += "\n\nВажно: Используй HTML-разметку для форматирования ответа (<b>, <i>, <code>, <s>, <u>, <pre>). MarkDown НЕЛЬЗЯ! Все ссылки вставляй сразу в текст <a href=""></a>"

    _PROMPT_CACHE[cache_key] = (mtime, text)

    return text

def load_system_prompt_for_chat(chat: types.Chat) -> str:
    """Load chat-specific system prompt text, falling back to default file."""
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
    """RU: Эвристически решает, нужно ли боту отвечать автоматически."""
    text = (message.text or "").strip()
    # RU: Если это reply — реагируем только если ответ адресован нашему боту
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot:
        replied_username = (getattr(message.reply_to_message.from_user, "username", "") or "").lower()
        if replied_username == (bot_username or "").lower():
            return True
        # Reply to a different bot — do not trigger autoreply
        return False
    if message.entities and text:
        for entity in message.entities:
            if entity.type == "mention":
                mention_text = text[entity.offset: entity.offset + entity.length]
                if mention_text.lstrip("@").lower() == bot_username:
                    return True
    BOT_ADDRESS_RE = re.compile(r'(?i)(?<!\w)(?:нейро-?бот(?:ик|яра)?|бот(?:ик|яра)?|бридж(?:ик)?)(?!\w)')
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

def _cleanup_freezes(now: Optional[float] = None) -> None:
    """RU: Удаляет истёкшие записи заморозки, поддерживая кэш в актуальном состоянии."""
    if now is None:
        now = time.time()
    expired = [uid for uid, ts in _USER_FREEZES.items() if ts <= now]
    for uid in expired:
        _USER_FREEZES.pop(uid, None)

def set_user_freeze(user_id: int, hours: int) -> float:
    """RU: Включает заморозку автоответов для пользователя на указанное число часов."""
    expires_at = time.time() + hours * 3600
    _USER_FREEZES[user_id] = expires_at
    return expires_at

def clear_user_freeze(user_id: int) -> bool:
    """RU: Снимает заморозку, если она была; возвращает факт изменения."""
    return _USER_FREEZES.pop(user_id, None) is not None

def get_user_freeze(user_id: int) -> Optional[float]:
    """RU: Возвращает UNIX-время окончания заморозки (или None)."""
    _cleanup_freezes()
    expires_at = _USER_FREEZES.get(user_id)
    if expires_at is None:
        return None
    if expires_at <= time.time():
        _USER_FREEZES.pop(user_id, None)
        return None
    return expires_at

def is_user_frozen(user_id: int) -> bool:
    """RU: Проверяет, есть ли у пользователя активная заморозка."""
    return get_user_freeze(user_id) is not None


def get_hour_string(hours: int) -> str:
    """RU: Форматирует количество часов человекочитаемой строкой."""
    return f"{hours} час" if hours == 1 else f"{hours} часа"


def format_player_info(nick: str, info: dict) -> str:
    """RU: Форматирует профиль MineBridge в безопасный для Telegram HTML."""
    # Порядок полей
    lines = [f"<b>Игрок</b> <code>{escape(str(nick))}</code>:"]

    for key, value in info.items():
        if key == "Роли":
            roles_lines = "\n".join(f"• {escape(str(r))}" for r in value)
            lines.append(f"{escape(key)}:\n{roles_lines}")

        else:
            # прочие простые поля и ссылки
            lines.append(f"{escape(key)}: <code>{escape(str(value))}</code>")

    return "\n".join(lines)
