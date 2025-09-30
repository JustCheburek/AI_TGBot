# handlers_helpers.py
import logging
from typing import Tuple

from bot_init import *
import utils
from openai import RateLimitError, APIError
import html_edit
from aiogram.enums import ChatType
import base64
import google.generativeai as genai
import config


HistoryKey = Tuple[int, int]

async def complete_openai(
    prompt: str,
    name: str,
    conv_key: HistoryKey,
    sys_prompt: str,
    rag_ctx: str | None = None,
    message=None,
    *,
    image_bytes: bytes | None = None,
    mime_type: str | None = None
) -> str:
    """Unified completion for text-only and vision inputs.

    - If image_bytes is provided (with image/* mime), sends a vision message (text + image_url).
    - Otherwise sends a plain text message.
    """
    prompt = utils._shorten(prompt)

    chat_id = conv_key[0]
    use_thread = False
    try:
        if message is not None:
            chat_type = getattr(message.chat, "type", None)
            if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
                use_thread = True
    except Exception:
        pass

    if use_thread and message is not None:
        # Чат
        input_with_ctx = await utils.build_input_from_chat_thread(message, prompt, name)
        utils.save_incoming_message(message)
    else:
        # Личка
        input_with_ctx = utils.build_input_with_history(conv_key, prompt, name)
        utils.remember_user(conv_key, prompt)

    if rag_ctx:
        input_with_ctx = f"{rag_ctx}\n\n{input_with_ctx}"

    messages = [{"role": "system", "content": sys_prompt}]

    has_image = False
    data_url = None
    if image_bytes:
        mt = (mime_type or "image/jpeg").strip().lower()
        if not mt.startswith("image/"):
            mt = f"image/{mt}" if "/" not in mt else mt
        try:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            data_url = f"data:{mt};base64,{b64}"
            has_image = True
        except Exception:
            logging.exception("failed to base64 image for vision request")
            return None

    if has_image and data_url:
        user_content = [
            {"type": "text", "text": input_with_ctx},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
    else:
        user_content = input_with_ctx

    messages.append({"role": "user", "content": user_content})

    while True:
        try:
            resp = await openai_client.chat.completions.create(
                model="x-ai/grok-4-fast:free",
                messages=messages,
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
        except (RateLimitError, APIError):
            logging.exception("OpenAI completion rate limit/API error")
            return None


async def transcribe_voice_gemini(audio_bytes: bytes, mime_type: str | None = None) -> str | None:
    """Transcribe voice audio using Google Gemini 2.5 Flash.

    Returns recognized text or None on failure.
    """
    try:
        genai.configure(api_key=config.GOOGLE_API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")
        mt = (mime_type or "audio/ogg").strip().lower()
        # Pass audio bytes directly as a part
        prompt = (
            "Твоя задача — расшифровать русскую речь в обычный текст. "
            "Отдай только распознанный текст без пояснений."
        )
        # Prefer async call if available
        if hasattr(model, "generate_content_async"):
            resp = await model.generate_content_async([
                {"mime_type": mt, "data": audio_bytes},
                prompt,
            ], generation_config={"temperature": 0})
        else:
            # Fallback to sync API in a thread if async is not available
            import asyncio
            loop = asyncio.get_running_loop()
            def _sync_call():
                return model.generate_content([
                    {"mime_type": mt, "data": audio_bytes},
                    prompt,
                ], generation_config={"temperature": 0})
            resp = await loop.run_in_executor(None, _sync_call)
        text = (getattr(resp, "text", None) or "").strip()
        return text or None
    except Exception:
        logging.exception("Gemini ASR failed")
        return None
