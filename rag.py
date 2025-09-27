# rag.py
import json
import asyncio
from pathlib import Path
import numpy as np
import logging
import httpx
from datetime import *
import config
import utils
import mc
from mb_api import fetch_player_by_nick

RAG_CHUNKS = []   # [{id, file, text, mtime}]
RAG_VECS = None
RAG_LOADED = False
RAG_LOCK = asyncio.Lock()

async def _embed_batch(texts: list[str]) -> list[list[float]]:
    JINA_KEY = __import__("os").environ.get("JINA_API_KEY")
    if not JINA_KEY:
        raise RuntimeError("JINA_API_KEY is not set in environment")
    attempt = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=60) as s:
                r = await s.post(
                    "https://api.jina.ai/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {JINA_KEY}",
                        "Accept": "application/json",
                    },
                    json={"model": config.RAG_EMB_MODEL, "input": texts},
                )
                r.raise_for_status()
                payload = r.json()
                return [item["embedding"] for item in payload["data"]]
        except httpx.HTTPStatusError as e:
            attempt += 1
            if attempt > config.MAX_OPENAI_RETRIES:
                body = (e.response.text or "")[:500]
                logging.exception("RAG: Jina HTTP %s, body: %s", e.response.status_code, body)
                raise
            wait = min(config.OPENAI_BACKOFF_BASE * (2 ** (attempt - 1)), 60)
            logging.warning("RAG: Jina HTTP %s, retry %d/%d after %.1fs", e.response.status_code, attempt, config.MAX_OPENAI_RETRIES, wait)
            await asyncio.sleep(wait)
        except Exception:
            logging.exception("RAG: Jina embeddings request failed")
            raise

def read_text_file(p: Path) -> str:
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore")
        if raw.startswith("\ufeff"):
            raw = raw.lstrip("\ufeff")
        return raw.replace("\r\n", "\n").replace("\r", "\n")
    except Exception:
        logging.exception("RAG: failed to read %s", p)
        return ""
    

def split_chunks(text: str, size: int, ov: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i+size])
        i += max(1, size - ov)
    return [c for c in out if c.strip()]

async def _ensure_rag_index():
    global RAG_CHUNKS, RAG_VECS, RAG_LOADED
    async with RAG_LOCK:
        config.RAG_INDEX_DIR.mkdir(parents=True, exist_ok=True)
        meta_path = config.RAG_INDEX_DIR / "chunks.json"
        vecs_path = config.RAG_INDEX_DIR / "vecs.npy"

        if meta_path.exists() and vecs_path.exists() and not RAG_LOADED:
            try:
                RAG_CHUNKS = json.loads(meta_path.read_text(encoding="utf-8"))
                RAG_VECS = np.load(vecs_path)
                RAG_LOADED = True
                logging.info("RAG: loaded cache with %d chunks", len(RAG_CHUNKS))
            except Exception:
                logging.exception("RAG: failed to load cache, rebuilding")

        kb_files = []
        if config.KB_DIR.exists():
            for p in config.KB_DIR.rglob("*"):
                if p.is_file() and p.suffix.lower() in {".txt", ".md"}:
                    kb_files.append(p)

        known_paths = {c["file"] for c in RAG_CHUNKS}
        kb_paths = {str(p) for p in kb_files}

        need_rebuild = (not RAG_LOADED) or (known_paths != kb_paths)

        if not need_rebuild:
            # check mtimes
            for p in kb_files:
                m = p.stat().st_mtime
                if not any(c["file"] == str(p) and abs(c.get("mtime", 0.0) - m) < 1e-6 for c in RAG_CHUNKS):
                    need_rebuild = True
                    break

        if not need_rebuild:
            return

        logging.info("RAG: (re)building index...")
        all_chunks = []
        all_texts = []
        for p in kb_files:
            txt = read_text_file(p)
            parts = split_chunks(txt, config.RAG_CHUNK_SIZE, config.RAG_CHUNK_OVERLAP)
            m = p.stat().st_mtime
            for i, ch in enumerate(parts):
                cid = f"{utils.hash(str(p))}:{i}"
                all_chunks.append({"id": cid, "file": str(p), "text": ch, "mtime": m})
                all_texts.append(ch)

        vecs = []
        for i in range(0, len(all_texts), config.RAG_EMB_BATCH):
            batch = all_texts[i:i+config.RAG_EMB_BATCH]
            vecs.extend(await _embed_batch(batch))

        if vecs:
            V = np.array(vecs, dtype="float32")
            norms = np.linalg.norm(V, axis=1, keepdims=True)
            norms[norms == 0.0] = 1.0
            V /= norms
            RAG_CHUNKS = all_chunks
            RAG_VECS = V
            meta_path.write_text(json.dumps(RAG_CHUNKS, ensure_ascii=False, indent=2), encoding="utf-8")
            np.save(vecs_path, RAG_VECS)
            RAG_LOADED = True
            logging.info("RAG: built %d chunks from %d files", len(RAG_CHUNKS), len(kb_files))
        else:
            RAG_CHUNKS, RAG_VECS, RAG_LOADED = [], None, True
            logging.warning("RAG: no chunks produced (empty kb?)")

