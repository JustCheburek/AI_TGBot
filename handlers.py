# handlers.py
import logging
import json  # <-- добавлено для форматирования player_info

from aiogram import types
from aiogram.filters import Command

from bot_init import *
import config
import utils
import mc
import rag
import handlers_helpers
from mb_api import fetch_player_by_nick

# is_subscribed implementation (uses bot)
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=config.CHANNEL, user_id=user_id)
        return member.status in ("creator", "administrator", "member", "restricted")
    except Exception:
        logging.exception("Error checking subscription")
        return False

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    name = message.from_user.username
    if await is_subscribed(user_id):
        await message.answer(f"Привет, @{name}! Можешь писать мне свои вопросы. Обращайся ко мне - нейробот или бот")
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
    if query.data != "check_subscription":
        await query.answer()
        return
    if await is_subscribed(query.from_user.id):
        await query.message.answer("Майнкрафт сервер *временно оффлайн*.")
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
