# handlers.py
import logging
import time
import re

from aiogram import types
from aiogram.filters import Command

from bot_init import *
import config
import utils
import mc
import rag
import handlers_helpers
import msgs

# is_subscribed implementation (uses bot)
async def is_subscribed(id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=config.CHANNEL, user_id=id)
        return member.status in ("creator", "administrator", "member", "restricted")
    except Exception:
        logging.exception("Error checking subscription")
        return False

def _build_freeze_keyboard(id: int, hot: bool = True) -> types.InlineKeyboardMarkup:
    buttons = [
        types.InlineKeyboardButton(text=utils.get_hour_string(hours), callback_data=f"freeze:{id}:{hours}")
        for hours in config.FREEZE_OPTIONS
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    if hot:
        rows.append([types.InlineKeyboardButton(text="🔥 Разморозка 🔥", callback_data=f"unfreeze:{id}")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)

@dp.message(Command("freeze"))
async def cmd_freeze(message: types.Message):
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
    id = message.from_user.id
    username = (message.from_user.username or f"{message.from_user.first_name}")
    if await is_subscribed(id):
        await message.reply(f"Привет, @{username}!\nМожешь писать мне свои вопросы\nОбращайся ко мне - нейробот или бот")
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
async def cmd_status(message: types.Message):
    msg = await message.reply("🔎 Проверяю статус сервера...")
    try:
        payload = await mc.fetch_status()
        text = mc.format_status_text(payload)
        await msg.edit_text(text)
    except Exception as e:
        await msg.edit_text(f"⚠️ Не удалось получить статус: `{utils._shorten(str(e), 300)}`")

@dp.message(Command("rag_reindex"))
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
        await query.message.reply(f"Привет, @{username}!\nМожешь писать мне свои вопросы\nОбращайся ко мне - нейробот или бот")
    else:
        await query.message.reply("Подписка не найдена! Убедитесь, что подписаны на канал", show_alert=True)

@dp.message()
async def auto_reply(message: types.Message):
    if not message.text:
        return
    
    id = message.from_user.id
    if not await is_subscribed(id) and id != 1087968824:
        await message.reply("Подпишитесь на @MineBridgeOfficial, чтобы пользоваться ботом")
        utils.save_incoming_message(message)
        return
    
    if utils.is_user_frozen(id):
        logging.info("Auto replies are temporarily frozen for user %s", id)
        utils.save_incoming_message(message)
        return

    # robust chat type handling (works for aiogram returning enum or string)
    chat_type = getattr(message.chat, "type", None)
    if isinstance(chat_type, str):
        ct_name = chat_type.upper()
    else:
        # chat_type may be an Enum with .name, or something else
        ct_name = getattr(chat_type, "name", str(chat_type)).upper()
    is_group = ct_name in ("GROUP", "SUPERGROUP")

    if is_group and not utils.should_answer(message, bot_username):
        logging.info("Пропущено (но сохранено) сообщение без упоминания бота или ответа на бота (группа)")
        utils.save_incoming_message(message)
        return

    try:
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        except Exception:
            pass

        msg = await message.reply("⏳ <b>Печатаю...</b>")

        username = (message.from_user.username or f"{message.from_user.first_name}")
        conv_key = utils.make_key(message)

        sys_prompt = utils.load_system_prompt_for_chat(message.chat)
        sys_prompt += "\n\nВажно: Используй HTML-разметку для форматирования ответа. MarkDown НЕЛЬЗЯ! Все ссылки вставляй сразу в текст.\n"
        sys_prompt += "ВАЖНО: В ответе не показывай служебные индексы источников (вида [xxxxxxxxxx:0] или 0d829391f3:0)"

        rag_ctx = ""
        try:
            # Получаем RAG контекст (если включён)
            if config.RAG_ENABLED:
                rag_ctx = await rag.build_full_context(message.text, username)
        except Exception:
            logging.exception("RAG: failed to build context")

        # call OpenAI (non-stream). Keep as in original file
        answer = await handlers_helpers.complete_openai_nostream(
            message.text,
            username,
            conv_key,
            sys_prompt,
            rag_ctx=rag_ctx,
            message=message,
        )

        await msgs.long_text(msg, message, answer)
    except Exception as e:
        logging.exception("Ошибка в auto_reply")
        try:
            await msg.edit_text(f"<b>Что-то пошло не так</b> ⚠️\n{str(e)}")
        except Exception:
            pass
