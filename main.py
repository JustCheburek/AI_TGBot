# main.py
import asyncio
import logging
import traceback

from bot_init import bot, dp
import config, rag, mc, utils, handlers, handlers_helpers # испорт всего-всего

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
        from bot_init import openai_client  # если openai_client объявлен в bot_init
        aclose = getattr(openai_client, "aclose", None)
        if callable(aclose):
            if asyncio.iscoroutinefunction(aclose):
                await aclose()
            else:
                aclose()
        else:
            close = getattr(openai_client, "close", None)
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
