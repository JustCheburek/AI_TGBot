from aiogram import types
import re

async def long_text(msg: types.Message, user_msg: types.Message, text: str):
    CHUNK = 4000
    if not text:
        await msg.edit_text("<b>Не удалось получить ответ — попробуйте позже</b>")
        return
    parts = [text[i:i+CHUNK] for i in range(0, len(text), CHUNK)] or ["..."]
    await msg.edit_text(parts[0])
    for part in parts[1:]:
        await msg.reply(user_msg, part)