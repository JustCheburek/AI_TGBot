# ai.py
import os
import logging
from typing import Tuple

import utils
from openai import RateLimitError, APIError
import tghtml
from aiogram.enums import ChatType
from openai import AsyncOpenAI

HistoryKey = Tuple[int, int]
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url="https://openrouter.ai/api/v1")


if not OPENAI_API_KEY:
    raise SystemExit("Set OPENAI_API_KEY in .env")

async def request(user_text: str, name: str, conv_key: HistoryKey, sys_prompt: str, rag_ctx: str | None = None, *, message=None) -> str:
    """RU: Отправляет один запрос к OpenAI (без стрима) и обновляет локальную историю."""
    prompt = (user_text or "").strip()
    if not prompt:
        return ""
    prompt = utils._shorten(prompt)
    # RU: Для групп формируем инпут иначе — подтягиваем контекст треда по необходимости
    chat_id = conv_key[0]
    # RU: message — это aiogram.types.Message; если это группа, используем цепочку reply и не пишем в локальную историю
    use_thread = False
    try:
        if message is not None:
            chat_type = getattr(message.chat, "type", None)
            if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
                use_thread = True
    except Exception:
        pass

    if use_thread and message is not None:
        input_with_ctx = await utils.build_input_from_chat_thread(message, prompt, name)
        utils.save_incoming_message(message)
    else:
        input_with_ctx = utils.build_input_with_history(conv_key, prompt, name)
        utils.remember_user(conv_key, prompt)
        
    if rag_ctx:
        input_with_ctx = f"{rag_ctx}\n\n{input_with_ctx}"
        
    while True:
        try:
            resp = await client.chat.completions.create(
                model="x-ai/grok-4-fast:free",
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": input_with_ctx},
                ],
                temperature=1,
            )
            text = (resp.choices[0].message.content or "").strip()
            text = tghtml.remove(text)
            if text:
                if not use_thread:
                    utils.remember_assistant(conv_key, text)
                else:
                    utils.save_outgoing_message(chat_id, text)
            return text
        except (RateLimitError, APIError) as e:
            logging.exception("OpenAI non-stream rate limit")
            return None
