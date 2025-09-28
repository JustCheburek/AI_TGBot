from __future__ import annotations

import asyncio
import io
import logging
import mimetypes
import random
import re
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

import httpx
import nextcord as dlib

import config as dcfg


PHOTO_TAG_RE = re.compile(r"\[\[photo:([^\]]+)\]\]", re.IGNORECASE)
STICKER_TAG_RE = re.compile(r"\[\[sticker:([^\]]+)\]\]", re.IGNORECASE)
MEDIA_TAG_RE = re.compile(r"\[\[(photo|sticker):([^\]]+)\]\]", re.IGNORECASE)

_MAX_IMAGE_BYTES = 9.5 * 1024 * 1024
_IMAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}
_IMAGE_TIMEOUT = httpx.Timeout(15.0, connect=10.0, read=15.0)
_ALLOWED_IMAGE_EXTS = {".jpg", ".png", ".gif", ".webp"}
_IMAGE_RESULT_ATTEMPTS = 3
_PIXABAY_API_URL = "https://pixabay.com/api/"
_PIXABAY_LANG = "ru"


def _find_photo_file(name: str) -> Optional[Path]:
    base = (name or "").strip()
    base = re.sub(r"[\\/]+", "", base)
    photos_dir = dcfg.PHOTOS_DIR
    if not photos_dir.exists():
        return None
    exts = ["jpg", "jpeg", "png", "webp", "gif"]
    for ext in exts:
        p = photos_dir / f"{base}.{ext}"
        if p.exists():
            return p
    try:
        for p in photos_dir.iterdir():
            if p.is_file() and p.stem.lower() == base.lower():
                return p
    except Exception:
        pass
    return None


def _normalise_ext(ext: Optional[str]) -> str:
    if not ext:
        return ""
    ext = ext.lower()
    if ext in {".jpeg", ".jpe", ".jfif", ".bmp"}:
        return ".jpg"
    return ext


def _guess_image_extension(url: str, content_type: Optional[str]) -> str:
    primary = (content_type or "").split(";")[0].strip().lower()
    ext = mimetypes.guess_extension(primary) or ""
    if not ext:
        path = unquote(urlparse(url).path)
        ext = Path(path).suffix
    ext = _normalise_ext(ext)
    if not ext or ext not in _ALLOWED_IMAGE_EXTS:
        return ".jpg"
    return ext


def _build_image_filename(query: str, url: str, content_type: Optional[str]) -> str:
    ext = _guess_image_extension(url, content_type)
    base = re.sub(r"[^a-z0-9_-]+", "_", (query or "image").strip().lower())
    base = base.strip("_") or "image"
    return f"{base}_{uuid.uuid4().hex[:8]}{ext}"


def _is_url(s: str) -> bool:
    try:
        p = urlparse(s)
        return (p.scheme in {"http", "https"}) and bool(p.netloc)
    except Exception:
        return False


async def _fetch_pixabay_hits(client: httpx.AsyncClient, query: str) -> list[dict]:
    api_key = (dcfg.PIXABAY_API_KEY or "").strip()
    if not api_key:
        logging.debug("image search skipped; missing PIXABAY_API_KEY")
        return []
    params = {
        "key": api_key,
        "q": query,
        "lang": _PIXABAY_LANG,
        "image_type": "photo",
        "safesearch": "true",
        "per_page": 50,
        "order": "popular",
    }
    try:
        resp = await client.get(_PIXABAY_API_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits")
        return hits if isinstance(hits, list) else []
    except Exception:
        logging.debug("pixabay search failed for %s", query)
        return []


async def _search_image_online(q: str) -> Optional[tuple[str, Optional[bytes], str]]:
    # returns (filename, content-bytes-or-None, url)
    try:
        async with httpx.AsyncClient(headers=_IMAGE_HEADERS, timeout=_IMAGE_TIMEOUT) as client:
            for attempt in range(_IMAGE_RESULT_ATTEMPTS):
                hits = await _fetch_pixabay_hits(client, q)
                if not hits:
                    return None
                random.shuffle(hits)
                for h in hits:
                    image_url = (h.get("largeImageURL") or h.get("webformatURL") or "").strip()
                    if not image_url:
                        continue
                    if not _is_url(image_url):
                        continue
                    try:
                        img_resp = await client.get(image_url)
                        img_resp.raise_for_status()
                        content = img_resp.content
                        if not content:
                            continue
                        if len(content) > _MAX_IMAGE_BYTES:
                            continue
                        filename = _build_image_filename(q, image_url, img_resp.headers.get("Content-Type"))
                        return (filename, content, image_url)
                    except Exception:
                        continue
                if attempt < _IMAGE_RESULT_ATTEMPTS - 1:
                    await asyncio.sleep(0.5 + attempt * 0.5)
        return None
    except Exception:
        logging.exception("image search failed for query: %s", q)
        return None


async def _resolve_photo_payload(payload: str):
    target = (payload or "").strip()
    if not target:
        return None
    if _is_url(target):
        return (None, None, target)
    path = _find_photo_file(target)
    if path is not None:
        try:
            data = path.read_bytes()
            return (path.name, data, None)
        except Exception:
            logging.exception("failed to read local photo: %s", path)
            return None
    return await _search_image_online(target)


async def long_text(channel: dlib.abc.Messageable, text: str):
    """Sends long text with support for [[photo:...]] and [[sticker:...]]."""
    CHUNK = 1900
    text = text or ""

    actions: list[tuple[str, str]] = []
    pos = 0
    for m in MEDIA_TAG_RE.finditer(text):
        if m.start() > pos:
            actions.append(("text", text[pos:m.start()]))
        kind = m.group(1).lower()
        payload = m.group(2)
        if kind == "photo":
            actions.append(("photo", payload))
        elif kind == "sticker":
            actions.append(("sticker", payload))
        pos = m.end()
    if pos < len(text):
        actions.append(("text", text[pos:]))

    async def send_text_blocks(s: str):
        s = s.strip()
        if not s:
            return
        parts = [s[i:i + CHUNK] for i in range(0, len(s), CHUNK)]
        for part in parts:
            await channel.send(part)

    for kind, payload in actions:
        if kind == "text":
            await send_text_blocks(payload)
        elif kind == "photo":
            try:
                photo = await _resolve_photo_payload(payload)
                if not photo:
                    continue
                filename, data, url = photo
                if data and filename:
                    await channel.send(file=dlib.File(io.BytesIO(data), filename=filename))
                elif url:
                    # Let Discord embed the image URL
                    await channel.send(url)
            except Exception:
                logging.exception("failed to send photo: %s", payload)
        elif kind == "sticker":
            # Discord stickers require guild assets; send plain text fallback
            await channel.send(f"[sticker: {payload}]")
