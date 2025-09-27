from aiogram import types
from aiogram.types import FSInputFile, BufferedInputFile
from pathlib import Path
import asyncio
import logging
import mimetypes
import re
import uuid
from urllib.parse import urlparse, unquote

import httpx

from config import PIXABAY_API_KEY


PHOTO_TAG_RE = re.compile(r"\[\[photo:([^\]]+)\]\]", re.IGNORECASE)
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



def _find_photo_file(name: str) -> Path | None:
    base = (name or "").strip()
    base = re.sub(r"[\\/]+", "", base)
    photos_dir = Path("photos")
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

def _normalise_ext(ext: str | None) -> str:
    if not ext:
        return ""
    ext = ext.lower()
    if ext in {".jpeg", ".jpe"}:
        return ".jpg"
    if ext in {".jfif"}:
        return ".jpg"
    if ext in {".bmp"}:
        return ".jpg"  # телега лучше переварит jpeg как фото
    return ext

def _guess_image_extension(url: str, content_type: str | None) -> str:
    primary = (content_type or "").split(";")[0].strip().lower()
    ext = ""
    if primary:
        ext = mimetypes.guess_extension(primary) or ""
    if not ext:
        path = unquote(urlparse(url).path)
        ext = Path(path).suffix
    ext = _normalise_ext(ext)
    if not ext or ext not in _ALLOWED_IMAGE_EXTS:
        return ".jpg"
    return ext


def _build_image_filename(query: str, url: str, content_type: str | None) -> str:
    ext = _guess_image_extension(url, content_type)
    base = re.sub(r"[^a-z0-9_-]+", "_", (query or "image").strip().lower())
    base = base.strip("_") or "image"
    return f"{base}_{uuid.uuid4().hex[:8]}{ext}"


async def _fetch_pixabay_hits(client: httpx.AsyncClient, query: str) -> list[dict]:
    api_key = (PIXABAY_API_KEY or "").strip()
    if not api_key:
        logging.warning("image search skipped for %s: missing PIXABAY_API_KEY", query)
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
    except httpx.HTTPStatusError as exc:
        logging.warning("image search results failed for %s: %s", query, exc)
        return []
    except Exception as exc:
        logging.warning("image search results failed for %s: %s", query, exc)
        return []

    data = resp.json()
    hits = data.get("hits")
    if not isinstance(hits, list):
        logging.debug("image search: unexpected response payload for %s", query)
        return []
    return hits


def _is_url(s: str) -> bool:
    s = (s or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://")


async def _search_image_online(query: str) -> BufferedInputFile | None:
    q = (query or "").strip()
    if not q:
        return None
    api_key = (PIXABAY_API_KEY or "").strip()
    if not api_key:
        logging.warning("image search skipped for %s: missing PIXABAY_API_KEY", q)
        return None
    try:
        async with httpx.AsyncClient(headers=_IMAGE_HEADERS, timeout=_IMAGE_TIMEOUT, follow_redirects=True) as client:
            for attempt in range(_IMAGE_RESULT_ATTEMPTS):
                hits = await _fetch_pixabay_hits(client, q)
                if not hits:
                    if attempt < _IMAGE_RESULT_ATTEMPTS - 1:
                        await asyncio.sleep(0.5 + attempt * 0.5)
                        continue
                    return None
                for item in hits:
                    image_url = (
                        item.get("largeImageURL")
                        or item.get("fullHDURL")
                        or item.get("imageURL")
                        or item.get("webformatURL")
                        or item.get("previewURL")
                    )
                    if not image_url:
                        continue
                    declared_size = item.get("imageSize")
                    if declared_size:
                        try:
                            if int(declared_size) > _MAX_IMAGE_BYTES:
                                logging.debug("image search: skip %s (declared size %s bytes)", image_url, declared_size)
                                continue
                        except (TypeError, ValueError):
                            pass
                    try:
                        img_resp = await client.get(image_url, headers=_IMAGE_HEADERS)
                        img_resp.raise_for_status()
                        content = img_resp.content
                        if not content:
                            continue
                        if len(content) > _MAX_IMAGE_BYTES:
                            logging.debug("image search: skip %s (downloaded %d bytes)", image_url, len(content))
                            continue
                        filename = _build_image_filename(q, image_url, img_resp.headers.get("Content-Type"))
                        return BufferedInputFile(content, filename=filename)
                    except Exception as exc:
                        logging.debug("image search download failed for %s: %s", image_url, exc)
                        continue
                if attempt < _IMAGE_RESULT_ATTEMPTS - 1:
                    await asyncio.sleep(0.5 + attempt * 0.5)
        return None
    except Exception:
        logging.exception("image search failed for query: %s", q)
    return None





async def _resolve_photo_payload(payload: str) -> str | FSInputFile | BufferedInputFile | None:
    target = (payload or "").strip()
    if not target:
        return None
    if _is_url(target):
        return target
    path = _find_photo_file(target)
    if path is not None:
        return FSInputFile(str(path))
    return await _search_image_online(target)


async def long_text(msg: types.Message, user_msg: types.Message, text: str):
    CHUNK = 4000
    if text is None:
        text = ""

    print(text)

    actions: list[tuple[str, str]] = []
    pos = 0
    for m in PHOTO_TAG_RE.finditer(text):
        if m.start() > pos:
            actions.append(("text", text[pos:m.start()]))
        actions.append(("photo", m.group(1)))
        pos = m.end()
    if pos < len(text):
        actions.append(("text", text[pos:]))

    sent_any_text = False

    async def send_text_blocks(s: str, first_edit: bool):
        nonlocal sent_any_text
        s = s.strip()
        if not s:
            return
        parts = [s[i:i + CHUNK] for i in range(0, len(s), CHUNK)]
        if first_edit:
            try:
                await msg.edit_text(parts[0])
                sent_any_text = True
            except Exception:
                logging.exception("failed to edit initial message with text")
                await user_msg.answer(parts[0])
                sent_any_text = True
            for part in parts[1:]:
                await user_msg.answer(part)
        else:
            for part in parts:
                await user_msg.answer(part)
                sent_any_text = True

    first_text_pending = True
    for kind, payload in actions:
        if kind == "text":
            await send_text_blocks(payload, first_edit=first_text_pending)
            if first_text_pending and payload.strip():
                first_text_pending = False
        elif kind == "photo":
            photo_arg = await _resolve_photo_payload(payload)
            if photo_arg is None:
                logging.warning("photo not found or unsupported: %s", payload)
                continue
            try:
                await user_msg.answer_photo(photo=photo_arg)
            except Exception:
                logging.exception("failed to send photo: %s", payload)

    if first_text_pending:
        try:
            await msg.delete()
        except Exception:
            pass
