# mc.py
import asyncio
import logging
import httpx
import re

import config
import utils

_MC_STATUS_CACHE = {}

async def fetch_status() -> dict:
    now = asyncio.get_event_loop().time()
    cached = _MC_STATUS_CACHE.get(config.MC_SERVER_HOST)
    if cached and (now - cached[0] < config.MC_CACHE_TTL):
        return cached[1]

    url = f"https://api.mcsrvstat.us/3/{config.MC_SERVER_HOST}"

    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as s:
                r = await s.get(url)
                r.raise_for_status()
                data = r.json()
                _MC_STATUS_CACHE[config.MC_SERVER_HOST] = (now, data)
                return data
            
        except httpx.HTTPStatusError as e:
            body = (e.response.text or "")[:300]
            logging.exception(f"MC API HTTP {e.response.status_code}: {body}")
            return {}
        
        except Exception as e:
            logging.exception(f"MC API request failed: {e}")
            return {}

def format_status_text(payload: dict) -> str:
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
    lines = [f"<b>Статус MineBridge</b>",
             f"IP: <code>{config.MC_SERVER_HOST}</code>",
             f"Состояние: {'🟢 <b>ОНЛАЙН</b>' if online else '🔴 оффлайн'}"]
    if version:
        lines.append(f"Версия: <code>{version}</code>")
    if players_online is not None and players_max is not None:
        lines.append(f"Игроков: <b>{players_online}</b> / <b>{players_max}</b>")
    elif players_online is not None:
        lines.append(f"Игроков онлайн: <b>{players_online}</b>")
    if motd:
        safe_motd = re.sub(r'([_*`])', r'\\\1', motd)
        lines.append(f"<code>{safe_motd}</code>")
    return "\n".join(lines)
