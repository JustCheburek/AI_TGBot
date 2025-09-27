# bot_init.py
# RU: Инициализация объектов бота: Bot, Dispatcher и клиент OpenAI.
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from openai import AsyncOpenAI
from aiogram.client.default import DefaultBotProperties
import config

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY, base_url="https://openrouter.ai/api/v1")

# RU: username будет установлен при запуске (on_startup)
bot_username: str = "minebridge52bot"