async def search(query: str, k: int = config.RAG_TOP_K):
    if not config.RAG_ENABLED:
        return []
    await _ensure_rag_index()
    global RAG_VECS, RAG_CHUNKS
    if RAG_VECS is None or len(RAG_CHUNKS) == 0:
        return []
    q_emb = (await _embed_batch([query]))[0]
    q = np.array([q_emb], dtype="float32")
    q /= max(np.linalg.norm(q), 1e-12)
    sims = (RAG_VECS @ q.T).reshape(-1)
    top_idx = np.argsort(-sims)[:k]
    return [(RAG_CHUNKS[i], float(sims[i])) for i in top_idx]

async def build_full_context(
    user_query: str,
    username: str | None = None,
    k: int = config.RAG_TOP_K,
    max_chars: int = 2000,
) -> str:
    sections: list[str] = []

    # Dynamic server context
    try:
        payload = await mc.fetch_status()
        server_ctx = mc.format_status_text(payload)
        if server_ctx:
            sections.append(f"Пиши про статус, только когда просят\n{server_ctx}\n")
    except Exception:
        logging.exception("RAG: failed to fetch server status")

    # Dynamic player context
    if username:
        try:
            player_info = await fetch_player_by_nick(username)
            if player_info:
                sections.append(f"Игрок (из MineBridge API):\nИспользуй данные аккаунта, только когда просят\n{json.dumps(player_info, ensure_ascii=False)}\n")
        except Exception:
            logging.exception("RAG: failed to fetch player info")
            
    sections.append(f"Текущая дата: {datetime.now()}")

    # Knowledge base via semantic search
    results = await search(user_query, k=k)
    if results:
        total = 0
        kb_parts: list[str] = []
        for ch, _sc in results:
            snippet = (ch.get("text") or "").strip()
            if not snippet:
                continue
            if total + len(snippet) > max_chars:
                snippet = snippet[: max(0, max_chars - total)]
            if snippet:
                kb_parts.append(snippet)
                total += len(snippet)
            if total >= max_chars:
                break
        if kb_parts:
            sections.append("\n".join(kb_parts))

    return "\n\n".join([s for s in sections if s])

# async def build_context(user_query: str, k: int = config.RAG_TOP_K, max_chars: int = 2000) -> str:
#     results = await search(user_query, k=k)
#     if not results:
#         return ""
#     lines = ["Ниже выдержки из базы знаний. Используй их только как справку и не включай служебные индексы/ссылки в ответ."]
#     total = 0
#     for ch, sc in results:
#         snippet = ch["text"].strip()
#         if not snippet:
#             continue
#         if total + len(snippet) > max_chars:
#             snippet = snippet[:max(0, max_chars - total)]
#         lines.append(snippet)
#         total += len(snippet)
#         if total >= max_chars:
#             break
#     lines.append("— Конец выдержек —")
#     return "\n".join(lines)
