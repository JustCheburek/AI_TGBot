# handlers.py
import logging
import time
import re
import httpx

from aiogram import types
from aiogram.filters import Command

from bot_init import *
import config
import utils
import mc
import mb_api
import rag
import handlers_helpers
import msgs

# Проверка подписки пользователя на обязательный канал (использует объект bot)
async def is_subscribed(id: int) -> bool:
    """RU: Проверяет, подписан ли пользователь на обязательный канал."""
    try:
        member = await bot.get_chat_member(chat_id=config.CHANNEL, user_id=id)
        return member.status in ("creator", "administrator", "member", "restricted")
    except Exception:
        logging.exception("Error checking subscription")
        return False

def _build_freeze_keyboard(id: int, hot: bool = True) -> types.InlineKeyboardMarkup:
    """RU: Формирует инлайн-клавиатуру для заморозки/разморозки автоответов."""
    buttons = [
        types.InlineKeyboardButton(text=utils.get_hour_string(hours), callback_data=f"freeze:{id}:{hours}")
        for hours in config.FREEZE_OPTIONS
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    if hot:
        rows.append([types.InlineKeyboardButton(text="🔥 Разморозка 🔥", callback_data=f"unfreeze:{id}")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)

@dp.message(Command("id"))
async def cmd_id(message: types.Message):
    """RU: Ответить ID текущего чата."""
    try:
        chat_id = getattr(message.chat, "id", None)
        if chat_id is None:
            await message.reply("Не удалось определить ID чата")
            return
        await message.reply(f"ID чата: <code>{chat_id}</code>")
    except Exception:
        logging.exception("/id handler failed")
        try:
            await message.reply("Произошла ошибка при получении ID чата")
        except Exception:
            pass

@dp.message(Command("freeze"))
async def cmd_freeze(message: types.Message):
    """RU: Показывает кнопки для включения/отключения временной заморозки автоответов."""
    if not message.from_user:
        return

    id = message.from_user.id

    current_freeze = utils.get_user_freeze(id)
    if current_freeze:
        minites_unfreeze = round((current_freeze - time.time()) / 60)
        current_freeze = f"\n⏳ Текущая заморозка действует ещё <b>{minites_unfreeze} мин</b>"
    else:
        current_freeze = ""

    text_body = f"❄️ Выбери <b>длительность заморозки автоответов</b>" + current_freeze

    await message.reply(text_body, reply_markup=_build_freeze_keyboard(id, hot=bool(current_freeze)))


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """RU: Приветствие и предложение подписаться при необходимости."""
    id = message.from_user.id
    username = (message.from_user.username or f"{message.from_user.first_name}")
    if await is_subscribed(id):
        await message.reply(f"Привет, @{username}!\nМожешь писать мне свои вопросы\nОбращайся ко мне - бриджик")
        return

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Подписаться", url=f"https://t.me/{config.CHANNEL.lstrip('@')}")],
        [types.InlineKeyboardButton(text="Проверить подписку", callback_data="check_subscription")]
    ])
    await message.answer(
        "Для доступа нужен канал @MineBridgeOfficial — подпишитесь и нажмите «<b>Проверить подписку</b>»",
        reply_markup=kb
    )

@dp.message(Command("status"))
# RU: Возвращает текущий статус Minecraft-сервера (через публичное API)
async def cmd_status(message: types.Message):
    msg = await message.reply("🔎 Проверяю статус сервера...")
    try:
        payload = await mc.fetch_status()
        text = mc.format_status_text(payload)
        await msg.edit_text(text)
    except Exception as e:
        await msg.edit_text(f"⚠️ Не удалось получить статус: `{utils._shorten(str(e), 300)}`")

@dp.message(Command("rag_reindex"))
# RU: Пересборка локального RAG-индекса по запросу администратора
async def cmd_rag_reindex(message: types.Message):
    if not config.RAG_ENABLED:
        await message.reply("RAG отключён")
        return
    msg = await message.reply("🔄 <b>Перестраиваю индекс</b>...")
    try:
        global RAG_CHUNKS
        rag.RAG_LOADED = False
        await rag._ensure_rag_index()
        await msg.edit_text(f"✅ <b>Готово</b>\nЧанков: {len(rag.RAG_CHUNKS)}")
    except Exception as e:
        logging.exception("RAG reindex error")
        await msg.edit_text(f"⚠️ Ошибка перестройки: {e}")


