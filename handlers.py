# handlers.py
import logging
import json  # <-- добавлено для форматирования player_info
import time
from datetime import datetime
from typing import Dict, Optional

from aiogram import types
from aiogram.filters import Command

from bot_init import *
import config
import utils
import mc
import rag
import handlers_helpers
from mb_api import fetch_player_by_nick

FREEZE_OPTIONS = (1, 2, 3, 4)
_USER_FREEZES: Dict[int, float] = {}

def _cleanup_freezes(now: Optional[float] = None) -> None:
    if now is None:
        now = time.time()
    expired = [uid for uid, ts in _USER_FREEZES.items() if ts <= now]
    for uid in expired:
        _USER_FREEZES.pop(uid, None)

def set_user_freeze(user_id: int, hours: int) -> float:
    expires_at = time.time() + hours * 3600
    _USER_FREEZES[user_id] = expires_at
    return expires_at

def get_user_freeze(user_id: int) -> Optional[float]:
    _cleanup_freezes()
    expires_at = _USER_FREEZES.get(user_id)
    if expires_at is None:
        return None
    if expires_at <= time.time():
        _USER_FREEZES.pop(user_id, None)
        return None
    return expires_at

def is_user_frozen(user_id: int) -> bool:
    return get_user_freeze(user_id) is not None

def format_freeze_until(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime('%d.%m %H:%M')

# is_subscribed implementation (uses bot)
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=config.CHANNEL, user_id=user_id)
        return member.status in ("creator", "administrator", "member", "restricted")
    except Exception:
        logging.exception("Error checking subscription")
        return False

