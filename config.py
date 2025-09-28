# config.py
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# RU: Параметры MineBridge API
MB_HOST = "майнбридж.рф"

# Память
GROUP_MAX_MESSAGES = 12
DM_MAX_MESSAGES = 5

# RU: Настройки RAG (поиск по базе знаний)
JINA_KEY = os.getenv("JINA_API_KEY")
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

# RU: Прочие параметры
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")
MC_SERVER_HOST = os.getenv("MC_SERVER_HOST")
MC_CACHE_TTL = 20
FREEZE_OPTIONS = (1, 2, 3, 4)

# RU: Проверка обязательных переменных окружения
if not MC_SERVER_HOST:
    raise SystemExit("Set MC_SERVER_HOST in .env")
if not JINA_KEY:
    raise RuntimeError("Set JINA_API_KEY in .env")
