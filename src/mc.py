import asyncio
import httpx
import logging
import re
from typing import Tuple

import config
import utils  # avoid linting; we will reimplement small helpers
# we'll reimplement needed small things locally to avoid import cycles

_MC_STATUS_CACHE = {}

def _cache_key(host: str, port: int | None) -> str:
    return f"{host}:{port or 0}"

async def fetch_status(host: str, port: int | None = None) -> dict:
    if not host:
        raise ValueError("Не указан host сервера")
    key = _cache_key(host, port)
    now = asyncio.get_event_loop().time()
    cached = _MC_STATUS_CACHE.get(key)
    if cached and (now - cached[0] < config.MC_CACHE_TTL):
        return cached[1]

    url = f"https://api.mcsrvstat.us/3/{host}"
    if port and port > 0:
        url = f"{url}:{port}"

    attempt = 0
    MAX_OPENAI_RETRIES = 2
    OPENAI_BACKOFF_BASE = 1.5

    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as s:
                r = await s.get(url)
                r.raise_for_status()
                data = r.json()
                _MC_STATUS_CACHE[key] = (now, data)
                return data
        except httpx.HTTPStatusError as e:
            attempt += 1
            if attempt > MAX_OPENAI_RETRIES:
                body = (e.response.text or "")[:300]
                raise RuntimeError(f"MC API HTTP {e.response.status_code}: {body}")
            wait = min(OPENAI_BACKOFF_BASE * (2 ** (attempt - 1)), 10)
            await asyncio.sleep(wait)
        except Exception as e:
            raise RuntimeError(f"Не удалось получить статус сервера: {e}") from e

def format_status_text(host: str, port: int | None, payload: dict) -> str:
    online = bool(payload.get("online"))
    version = payload.get("version") or ""
    players_online = players_max = None
    if isinstance(payload.get("players"), dict):
        players_online = payload["players"].get("online")
        players_max = payload["players"].get("max")
    addr = f"{host}:{port}" if port else host
    motd = ""
    try:
        motd_data = payload.get("motd") or {}
        motd_clean = motd_data.get("clean")
        if isinstance(motd_clean, list):
            motd = "\n".join(motd_clean)
        elif isinstance(motd_clean, str):
            motd = motd_clean
    except Exception:
        pass
    lines = [f"*Статус сервера:* `{addr}`",
             f"Состояние: {'🟢 онлайн' if online else '🔴 оффлайн'}"]
    if version:
        lines.append(f"Версия: `{version}`")
    if players_online is not None and players_max is not None:
        lines.append(f"Игроков: *{players_online}* / *{players_max}*")
    elif players_online is not None:
        lines.append(f"Игроков онлайн: *{players_online}*")
    if motd:
        safe_motd = re.sub(r'([_*`])', r'\\\1', motd)
        lines.append(f"MOTD:\n`{safe_motd}`")
    if not online:
        lines.append("\n_Если сервер должен быть онлайн — попробуйте позже или обратитесь к администраторам._")
    return "\n".join(lines)
