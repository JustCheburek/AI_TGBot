from __future__ import annotations

import asyncio
import logging
from typing import Optional

import nextcord as dlib
from nextcord.ext import commands

import dsconfig as dcfg
import ai
import utils
import mc
import tghtml

try:
    import rag
except Exception:  # pragma: no cover
    rag = None  # type: ignore

try:
    import mb_api
except Exception:  # pragma: no cover
    mb_api = None  # type: ignore


log = logging.getLogger(__name__)


def build_sys_prompt() -> str:
    # Keep consistent with TG behavior: tags + simple HTML note
    p = [
        "Ты помощник сервера MineBridge. Отвечай дружелюбно, но по делу.",
        "Можно использовать специальные теги [[photo:...]] и [[sticker:...]].",
        "Разметка: простой Markdown, ссылки в явном виде.",
    ]
    return "\n".join(p)


class MineBridgeDiscord(commands.Bot):
    def __init__(self):
        intents = dlib.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="/", intents=intents, case_insensitive=True)

    async def setup_hook(self) -> None:
        log.info("Discord bot setup complete")


bot = MineBridgeDiscord()


@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user and bot.user.id)


async def _is_allowed_user(user: dlib.abc.User) -> bool:
    # Allow all by default; restrict if configured
    gids = getattr(dcfg, "ALLOWED_GUILD_IDS", []) or []
    require = bool(getattr(dcfg, "REQUIRE_GUILD_MEMBERSHIP", False) and gids)
    if not require:
        return True
    try:
        for gid in gids:
            guild = bot.get_guild(gid)
            if not guild:
                continue
            m = guild.get_member(user.id)
            if m is not None:
                return True
    except Exception:
        log.exception("guild membership check failed for %s", user)
    return False


def _should_answer_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    # Simple heuristics similar to TG
    score = 0
    if "?" in t:
        score += 2
    for kw in ("как", "что", "почему", "зачем", "help", "помоги", "статус", "player"):
        if kw.lower() in t.lower():
            score += 2
            break
    if len(t) >= 25:
        score += 1
    return score >= 3


async def _complete(user_text: str, name: str, conv_key: utils.HistoryKey, *, message: dlib.Message) -> Optional[str]:
    prompt = (user_text or "").strip()
    if not prompt:
        return ""
    prompt = utils.shorten(prompt)

    use_channel_ctx = bool(message.guild)
    if use_channel_ctx:
        input_with_ctx = utils.build_input_from_logs(message.channel.id, prompt, name)
        try:
            utils.save_chat_line(message.channel.id, getattr(message.author, "name", ""), prompt, is_bot=False)
        except Exception:
            pass
    else:
        input_with_ctx = utils.build_input_with_history(conv_key, prompt, name)
        utils.remember_user(conv_key, prompt)

    rag_ctx = ""
    if getattr(dcfg, "RAG_ENABLED", False) and rag is not None:
        try:
            username = (getattr(message.author, "name", None) or "").strip()
            rag_ctx = await rag.build_full_context(prompt, username)
        except Exception:
            log.exception("RAG: failed to build context")
            rag_ctx = ""

    if rag_ctx:
        input_with_ctx = f"{rag_ctx}\n\n{input_with_ctx}"

    try:
        resp = await ai.client.chat.completions.create(
            model="x-ai/grok-4-fast:free",
            messages=[
                {"role": "system", "content": build_sys_prompt()},
                {"role": "user", "content": input_with_ctx},
            ],
            temperature=1,
        )
        text = (resp.choices[0].message.content or "").strip()
        text = tghtml.remove(text)
        if text:
            if not use_channel_ctx:
                utils.remember_assistant(conv_key, text)
            else:
                utils.save_outgoing_message(message.channel.id, text)
        return text
    except Exception:
        log.exception("OpenAI completion failed")
        return None


@bot.command(name="freeze")
async def cmd_freeze(ctx: commands.Context, hours: Optional[int] = None):
    if not await _is_allowed_user(ctx.author):
        await ctx.reply("Доступ запрещён.")
        return
    if not hours:
        await ctx.reply("Укажи часы: /freeze 1|2|3|4")
        return
    hours = int(max(0, hours))
    utils.set_user_freeze(ctx.author.id, hours)
    await ctx.reply(f"Автоответ выключен на {utils.get_hour_string(hours)}. Команда /unfreeze для снятия.")


