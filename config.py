# config.py
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# MineBridge API
MB_HOST = "майнбридж.рф"

# Telegram / OpenAI
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHANNEL = os.getenv("CHANNEL", "@MineBridgeOfficial")

# Minecraft
MC_SERVER_HOST = os.getenv("MC_SERVER_HOST")
MC_SERVER_PORT = int(os.getenv("MC_SERVER_PORT", "25565"))

# RAG
BASE_DIR = Path(__file__).resolve().parent
KB_DIR = Path(__file__).resolve().parent / "kb"          # положите сюда .txt/.md файлы
RAG_INDEX_DIR = Path(__file__).resolve().parent / ".rag_cache"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
RAG_ENABLED = True
RAG_CHUNK_SIZE = 900
RAG_CHUNK_OVERLAP = 150
RAG_TOP_K = 6
RAG_EMB_MODEL = "jina-embeddings-v3"
RAG_EMB_BATCH = 64

# misc
MAX_HISTORY_MESSAGES = 5
MC_CACHE_TTL = 20
MAX_OPENAI_RETRIES = 2
OPENAI_BACKOFF_BASE = 1.5
FREEZE_OPTIONS = (1, 2, 3, 4)

# sanity checks
if not BOT_TOKEN:
    raise SystemExit("Set BOT_TOKEN in .env")
if not OPENAI_API_KEY:
    raise SystemExit("Set OPENAI_API_KEY in .env")
if not MC_SERVER_HOST:
    raise SystemExit("Set MC_SERVER_HOST (and optionally MC_SERVER_PORT) in .env")
