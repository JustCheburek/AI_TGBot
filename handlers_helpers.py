# handlers_helpers.py
import logging
from typing import Tuple

from bot_init import *
import utils
from openai import RateLimitError, APIError
import html_edit
from aiogram.enums import ChatType
import base64


HistoryKey = Tuple[int, int]

async def complete_openai_nostream(user_text: str, name: str, conv_key: HistoryKey, sys_prompt: str, rag_ctx: str | None = None, *, message=None) -> str:
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


async def complete_openai_vision(
    image_bytes: bytes,
    mime_type: str | None,
    user_text: str,
    name: str,
    conv_key: HistoryKey,
    sys_prompt: str,
    rag_ctx: str | None = None,
    *,
    message=None,
) -> str:
    """RU: Вызов OpenAI (мультимодальный): анализ изображения + текст запроса.

    Использует тот же модельный endpoint, что и для текста, но с контент-частями
    (text + image_url data: URI).
    """
    prompt = (user_text or "").strip()
    # допускаем пустую подпись: тогда попросим описать изображение
    if not prompt:
        prompt = "Опиши изображение. Если на нем есть текст — распознай и процитируй."

    # В группах используем краткий лог диалога, в ЛС — персональную историю
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

    # data: URL для изображения
    try:
        b64 = base64.b64encode(image_bytes).decode("ascii")
    except Exception:
        logging.exception("failed to base64 image for vision request")
        return None
    mt = (mime_type or "image/jpeg").strip().lower()
    data_url = f"data:{mt};base64,{b64}"

    while True:
        try:
            resp = await openai_client.chat.completions.create(
                model="x-ai/grok-4-fast:free",
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": input_with_ctx},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
                temperature=1,
            )
            text = (resp.choices[0].message.content or "").strip()
            text = html_edit.remove(text)
            if text:
                if not use_thread:
                    utils.remember_assistant(conv_key, text)
                else:
                    chat_id = conv_key[0]
                    utils.save_outgoing_message(chat_id, text)
            return text
        except (RateLimitError, APIError):
            logging.exception("OpenAI vision rate limit/API error")
            return None