@bot.command(name="unfreeze")
async def cmd_unfreeze(ctx: commands.Context):
    if not await _is_allowed_user(ctx.author):
        await ctx.reply("Доступ запрещён.")
        return
    if utils.clear_user_freeze(ctx.author.id):
        await ctx.reply("Автоответ включён.")
    else:
        await ctx.reply("Автоответ и так включён.")


@bot.command(name="status")
async def cmd_status(ctx: commands.Context):
    if not await _is_allowed_user(ctx.author):
        await ctx.reply("Доступ запрещён.")
        return
    await ctx.trigger_typing()
    try:
        payload = await mc.fetch_status()
        # Reuse Telegram formatter but strip HTML tags for Discord readability
        text = mc.format_status_text(payload)
        text = tghtml.remove(text)
        await ctx.reply(text)
    except Exception as e:
        await ctx.reply(f"Не удалось получить статус: `{str(e)[:300]}`")


@bot.command(name="rag_reindex")
async def cmd_rag_reindex(ctx: commands.Context):
    if not await _is_allowed_user(ctx.author):
        await ctx.reply("Доступ запрещён.")
        return
    if not getattr(dcfg, "RAG_ENABLED", False) or rag is None:
        await ctx.reply("RAG отключен.")
        return
    msg = await ctx.reply("Перестраиваю индекс...")
    try:
        rag.RAG_LOADED = False
        await rag._ensure_rag_index()
        await msg.edit(content=f"Ок. Загружено чанков: {len(rag.RAG_CHUNKS)}")
    except Exception as e:
        await msg.edit(content=f"Ошибка: {e}")


@bot.command(name="player")
async def cmd_player(ctx: commands.Context, *, nick: Optional[str] = None):
    if not await _is_allowed_user(ctx.author):
        await ctx.reply("Доступ запрещён.")
        return
    nick = (nick or (ctx.author.name or "")).strip()
    if not nick:
        await ctx.reply("Укажи ник: `/player NICK`.")
        return
    msg = await ctx.reply("Сек, ищу игрока...")
    try:
        if mb_api is None:
            await msg.edit(content="mb_api недоступен")
            return
        player_info = await mb_api.fetch_player_by_nick(nick)
        if not player_info:
            await msg.edit(content=f"Игрок `{nick}` не найден.")
            return
        # Discord-friendly formatting: simple lines
        lines = [f"**Игрок** `{nick}`:"]
        for key, value in player_info.items():
            if key == "Роли" and isinstance(value, list):
                roles_lines = "\n".join(f"- {str(r)}" for r in value)
                lines.append(f"{key}:\n{roles_lines}")
            else:
                lines.append(f"{key}: `{value}`")
        await msg.edit(content="\n".join(lines))
    except Exception as e:
        await msg.edit(content=f"Ошибка: {str(e)[:300]}")


@bot.event
async def on_message(message: dlib.Message):
    # Process commands first
    ctx = await bot.get_context(message)
    if ctx.valid:
        await bot.process_commands(message)
        return

    # Ignore bots/empty
    if getattr(message.author, "bot", False) or not (message.content or "").strip():
        return

    # Policy check
    if not await _is_allowed_user(message.author):
        return

    # Freeze check
    if utils.is_user_frozen(message.author.id):
        return

    # Guild heuristic: require mention or natural question; DM always
    if message.guild:
        if bot.user and bot.user in getattr(message, "mentions", []):
            pass
        elif not _should_answer_text(message.content):
            # Save to logs for context anyway
            try:
                utils.save_chat_line(message.channel.id, getattr(message.author, "name", ""), message.content or "", is_bot=False)
            except Exception:
                pass
            return

    try:
        async with message.channel.typing():
            conv_key = (message.channel.id, message.author.id)
            username = (getattr(message.author, "name", None) or "").strip()
            answer = await _complete(message.content, username, conv_key, message=message)
        if answer:
            # Discord hard limit is 2000; keep margin
            CHUNK = 1900
            parts = [answer[i:i+CHUNK] for i in range(0, len(answer), CHUNK)]
            for part in parts:
                await message.channel.send(part)
    except Exception:
        log.exception("auto-reply failed")
