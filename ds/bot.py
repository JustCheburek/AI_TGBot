from __future__ import annotations

import asyncio
import logging
from typing import Optional

import nextcord as dlib
from nextcord.ext import commands

import config as dcfg
from openai_client import create_client
import utils as dut
import msgs as dmsgs
import mc as dmc

import html_edit

# Optional imports from the existing codebase (may instantiate Telegram objects at import-time)
try:
    import rag  # reuse RAG index/search
except Exception:  # pragma: no cover
    rag = None  # type: ignore

try:
    import mb_api  # reuse MineBridge API client
except Exception:  # pragma: no cover
    mb_api = None  # type: ignore


log = logging.getLogger(__name__)


def build_sys_prompt() -> str:
    # Keep consistent with TG behavior (tags + simple HTML note)
    p = [
        "Ты — ассистент MineBridge. Отвечай по-русски, кратко и по делу.",
        "Если уместно — вставляй теги [[photo:...]] или [[sticker:...]].",
        "Важно: используй MarkDown.",
    ]
    return "\n".join(p)


class MineBridgeDiscord(commands.Bot):
    def __init__(self):
        intents = dlib.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="/", intents=intents, case_insensitive=True)
        self.openai = create_client()

    async def setup_hook(self) -> None:
        log.info("Discord bot setup complete")


bot = MineBridgeDiscord()


@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user and bot.user.id)


async def _is_allowed_user(user: dlib.abc.User) -> bool:
    if not dcfg.REQUIRE_GUILD_MEMBERSHIP or not dcfg.ALLOWED_GUILD_IDS:
        return True
    try:
        for gid in dcfg.ALLOWED_GUILD_IDS:
            guild = bot.get_guild(gid)
            if not guild:
                continue
            m = guild.get_member(user.id)
            if m is not None:
                return True
    except Exception:
        log.exception("guild membership check failed for %s", user)
    return False


async def complete_openai_nostream(user_text: str, name: str, conv_key: dut.HistoryKey, *, message: dlib.Message) -> Optional[str]:
    prompt = (user_text or "").strip()
    if not prompt:
        return ""
    prompt = dut.shorten(prompt)

    # Build context: in guild use channel logs; in DM use private history
    use_channel_ctx = bool(message.guild)
    if use_channel_ctx:
        input_with_ctx = dut.build_input_from_channel_context(message, prompt, name)
        dut.save_incoming_message(message)
    else:
        input_with_ctx = dut.build_input_with_history(conv_key, prompt, name)
        dut.remember_user(conv_key, prompt)

    rag_ctx = ""
    if dcfg.RAG_ENABLED and rag is not None:
        try:
            # reuse username as in TG flow
            username = (getattr(message.author, "name", None) or "").strip()
            rag_ctx = await rag.build_full_context(prompt, username)
        except Exception:
            log.exception("RAG: failed to build context")
            rag_ctx = ""

    if rag_ctx:
        input_with_ctx = f"{rag_ctx}\n\n{input_with_ctx}"

    try:
        resp = await bot.openai.chat.completions.create(
            model="x-ai/grok-4-fast:free",
            messages=[
                {"role": "system", "content": build_sys_prompt()},
                {"role": "user", "content": input_with_ctx},
            ],
            temperature=1,
        )
        text = (resp.choices[0].message.content or "").strip()
        text = html_edit.remove(text)
        if text:
            if not use_channel_ctx:
                dut.remember_assistant(conv_key, text)
            else:
                dut.save_outgoing_message(message.channel.id, text)
        return text
    except Exception:
        log.exception("OpenAI non-stream failed")
        return None


def _freeze_help_text(user_id: int) -> str:
    cur = dut.get_user_freeze(user_id)
    if cur:
        return "Заморозка уже активна. Используйте /unfreeze чтобы снять.”"
    opts = ", ".join(dut.get_hour_string(h) for h in dcfg.FREEZE_OPTIONS)
    return f"Укажите длительность, например: /freeze 2\nДоступные варианты: {opts}"


@bot.command(name="start")
async def cmd_start(ctx: commands.Context):
    if not await _is_allowed_user(ctx.author):
        await ctx.reply("Доступ ограничен. Вступите на сервер, чтобы пользоваться бриджиком.")
        return
    await ctx.reply(f"Привет, @{ctx.author.name}! Я готов помочь.")


