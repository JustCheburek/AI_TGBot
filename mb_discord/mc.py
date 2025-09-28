import logging
import httpx
import re
from typing import Optional

from dotenv import load_dotenv
import os

load_dotenv()

MC_SERVER_HOST = os.getenv("MC_SERVER_HOST")

_MC_STATUS_CACHE: dict[str, tuple[float, dict]] = {}
_MC_CACHE_TTL = 20.0


async def fetch_status() -> dict:
    if not MC_SERVER_HOST:
        logging.warning("MC_SERVER_HOST is not set; returning empty status")
        return {}

    import asyncio
    now = asyncio.get_event_loop().time()
    cached = _MC_STATUS_CACHE.get(MC_SERVER_HOST)
    if cached and (now - cached[0] < _MC_CACHE_TTL):
        return cached[1]

    url = f"https://api.mcsrvstat.us/3/{MC_SERVER_HOST}"
    try:
        async with httpx.AsyncClient(timeout=10) as s:
            r = await s.get(url)
            r.raise_for_status()
            data = r.json()
            _MC_STATUS_CACHE[MC_SERVER_HOST] = (now, data)
            return data
    except httpx.HTTPStatusError as e:
        body = (e.response.text or "")[:300]
        logging.exception("MC API HTTP %s: %s", e.response.status_code, body)
        return {}
    except Exception as e:
        logging.exception("MC API request failed: %s", e)
        return {}


def format_status_markdown(payload: dict) -> str:
    online = bool(payload.get("online"))
    version = payload.get("version") or ""
    players_online = players_max = None
    if isinstance(payload.get("players"), dict):
        players_online = payload["players"].get("online")
        players_max = payload["players"].get("max")
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

    lines = [
        "**Статус сервера MineBridge**",
        f"IP: `{MC_SERVER_HOST or 'N/A'}`",
        f"Состояние: {'✅ **онлайн**' if online else '❌ офлайн'}",
    ]
    if version:
        lines.append(f"Версия: `{version}`")
    if players_online is not None and players_max is not None:
        lines.append(f"Игроков: **{players_online}** / **{players_max}**")
    elif players_online is not None:
        lines.append(f"Онлайн: **{players_online}**")
    if motd:
        safe_motd = re.sub(r'([_*`])', r'\\\\\1', motd)
        lines.append(f"`{safe_motd}`")
    return "\n".join(lines)

