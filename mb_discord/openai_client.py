from openai import AsyncOpenAI
import config as dcfg


def create_client() -> AsyncOpenAI:
    key = (dcfg.OPENAI_API_KEY or "").strip()
    base = (dcfg.OPENAI_BASE_URL or "").strip()
    if not key:
        raise RuntimeError("Set OPENAI_API_KEY in .env for Discord bot")
    return AsyncOpenAI(api_key=key, base_url=base if base else None)