@dp.callback_query()
async def callback_any(query: types.CallbackQuery):
    """RU: Обрабатывает коллбеки: freeze/unfreeze и проверку подписки."""
    username = (query.from_user.username or f"{query.from_user.first_name}")
    data = (query.data or "").strip()

    if data.startswith("freeze:"):
        parts = data.split(":")
        if len(parts) != 3:
            await query.answer("Не удалось заморозить", show_alert=True)
            return
        _, id, hours = parts
        if id != str(query.from_user.id):
            await query.answer("Не твоё сообщение!", show_alert=True)
            return
        try:
            id = int(id)
            hours = int(hours)
        except ValueError:
            await query.answer("Недопустимые параметры", show_alert=True)
            return
        if hours not in config.FREEZE_OPTIONS:
            await query.answer("Недопустимая длительность", show_alert=True)
            return

        id = query.from_user.id
        utils.set_user_freeze(id, hours)
        try:
            if query.message:
                await query.message.edit_text(
                    f"🔐 Авто-ответы <b>выключены</b> для <b>{username}</b> на <b>{utils.get_hour_string(hours)}</b>",
                    reply_markup=_build_freeze_keyboard(id),
                )
        except Exception:
            logging.exception("freeze: failed to edit confirmation message")
        await query.answer(f"🔐 Авто-ответы <b>выключены</b> для <b>{username}</b> на <b>{utils.get_hour_string(hours)}</b>")
        return

    if data.startswith("unfreeze:"):
        parts = data.split(":")
        if len(parts) != 2:
            await query.answer("Не удалось разморозить", show_alert=True)
            return
        _, id = parts
        if id != str(query.from_user.id):
            await query.answer("Это не твоё сообщение!", show_alert=True)
            return

        id = query.from_user.id
        utils.clear_user_freeze(id)
        
        try:
            if query.message:
                await query.message.edit_text(
                    f"🔑 Авто-ответы <b>включены</b> для <b>{username}</b>",
                    reply_markup=_build_freeze_keyboard(id, hot=False),
                )
        except Exception:
            logging.exception("unfreeze: failed to edit confirmation message")
        await query.answer(f"🔑 Авто-ответы <b>включены</b> для <b>{username}</b>")
        return

    if data != "check_subscription":
        await query.answer()
        return

    if await is_subscribed(query.from_user.id):
        await query.message.reply(f"Привет, @{username}!\nМожешь писать мне свои вопросы\nОбращайся ко мне - бриджик")
    else:
        await query.message.reply("Подписка не найдена! Убедитесь, что подписаны на канал", show_alert=True)

@dp.message(Command("player"))
 # RU: Команда /player — получить данные игрока по нику (или @username)
async def cmd_player(message: types.Message):
    """/player [nick] — получить данные игрока из MineBridge API.
    Если ник не указан, пробуем использовать Telegram @username отправителя."""
    id = message.from_user.id
    if not await is_subscribed(id):
        await message.reply("Подпишитесь на @MineBridgeOfficial, чтобы пользоваться бриджиком")
        utils.save_incoming_message(message)
        return
    
    text = (message.text or "").strip()
    nick = ""
    try:
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            nick = parts[1].strip()
    except Exception:
        pass

    if not nick:
        nick = (getattr(message.from_user, "username", None) or "").strip()

    if not nick:
        await message.reply("Укажи ник: <code>/player [ник]</code>. Не удалось определить твой ник по @username.")
        return
    msg = await message.reply("🔎 Проверяю игрока...")
    try:
        player_info = await mb_api.fetch_player_by_nick(nick)
        if not player_info:
            await msg.edit_text(f"😕 Игрок <code>{nick}</code> не найден или произошла ошибка API.")
            return
        text = utils.format_player_info(nick, player_info)
        await msg.edit_text(text)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка при запросе: {utils._shorten(str(e), 300)}")

