import asyncio
import logging
from bot_init import *
import rag

async def on_startup():
    global bot_username
    try:
        me = await bot.get_me()
        bot_username = (me.username or "").lower()
        logging.info(f"Bot username: @{bot_username}")
    except Exception:
        logging.exception("Failed to get bot username on startup")
    try:
        if True:
            await rag._ensure_rag_index()
    except Exception:
        logging.exception("RAG: failed to ensure index on startup")

async def shutdown():
    try:
        if hasattr(openai_client, "close") and asyncio.iscoroutinefunction(openai_client.close):
            await openai_client.close()
    except Exception:
        pass
    try:
        await bot.session.close()
    except Exception:
        pass

async def main():
    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        await shutdown()

if __name__ == "__main__":
    asyncio.run(main())
