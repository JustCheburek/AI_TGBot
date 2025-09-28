import logging
from dotenv import load_dotenv

import config as dcfg
from bot import bot


if __name__ == "__main__":
    load_dotenv()
    token = (dcfg.DISCORD_BOT_TOKEN or "").strip()
    if not token:
        raise SystemExit("Set DISCORD_BOT_TOKEN in .env to run the Discord bot")
    logging.basicConfig(level=logging.INFO)
    bot.run(token)