@dp.message()
async def auto_reply(message: types.Message):
    """RU: Автоответ ИИ — отвечает, когда сообщение адресовано боту."""
    incoming_text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    has_photo = bool(getattr(message, "photo", None))
    has_image_doc = bool(getattr(message, "document", None) and str(getattr(message.document, "mime_type", "")).startswith("image/"))
    has_image = has_photo or has_image_doc
    has_voice = bool(getattr(message, "voice", None))

    # Voice transcription (runs even if bot is not addressed)
    if has_voice:
        try:
            try:
                await bot.send_chat_action(chat_id=message.chat.id, action="typing")
            except Exception:
                pass
            fid = message.voice.file_id
            mime = (getattr(message.voice, "mime_type", None) or "audio/ogg")
            fobj = await bot.get_file(fid)
            file_path = getattr(fobj, "file_path", None)
            if not file_path:
                raise RuntimeError("missing voice file_path")
            url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
            timeout = httpx.Timeout(30.0, connect=10.0, read=30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                audio_bytes = resp.content
            incoming_text = await handlers_helpers.transcribe_voice_gemini(audio_bytes, mime)
        except Exception:
            logging.exception("voice transcription flow failed")
    if not incoming_text and not has_image:
        # RU: Сохраняем известные нетекстовые данные (в т.ч. стикеры), но не отвечаем
        try:
            if getattr(message, "sticker", None) is not None:
                utils.save_incoming_sticker(message)
        except Exception:
            pass
        utils.save_incoming_message(message)
        return
    
    id = getattr(message.from_user, "id", None)
    if id is not None and utils.is_user_frozen(id):
        logging.info("Auto replies are temporarily frozen for user %s", id)
        utils.save_incoming_message(message)
        return

    # RU: Надёжно определяем тип чата (aiogram может вернуть enum или строку)
    chat_type = getattr(message.chat, "type", None)
    if isinstance(chat_type, str):
        ct_name = chat_type.upper()
    else:
        # RU: chat_type может быть Enum с .name или чем-то иным
        ct_name = getattr(chat_type, "name", str(chat_type)).upper()
    is_group = ct_name in ("GROUP", "SUPERGROUP")

    if is_group and not utils.should_answer(message, bot_username):
        logging.info("Пропущено (но сохранено) сообщение без упоминания бриджика или ответа на бриджик (группа)")
        utils.save_incoming_message(message)
        return
    
    id = message.from_user.id
    if not await is_subscribed(id):
        await message.reply("Подпишитесь на @MineBridgeOfficial, чтобы пользоваться бриджиком")
        utils.save_incoming_message(message)
        return

    try:
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        except Exception:
            pass
        msg = await message.reply("🖼️ <b>Распознаю изображение...</b>" if has_image else "⏳ <b>Думаю...</b>")
        username = (message.from_user.username or f"{message.from_user.first_name}")
        conv_key = utils.make_key(message)

        sys_prompt = utils.load_system_prompt_for_chat(message.chat)
        sys_prompt += "\n\nПоддерживаются теги [[photo:...]] и [[sticker:...]] (file_id/alias/last)."
        sys_prompt += "\n\nВажно: Используй HTML-разметку для форматирования ответа (<b>, <i>, <code>, <s>, <u>, <pre>). MarkDown НЕЛЬЗЯ! Все ссылки вставляй сразу в текст <a href=""></a>"

        rag_ctx = ""
        try:
            # Получаем RAG контекст (если включён)
            if config.RAG_ENABLED:
                rag_ctx = await rag.build_full_context(incoming_text, username)
        except Exception:
            logging.exception("RAG: failed to build context")

        # call OpenAI: vision for images, plain for text
        if has_image:
            try:
                if message.photo:
                    file_id = message.photo[-1].file_id
                    mime = "image/jpeg"
                else:
                    file_id = message.document.file_id
                    mime = (message.document.mime_type or "image/jpeg")
                fobj = await bot.get_file(file_id)
                file_path = getattr(fobj, "file_path", None)
                if not file_path:
                    raise RuntimeError("missing file_path")
                url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
                timeout = httpx.Timeout(20.0, connect=10.0, read=20.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    image_bytes = resp.content
                answer = await handlers_helpers.complete_openai(
                    incoming_text,
                    username,
                    conv_key,
                    sys_prompt,
                    rag_ctx,
                    message,
                    image_bytes=image_bytes,
                    mime_type=mime,
                )
            except Exception:
                logging.exception("vision flow failed")
                answer = "Не удалось обработать изображение. Попробуй ещё раз прислать фото или добавь подпись."
        else:
            answer = await handlers_helpers.complete_openai(
                incoming_text,
                username,
                conv_key,
                sys_prompt,
                rag_ctx,
                message,
            )

        await msgs.long_text(msg, message, answer)
    except Exception as e:
        logging.exception("Ошибка в auto_reply")
        try:
            await msg.edit_text(f"<b>Что-то пошло не так</b> ⚠️\n{str(e)}")
        except Exception:
            pass