@bot.command(name="freeze")
async def cmd_freeze(ctx: commands.Context, hours: Optional[int] = None):
    if not await _is_allowed_user(ctx.author):
        await ctx.reply("Доступ ограничен.")
        return
    if hours is None:
        await ctx.reply(_freeze_help_text(ctx.author.id))
        return
    if hours <= 0:
        dut.clear_user_freeze(ctx.author.id)
        await ctx.reply("Автоответы отключены временно: снято.")
        return
    dut.set_user_freeze(ctx.author.id, hours)
    await ctx.reply(f"Автоответы заморожены на {dut.get_hour_string(hours)}. Используйте /unfreeze для снятия.")


@bot.command(name="unfreeze")
async def cmd_unfreeze(ctx: commands.Context):
    if not await _is_allowed_user(ctx.author):
        await ctx.reply("Доступ ограничен.")
        return
    if dut.clear_user_freeze(ctx.author.id):
        await ctx.reply("Заморозка снята.")
    else:
        await ctx.reply("Заморозка не была активна.")


@bot.command(name="status")
async def cmd_status(ctx: commands.Context):
    if not await _is_allowed_user(ctx.author):
        await ctx.reply("Доступ ограничен.")
        return
    await ctx.trigger_typing()
    try:
        payload = await dmc.fetch_status()
        text = dmc.format_status_markdown(payload)
        await ctx.reply(text)
    except Exception as e:
        await ctx.reply(f"Не удалось получить статус: `{str(e)[:300]}`")


@bot.command(name="rag_reindex")
async def cmd_rag_reindex(ctx: commands.Context):
    if not await _is_allowed_user(ctx.author):
        await ctx.reply("Доступ ограничен.")
        return
    if not dcfg.RAG_ENABLED or rag is None:
        await ctx.reply("RAG выключен.")
        return
    msg = await ctx.reply("🔄 Перестраиваю индекс...")
    try:
        # reuse existing rag module
        rag.RAG_LOADED = False
        await rag._ensure_rag_index()
        await msg.edit(content=f"✅ Готово. Чанкoв: {len(rag.RAG_CHUNKS)}")
    except Exception as e:
        await msg.edit(content=f"⚠️ Ошибка: {e}")


@bot.command(name="player")
async def cmd_player(ctx: commands.Context, *, nick: Optional[str] = None):
    if not await _is_allowed_user(ctx.author):
        await ctx.reply("Доступ ограничен.")
        return
    if not nick:
        nick = (ctx.author.name or "").strip()
    if not nick:
        await ctx.reply("Формат: `/player NICK`.")
        return
    msg = await ctx.reply("🔎 Ищу игрока...")
    try:
        if mb_api is None:
            await msg.edit(content="mb_api недоступен")
            return
        player_info = await mb_api.fetch_player_by_nick(nick)
        if not player_info:
            await msg.edit(content=f"Профиль `{nick}` не найден.")
            return
        await msg.edit(content=dut.format_player_info_md(nick, player_info))
    except Exception as e:
        await msg.edit(content=f"Ошибка: {str(e)[:300]}")


@bot.event
async def on_message(message: dlib.Message):
    # Let commands process first if they match
    ctx = await bot.get_context(message)
    if ctx.valid:
        await bot.process_commands(message)
        return

    # Skip bots, system, empty
    if message.author.bot or not (message.content or "").strip():
        return

    # DM or guild policy
    if not await _is_allowed_user(message.author):
        return

    # Freeze check
    if dut.is_user_frozen(message.author.id):
        return

    # In guild: heuristics; in DM: always
    if message.guild:
        if not bot.user:
            return
        if not dut.should_answer_discord(message, bot.user):
            # still save for context
            try:
                dut.save_incoming_message(message)
            except Exception:
                pass
            return

    try:
        async with message.channel.typing():
            conv_key = dut.make_key(message)
            username = (getattr(message.author, "name", None) or "").strip()
            answer = await complete_openai_nostream(message.content, username, conv_key, message=message)
        if answer:
            await dmsgs.long_text(message.channel, answer)
    except Exception:
        log.exception("auto-reply failed")
