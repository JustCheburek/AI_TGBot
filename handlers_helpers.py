# handlers_helpers.py
import logging
from typing import Tuple

from bot_init import *
import utils
from openai import RateLimitError, APIError
import html_edit
from aiogram.enums import ChatType


HistoryKey = Tuple[int, int]

async def _extract_retry_after_seconds(err) -> float | None:
    try:
        headers = getattr(err, "headers", None) or {}
        if headers:
            ra = headers.get("retry-after") or headers.get("Retry-After")
            if ra:
                try:
                    return float(ra)
                except Exception:
                    pass
    except Exception:
        pass
    try:
        ra = getattr(err, "retry_after", None)
        if ra is not None:
            return float(ra)
    except Exception:
        pass
    try:
        import re
        msg = str(err)
        m = re.search(r'(\d+)\s*m(?:in)?\s*(\d+)\s*s', msg)
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
        m2 = re.search(r'in\s*(\d+)\s*s', msg)
        if m2:
            return int(m2.group(1))
        m3 = re.search(r'(\d+)\s*seconds', msg)
        if m3:
            return int(m3.group(1))
    except Exception:
        pass
    return None

async def complete_openai_nostream(user_text: str, name: str, conv_key: HistoryKey, sys_prompt: str, rag_ctx: str | None = None, *, message=None) -> str:
    prompt = (user_text or "").strip()
    if not prompt:
        return ""
    prompt = utils._shorten(prompt)
    # Build input differently for group chats: fetch context from chat thread on demand
    chat_id = conv_key[0]
    # message is aiogram.types.Message; if provided and chat is group, use reply chain, skip local history
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
            resp = await openai_client.chat.completions.create(
                model="x-ai/grok-4-fast:free",
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": input_with_ctx},
                ],
                temperature=1,
            )
            text = (resp.choices[0].message.content or "").strip()
            text = html_edit.remove(text)
            if text:
                if not use_thread:
                    utils.remember_assistant(conv_key, text)
                else:
                    utils.save_outgoing_message(chat_id, text)
            return text
        except (RateLimitError, APIError) as e:
            logging.exception("OpenAI non-stream rate limit")
            return None