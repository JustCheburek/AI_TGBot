# mb_api.py
import asyncio
import logging
import httpx
from typing import Optional, Dict, Any
from urllib.parse import quote_plus
import time
import config
import json

# простое в памяти кэширование: key -> (ts, value)
_MB_CACHE: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}
_MB_CACHE_TTL = 20.0  # seconds, настраиваемо

# параметры повторов/таймаутов (подобно mc.py)
_MAX_RETRIES = 2
_BACKOFF_BASE = 1.5
_HTTP_TIMEOUT = 10.0
_MAX_PLAYER_CHARS = 2000

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
            status = getattr(e.response, "status_code", None)
            if 400 <= (status or 0) < 500 or attempt > _MAX_RETRIES:
                body = (getattr(e.response, "text", "") or "")[:500]
                logger.warning("mb_api: HTTP error %s for %s: %s", status, nick, body)
                return None
            wait = min(_BACKOFF_BASE * (2 ** (attempt - 1)), 10)
            logger.warning("mb_api: retrying HTTP error %s for %s (attempt %d) after %.1fs", status, nick, attempt, wait)
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


async def fetch_player_by_nick(nick: str, use_cache: bool = True) -> Optional[str]:
    """
    Основная функция: принимает ник (строку), возвращает JSON-строку с информацией или None.
    use_cache=True включает кратковременный кэш.
    """
    if not nick:
        return None
    key = f"mb:{nick.lower()}"
    if use_cache:
        cached = _get_cache(key)
        if cached is not None:
            # cached — dict или None; сериализуем как строку перед возвратом
            try:
                player_info = json.dumps(cached, ensure_ascii=False, indent=2)
                if len(player_info) > _MAX_PLAYER_CHARS:
                    player_info = player_info[:_MAX_PLAYER_CHARS] + "\n... (truncated)"
                return player_info
            except Exception:
                # если по какой-то причине сериализация упала, просто вернём None
                logger.exception("mb_api: failed to json.dumps cached value for %s", nick)
                return None

    data = await _fetch_json_from_api(nick)
    if use_cache:
        # сохраняем в кэш original dict (может быть None)
        _set_cache(key, data)

    if data is None:
        return None

    # data — dict, поэтому используем ключи словаря
    try:
        del data["invites"]
        del data["punishments"]
        del data["casesPurchases"]
    except Exception:
        # на случай, если data не тот тип — логируем и продолжаем
        logger.exception("mb_api: unexpected data type for nick %s: %r", nick, type(data))

    player_info = json.dumps(data, ensure_ascii=False, indent=2)

    if len(player_info) > _MAX_PLAYER_CHARS:
        player_info = player_info[:_MAX_PLAYER_CHARS] + "\n... (truncated)"

    return player_info
