import os
from pathlib import Path
from dotenv import load_dotenv

# Load env independently from Telegram config
load_dotenv()

# Discord-specific settings
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

if not DISCORD_BOT_TOKEN:
    # Keep non-fatal to let import succeed; main guards will check
    pass

