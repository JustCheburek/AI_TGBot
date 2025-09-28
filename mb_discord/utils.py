from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from html import escape
from typing import Deque, Dict, List, Optional, Tuple

import nextcord as dlib

import config as dcfg


# Shortening helper
def shorten(s: str, limit: int = 400) -> str:
    s = (s or "").strip()
    return (s[:limit] + "...") if len(s) > limit else s


# History per user in DMs (or per channel+user)
HistoryKey = Tuple[int, int]  # (channel_id, user_id)
HISTORY: Dict[HistoryKey, Deque[Tuple[str, str]]] = defaultdict(lambda: deque(maxlen=dcfg.DM_MAX_MESSAGES))


def make_key(msg: dlib.Message) -> HistoryKey:
    return (msg.channel.id, msg.author.id)


def remember_user(key: HistoryKey, text: str) -> None:
    HISTORY[key].append(("user", shorten(text)))


def remember_assistant(key: HistoryKey, text: str) -> None:
    HISTORY[key].append(("assistant", shorten(text)))


def build_input_with_history(key: HistoryKey, user_text: str, name: str) -> str:
    lines: List[str] = []
    hist = HISTORY.get(key)
    if hist:
        lines.append(f"История общения (последние {dcfg.DM_MAX_MESSAGES}):")
        for role, text in hist:
            who = "Пользователь" if role == "user" else "Ассистент"
            lines.append(f"{who}: {text}")
        lines.append("Конец истории")
    lines.append(f"Пользователь ({name}): {user_text}")
    lines.append("Ассистент:")
    return "\n".join(lines)


# Per-channel raw logs for group context
ChatLine = Tuple[str, bool, str]
CHAT_LOGS: Dict[int, Deque[ChatLine]] = defaultdict(lambda: deque(maxlen=dcfg.GROUP_MAX_MESSAGES))


def author_name(u: dlib.abc.User) -> str:
    if isinstance(u, dlib.Member):
        return (u.nick or u.name or "Гость").strip()
    return (u.name or "Гость").strip()


def save_incoming_message(msg: dlib.Message) -> None:
    text = (msg.content or "").strip()
    if not text:
        return
    CHAT_LOGS[msg.channel.id].append((author_name(msg.author), bool(getattr(msg.author, "bot", False)), text))


def save_outgoing_message(channel_id: int, text: str) -> None:
    CHAT_LOGS[channel_id].append(("Ассистент", True, shorten(text)))


def build_input_from_channel_context(msg: dlib.Message, user_text: str, name: str) -> str:
    """Builds input text using recent channel messages (for group chats)."""
    lines: List[str] = []
    logs = list(CHAT_LOGS.get(msg.channel.id) or [])
    if logs:
        lines.append(f"Контекст канала (последние {len(logs)} сообщений):")
        for author, is_bot, text in logs:
            who = "Ассистент" if is_bot else author
            lines.append(f"{who}: {text}")
        lines.append("Конец контекста")
    lines.append(f"Пользователь ({name}): {user_text}")
    lines.append("Ассистент:")
    return "\n".join(lines)


# Heuristics for replying in guild channels
BOT_ADDRESS_RE = re.compile(r'(?i)(?<!\w)(?:нейро-?бот(?:ик|яра)?|бот(?:ик|яра)?|бридж(?:ик)?)(?!\w)')
QUESTION_MARK_RE = re.compile(r"\?")
INTERROGATIVE_RE = re.compile(r"(?i)\b(кто|что|где|когда|почему|зачем|как|можно|покажи|help|помоги|статус|игрок|player)\b")
COMMAND_RE = re.compile(r"(?i)\b(freeze|unfreeze|status|player|rag|mostiki|help)\b")
NOISE_RE = re.compile(r"^\s*(?:[^\w\s]|[\w]{1,2})\s*$")


def should_answer_discord(msg: dlib.Message, bot_user: dlib.ClientUser) -> bool:
    text = (msg.content or "").strip()
    if not text or NOISE_RE.match(text):
        return False
    # Direct mention wins
    if bot_user in msg.mentions:
        return True
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


# Freeze state
_USER_FREEZES: Dict[int, float] = {}


def _cleanup_freezes(now: Optional[float] = None) -> None:
    if now is None:
        now = time.time()
    expired = [uid for uid, ts in _USER_FREEZES.items() if ts <= now]
    for uid in expired:
        _USER_FREEZES.pop(uid, None)


def set_user_freeze(user_id: int, hours: int) -> float:
    expires_at = time.time() + max(0, hours) * 3600
    _USER_FREEZES[user_id] = expires_at
    return expires_at


def clear_user_freeze(user_id: int) -> bool:
    return _USER_FREEZES.pop(user_id, None) is not None


def get_user_freeze(user_id: int) -> Optional[float]:
    _cleanup_freezes()
    ts = _USER_FREEZES.get(user_id)
    if ts is None:
        return None
    if ts <= time.time():
        _USER_FREEZES.pop(user_id, None)
        return None
    return ts


def is_user_frozen(user_id: int) -> bool:
    return get_user_freeze(user_id) is not None


def get_hour_string(hours: int) -> str:
    return f"{hours} час" if hours == 1 else f"{hours} часа" if hours in (2, 3, 4) else f"{hours} часов"


def format_player_info_md(nick: str, info: dict) -> str:
    lines = [f"**Игрок** `{escape(str(nick))}`:"]
    for key, value in info.items():
        if key == "Роли":
            if isinstance(value, list):
                roles_lines = "\n".join(f"- {escape(str(r))}" for r in value)
                lines.append(f"{escape(key)}:\n{roles_lines}")
            else:
                lines.append(f"{escape(key)}: `{escape(str(value))}`")
        else:
            lines.append(f"{escape(key)}: `{escape(str(value))}`")
    return "\n".join(lines)
