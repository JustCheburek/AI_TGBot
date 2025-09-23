import logging
import asyncio

from aiogram import types
from aiogram.filters import Command

from bot_init import *
import config
import utils
import mc
import rag

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
        await utils.safe_edit_to(sent, f"⚠️ Не удалось получить статус: `{_shorten(str(e), 300)}`")

@dp.message(Command("rag_reindex"))
async def cmd_rag_reindex(message: types.Message):
    if not config.RAG_ENABLED:
        await message.reply("RAG отключён.")
        return
    sent_msg = await message.reply("🔄 Перестраиваю индекс...")
    try:
        global RAG_CHUNKS
        # force rebuild
        import importlib
        rag_mod = importlib.import_module(".rag", package=__package__)
        rag_mod.RAG_LOADED = False
        await rag_mod._ensure_rag_index()
        await utils.safe_edit_to(sent_msg, f"✅ Готово. Чанков: {len(rag_mod.RAG_CHUNKS)}")
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
    is_group = message.chat.type.name in ("GROUP", "SUPERGROUP")
    if is_group and not utils.should_answer(message, bot_username):
        logging.info("Пропущено сообщение без упоминания бота или ответа на бота (группа).")
        return

    if not await is_subscribed(user_id) and user_id != 1087968824:
        await message.reply("Подпишитесь на @MineBridgeOfficial, чтобы пользоваться ботом.")
        return

    txt = message.text.strip()
    if config.STATUS_INTENT_RE.search(txt):
        sent = await message.reply("🔎 Проверяю статус сервера...")
        try:
            payload = await mc.fetch_status(config.MC_SERVER_HOST, config.MC_SERVER_PORT)
            text = mc.format_status_text(config.MC_SERVER_HOST, config.MC_SERVER_PORT, payload)
            await utils.safe_edit_to(sent, text)
        except Exception as e:
            await utils.safe_edit_to(sent, f"⚠️ Не удалось получить статус: `{utils._shorten(str(e), 300)}`")
        return

    try:
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        except Exception:
            pass

        sent_msg = await message.reply("⏳ *Печатаю...*")

        sys_prompt = utils.load_system_prompt_for_chat(message.chat)
        sys_prompt += "\n\nВАЖНО: В ответе не показывай служебные индексы источников (вида [xxxxxxxxxx:0] или 0d829391f3:0)."

        rag_ctx = ""
        try:
            if config.RAG_ENABLED:
                rag_ctx = await rag.build_context(message.text, k=6, max_chars=2000)
        except Exception:
            logging.exception("RAG: failed to build context")

        username = (message.from_user.username or f"{message.from_user.first_name}")
        conv_key = utils.make_key(message)

        # call OpenAI (non-stream). Keep as in original file
        answer = await utils.complete_openai_nostream(
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
