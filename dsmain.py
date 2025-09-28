import logging
from dotenv import load_dotenv

import dsconfig as dcfg
from dsbot import bot


def main() -> None:
    load_dotenv()
    token = (getattr(dcfg, "DISCORD_BOT_TOKEN", "") or "").strip()
    if not token:
        raise SystemExit("Set DISCORD_BOT_TOKEN in .env to run the Discord bot")
    logging.basicConfig(level=logging.INFO)
    bot.run(token)


if __name__ == "__main__":
    main()

