import os
from pathlib import Path
from dotenv import load_dotenv

# Load env independently from Telegram config
load_dotenv()

# Discord-specific settings
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Optional: restrict bot replies to these guilds (comma-separated IDs)
ALLOWED_GUILD_IDS = [
    int(x.strip()) for x in (os.getenv("DISCORD_ALLOWED_GUILD_IDS", "").split(",")) if x.strip().isdigit()
]

# Optional: require membership to at least one allowed guild for DM usage
REQUIRE_GUILD_MEMBERSHIP = os.getenv("DISCORD_REQUIRE_GUILD", "false").strip().lower() in {"1", "true", "yes"}

# Conversation/history limits (kept similar to Telegram defaults)
GROUP_MAX_MESSAGES = int(os.getenv("DISCORD_GROUP_MAX_MESSAGES", "12"))
DM_MAX_MESSAGES = int(os.getenv("DISCORD_DM_MAX_MESSAGES", "5"))

# Freeze options presented to users
FREEZE_OPTIONS = tuple(int(x) for x in os.getenv("DISCORD_FREEZE_OPTIONS", "1,2,3,4").split(",") if x.strip())

# Image search keys (reuse PIXABAY_API_KEY if present)
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
PHOTOS_DIR = BASE_DIR / "photos"
KB_DIR = BASE_DIR / "kb"

# RAG toggle
RAG_ENABLED = os.getenv("DISCORD_RAG_ENABLED", "true").strip().lower() in {"1", "true", "yes"}

# OpenAI settings (reused)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

if not DISCORD_BOT_TOKEN:
    # Keep non-fatal to let import succeed; main guards will check
    pass

