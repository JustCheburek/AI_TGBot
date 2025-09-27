from aiogram import types
from aiogram.types import FSInputFile
from pathlib import Path
import logging
import re


PHOTO_TAG_RE = re.compile(r"\[\[photo:([^\]]+)\]\]", re.IGNORECASE)

def _find_photo_file(name: str) -> Path | None:
    base = (name or "").strip()
    # avoid path traversal
    base = re.sub(r"[\\/]+", "", base)
    photos_dir = Path("photos")
    if not photos_dir.exists():
        return None
    exts = ["jpg", "jpeg", "png", "webp", "gif"]
    for ext in exts:
        p = photos_dir / f"{base}.{ext}"
        if p.exists():
            return p
    # fallback: case-insensitive scan
    try:
        for p in photos_dir.iterdir():
            if p.is_file():
                if p.stem.lower() == base.lower():
                    return p
    except Exception:
        pass
    return None


async def long_text(msg: types.Message, user_msg: types.Message, text: str):
    CHUNK = 4000
    if text is None:
        text = ""

    print(text)

    # Build sequence of actions: (kind, payload)
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

    def _is_url(s: str) -> bool:
        s = s.strip().lower()
        return s.startswith("http://") or s.startswith("https://")

    first_text_pending = True
    for kind, payload in actions:
        if kind == "text":
            await send_text_blocks(payload, first_edit=first_text_pending)
            if first_text_pending and payload.strip():
                first_text_pending = False
        elif kind == "photo":
            photo_arg: str | FSInputFile | None = None
            if _is_url(payload):
                photo_arg = payload.strip()
            else:
                path = _find_photo_file(payload)
                if path is not None:
                    photo_arg = FSInputFile(str(path))
            if photo_arg is None:
                logging.warning("photo not found or unsupported: %s", payload)
                continue
            try:
                await user_msg.answer_photo(photo=photo_arg)
            except Exception:
                logging.exception("failed to send photo: %s", payload)

    # If there was no text at all, try to delete the placeholder message
    if first_text_pending:
        try:
            await msg.delete()
        except Exception:
            pass
