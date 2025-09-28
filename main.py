# main.py
# RU: Точка входа: инициализация бота, прогрев RAG и запуск поллинга.
import asyncio
import logging
import traceback

import config, rag, mc, utils, ai, tghandlers # испорт всего-всего
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import config
import ai

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# RU: username будет установлен при запуске (on_startup)
bot_username: str = "minebridge52bot"

logging.basicConfig(level=logging.DEBUG)

async def on_startup():
    global bot
    try:
        me = await bot.get_me()
        logging.info(f"Bot username: @{(me.username or '').lower()}")
    except Exception:
        logging.exception("Failed to get bot username on startup")
    try:
        if hasattr(rag, "_ensure_rag_index"):
            await rag._ensure_rag_index()
    except Exception:
        logging.exception("RAG: failed to ensure index on startup")

async def shutdown():
    try:
        # если у openai-клиента есть aclose/close — корректно закроем
        aclose = getattr(ai.client, "aclose", None)
        if callable(aclose):
            if asyncio.iscoroutinefunction(aclose):
                await aclose()
            else:
                aclose()
        else:
            close = getattr(ai.client, "close", None)
            if callable(close):
                res = close()
                if asyncio.iscoroutine(res):
                    await res
    except Exception:
        logging.exception("Error closing openai client")

    try:
        await bot.session.close()
    except Exception:
        pass

async def main():
    await on_startup()

    # отладочное логирование: покажем, что модуль handlers импортирован
    # RU: Хендлеры импортированы; стартуем поллинг
    logging.info("Handlers imported; starting polling")

    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.exception("Fatal polling error: %s", e)
        traceback.print_exc()
    finally:
        await shutdown()

if __name__ == "__main__":
    asyncio.run(main())