@dp.message(Command("freeze"))
async def cmd_freeze(message: types.Message):
    if not message.from_user:
        return

    id = message.from_user.id
    buttons = []

    for hours in FREEZE_OPTIONS:
        title = "1 час" if hours == 1 else f"{hours} часа"
        callback = f"freeze:{id}:{hours}"
        buttons.append(types.InlineKeyboardButton(text=title, callback_data=callback))

    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=rows)

    current_freeze = get_user_freeze(id)

    text_body = f"Выбери длительность заморозки автоответов" + \
        current_freeze if "\nТекущая заморозка действует до {format_freeze_until(current_freeze)}" else ""

    await message.reply(text_body, reply_markup=keyboard)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = (message.from_user.username or f"{message.from_user.first_name}")
    if await is_subscribed(user_id):
        await message.answer(f"Привет, @{username}! Можешь писать мне свои вопросы. Обращайся ко мне - нейробот или бот")
        return

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Подписаться", url=f"https://t.me/{config.CHANNEL.lstrip('@')}")],
        [types.InlineKeyboardButton(text="Проверить подписку", callback_data="check_subscription")]
    ])
    await message.answer(
        "Для доступа нужен канал @MineBridgeOfficial — подпишитесь и нажмите «*Проверить подписку*».",
        reply_markup=kb
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    sent = await message.reply("🔎 Проверяю статус сервера...")
    try:
        payload = await mc.fetch_status(config.MC_SERVER_HOST, config.MC_SERVER_PORT)
        text = mc.format_status_text(config.MC_SERVER_HOST, config.MC_SERVER_PORT, payload)
        await utils.safe_edit_to(sent, text)
    except Exception as e:
        await utils.safe_edit_to(sent, f"⚠️ Не удалось получить статус: `{utils._shorten(str(e), 300)}`")

@dp.message(Command("rag_reindex"))
async def cmd_rag_reindex(message: types.Message):
    if not config.RAG_ENABLED:
        await message.reply("RAG отключён.")
        return
    sent_msg = await message.reply("🔄 Перестраиваю индекс...")
    try:
        global RAG_CHUNKS
        rag.RAG_LOADED = False
        await rag._ensure_rag_index()
        await utils.safe_edit_to(sent_msg, f"✅ Готово. Чанков: {len(rag.RAG_CHUNKS)}")
    except Exception as e:
        logging.exception("RAG reindex error")
        await utils.safe_edit_to(sent_msg, f"⚠️ Ошибка перестройки: {e}")


@dp.callback_query()
async def callback_any(query: types.CallbackQuery):
    data = (query.data or "").strip()

    if data.startswith("freeze:"):
        parts = data.split(":")
        if len(parts) != 4:
            await query.answer("Не удалось применить заморозку.", show_alert=True)
            return
        _, target_id_str, hours_str, initiator_str = parts
        if initiator_str != str(query.from_user.id):
            await query.answer("Это меню предназначено для другого игрока", show_alert=True)
            return
        try:
            target_id = int(target_id_str)
            hours = int(hours_str)
        except ValueError:
            await query.answer("Недопустимые параметры", show_alert=True)
            return
        if hours not in FREEZE_OPTIONS:
            await query.answer("Недопустимая длительность", show_alert=True)
            return

        expires_at = set_user_freeze(target_id, hours)
        until_text = format_freeze_until(expires_at)
        try:
            if query.message:
                await query.message.edit_text(f"Автоответы заморожены до {until_text}.")
        except Exception:
            logging.exception("freeze: failed to edit confirmation message")
        await query.answer("Готово")
        return

    if data != "check_subscription":
        await query.answer()
        return

    if await is_subscribed(query.from_user.id):
        username = (query.message.from_user.username or f"{query.message.from_user.first_name}")
        await query.message.answer(f"Привет, @{username}! Можешь писать мне свои вопросы. Обращайся ко мне - нейробот или бот")
        await query.answer()
    else:
        await query.answer("Подписка не найдена. Убедитесь, что подписаны на канал.", show_alert=True)

@dp.message()
async def auto_reply(message: types.Message):
    if not message.text:
        return
    
    user_id = message.from_user.id
    if not await is_subscribed(user_id) and user_id != 1087968824:
        await message.reply("Подпишитесь на @MineBridgeOfficial, чтобы пользоваться ботом.")
        return

    if is_user_frozen(user_id):
        logging.info("Auto replies are temporarily frozen for user %s", user_id)
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
        logging.info("Пропущено сообщение без упоминания бота или ответа на бота (группа).")
        return

    try:
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        except Exception:
            pass

        sent_msg = await message.reply("⏳ *Печатаю...*")

        username = (message.from_user.username or f"{message.from_user.first_name}")
        conv_key = utils.make_key(message)

        sys_prompt = utils.load_system_prompt_for_chat(message.chat)
        sys_prompt += "\n\nВАЖНО: В ответе не показывай служебные индексы источников (вида [xxxxxxxxxx:0] или 0d829391f3:0)."

        rag_ctx = ""
        try:
            # Получаем RAG контекст (если включён)
            if config.RAG_ENABLED:
                rag_ctx = await rag.build_context(message.text, k=6, max_chars=2000)
        except Exception:
            logging.exception("RAG: failed to build context")

        # --- NEW: fetch player info from майнбридж API (mb_api.fetch_player_by_nick)
        player_ctx = ""
        try:
            player_info = await fetch_player_by_nick(username)
            if player_info:
                # краткое pretty-print (ограничим длину)
                player_ctx = "Информация об игроке (источник: майнбридж.рф):\n" + player_info
                # Включаем player_ctx в rag_ctx (модель увидит эти данные вместе с KB выдержками)
                if rag_ctx:
                    rag_ctx = player_ctx + "\n\n" + rag_ctx
                else:
                    rag_ctx = player_ctx
        except Exception:
            logging.exception("mb_api: failed to fetch player info")

        server_ctx = ""
        try:
            payload = await mc.fetch_status(config.MC_SERVER_HOST, config.MC_SERVER_PORT)
            server_ctx = mc.format_status_text(config.MC_SERVER_HOST, config.MC_SERVER_PORT, payload)
            if rag_ctx:
                rag_ctx = server_ctx + "\n\n" + rag_ctx
            else:
                rag_ctx = server_ctx
        except Exception as e:
            logging.exception("mc: failed to fetch server status")

        # call OpenAI (non-stream). Keep as in original file
        answer = await handlers_helpers.complete_openai_nostream(
            message.text,
            username,
            conv_key,
            sys_prompt,
            rag_ctx=rag_ctx,
        )

        await utils.send_long_text(sent_msg, message, answer)

    except Exception as e:
        logging.exception("Ошибка в auto_reply")
        try:
            await utils.safe_edit_to(sent_msg, f"*Что-то пошло не так* ⚠️\n{str(e)}")
        except Exception:
            pass
