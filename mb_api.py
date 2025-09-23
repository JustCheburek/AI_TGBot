# mb_api.py
import asyncio
import logging
import httpx
from typing import Optional, Dict, Any
from urllib.parse import quote_plus
import time
import config

# простое в памяти кэширование: key -> (ts, value)
_MB_CACHE: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}
_MB_CACHE_TTL = 20.0  # seconds, настраиваемо

# параметры повторов/таймаутов (подобно mc.py)
_MAX_RETRIES = 2
_BACKOFF_BASE = 1.5
_HTTP_TIMEOUT = 10.0

logger = logging.getLogger(__name__)


def _make_punycode_host(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii")
    except Exception:
        return host

async def _fetch_json_from_api(nick: str) -> Optional[Dict[str, Any]]:
    """Выполнить HTTP GET к API и вернуть JSON-пайлоад или None при ошибке."""
    host = _make_punycode_host(config.MB_HOST)
    nick_esc = quote_plus(nick, safe="")  # экранируем ник в URL
    url = f"https://{host}/api/name/{nick_esc}"

    attempt = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                r = await client.get(url)
                r.raise_for_status()
                try:
                    return r.json()
                except Exception:
                    # Если не JSON — логируем и возвращаем None
                    logger.exception("mb_api: failed to parse JSON for nick %s", nick)
                    return None
        except httpx.HTTPStatusError as e:
            attempt += 1
            # на 4xx обычно повторять бессмысленно
            if 400 <= getattr(e.response, "status_code", 0) < 500 or attempt > _MAX_RETRIES:
                body = (getattr(e.response, "text", "") or "")[:500]
                logger.warning("mb_api: HTTP error %s for %s: %s", getattr(e.response, "status_code", None), nick, body)
                return None
            wait = min(_BACKOFF_BASE * (2 ** (attempt - 1)), 10)
            logger.warning("mb_api: retrying HTTP error %s for %s (attempt %d) after %.1fs", getattr(e.response, "status_code", None), nick, attempt, wait)
            await asyncio.sleep(wait)
        except Exception as e:
            attempt += 1
            if attempt > _MAX_RETRIES:
                logger.exception("mb_api: network error (max retries) for %s: %s", nick, e)
                return None
            wait = min(_BACKOFF_BASE * (2 ** (attempt - 1)), 10)
            logger.warning("mb_api: network error for %s, retry %d after %.1fs: %s", nick, attempt, wait, e)
            await asyncio.sleep(wait)

def _get_cache(key: str) -> Optional[Dict[str, Any]]:
    row = _MB_CACHE.get(key)
    if not row:
        return None
    ts, val = row
    if time.time() - ts > _MB_CACHE_TTL:
        try:
            del _MB_CACHE[key]
        except KeyError:
            pass
        return None
    return val

def _set_cache(key: str, val: Optional[Dict[str, Any]]) -> None:
    _MB_CACHE[key] = (time.time(), val)

async def fetch_player_by_nick(nick: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """
    Основная функция: принимает ник (строку), возвращает распарсенный JSON или None.
    use_cache=True включает кратковременный кэш.
    """
    if not nick:
        return None
    key = f"mb:{nick.lower()}"
    if use_cache:
        cached = _get_cache(key)
        if cached is not None:
            return cached

    data = await _fetch_json_from_api(nick)
    if use_cache:
        _set_cache(key, data)
    return data

