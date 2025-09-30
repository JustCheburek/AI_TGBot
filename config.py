# config.py
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# RU: Параметры MineBridge API
MB_HOST = "майнбридж.рф"

# RU: Токены Telegram / OpenAI
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")
CHANNEL = os.getenv("CHANNEL", "@MineBridgeOfficial")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Память
GROUP_MAX_MESSAGES = 12
DM_MAX_MESSAGES = 5

# RU: Minecraft-сервер
MC_SERVER_HOST = os.getenv("MC_SERVER_HOST")

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
MC_CACHE_TTL = 20
FREEZE_OPTIONS = (1, 2, 3, 4)
STICKERS: dict[str, str] = {
    "странно": "CAACAgIAAxkBAAEPdBxo186lIJy0-xIy1eyVATr_mznqcgACTykAAgaVeUsg2eL2ufOZazYE",
    "нененене": "CAACAgIAAxkBAAEPdB5o1868amdp5swyuMsK0q-vYdF3xgACpGsAAhD9aUohrd7s7TgHiTYE",
    "крутой": "CAACAgIAAxkBAAEPdCBo187UoSTA0plgabybnSl0-0A2RwACiScAAlny6UpGhvNYO5zAKTYE",
    "сердце": "CAACAgIAAxkBAAEPdCJo187ihaiZMqs3jb4JA9iDefJWAAP1MAACPssIS9HkZ_xXu5p8NgQ",
    "привет": "CAACAgIAAxkBAAEPdCRo1870kvBfRhY_6x5rYIcjfx5hNgAC-y8AAgddAUsyYDgvJ2NxsTYE",
    "лайк": "CAACAgIAAxkBAAEPdC5o188CQqA8WW6UAQiL5KxPPLp9cwAC6ycAAv3bCEvOUGWa8xaW9DYE",
    "о боже": "CAACAgIAAxkBAAEPdDBo188ORT0-fTIQERvrNXsfqNrNfAACcCsAAp4IGEiu0E3hQ-bTEzYE",
    "сердцеед": "CAACAgIAAxkBAAEPdDJo188cR-wb98SP8hLBBcTJS-r3_gACeDcAAu9rgUgMcsWUSzUqjzYE",
    "я пофигист": "CAACAgIAAxkBAAEPdDRo188oAjG2x0UeX6_D9px0wAk4jgACSi4AAl8eSEkr3MjFJi8laTYE",
    "держи ковры": "CAACAgIAAxkBAAEPdDZo1882YBAkfWi1PpoVVa61TqymKAACljEAAkaP6Umsr6VkooR7zDYE",
    "привет детка": "CAACAgIAAxkBAAEPdDpo189KlNe3Dqw-R21xCKwSgu742QACRDMAAh2XOUqdiCZZo0GihjYE",
    "я устал": "CAACAgIAAxkBAAEPdDxo189XMqbCH6qjwnilQkW9hHIAATwAAh82AAIYZJBLZ4zUWspr7j42BA",
    "50 причин стать чебуреком": "CAACAgIAAxkBAAEPdD5o189k-BTLsFVq1AUqJ3PeZTW6jAACgjcAAhh6OUhvWxhgrCGMnTYE",
    "это наши ковры": "CAACAgIAAxkBAAEPdEBo189xUrCzDAwaKuYnejNd61XFiAAC_jMAAqUvqUrFJS7nMu25SDYE",
    "логотип майнбридж": "CAACAgIAAxkBAAEPdEJo18-fuqN93cSINqXPM7HBjh0L7wAClnUAAqu9-UhOkKmCkgUirzYE",
    "злой": "CAACAgIAAxkBAAEPdERo18_Q5OgT5DBFMVi3kGYAAUHE9D8AAu19AAK9LOFLCOKT-E8Blu02BA",
    "кривой лайк": "CAACAgIAAxkBAAEPdEZo19AjPQIaU0oYTUkePqkIVuyD2gACckcAAoG0sEghn014r-GJBDYE",
    "думать": "CAACAgIAAxkBAAEPdEho19A5nx_GRpTSY7B9XoMeUpilBAACP4QAAhpcaEnlchnEqaCAQjYE",
    "absolute cinema": "CAACAgUAAxkBAAEPdEpo19BXo_yCCCvUJXqZsaDmkMpQ_gACoiIAAuJAmFX4ln_-jyOnojYE",
    "ааа чебурек": "CAACAgIAAxkBAAEPdExo19B3uclvmJcG_fw6o_GV_XNAygACmW8AAmn2KUhx7O9ebCGM9DYE",
    "донат": "CAACAgIAAxkBAAEPdE5o19CntqMNTUjkTXfWX-tGYX3sJgAC9SkAAnODmElZp4v_nv7G_DYE",
    "сасун": "CAACAgIAAxkBAAEPdFBo19C0nZ_k8W6rQLqZe-psaZ4mkQAChDEAAt7P2Eh-W_CBrpdPSjYE",
    "программист": "CAACAgIAAxkBAAEPdFJo19DGzATA8Bb_JuCqW9azxwg3-gACUxgAAtkcCUuTTOAKMRASRzYE",
    "дададада": "CAACAgIAAxkBAAEPdFRo19DXMt-yFVwhZ_zYnsYS3II88wACLS0AApvBcUos_YGp9A1KYzYE",
    "омагад": "CAACAgIAAxkBAAEPdFZo19DrfRjZDJG6m4YVfbq6484-ZQACbAADT64DP65_iVH3bL_eNgQ",
    "каво мем": "CAACAgIAAxkBAAEPdFho19D9BKEsqGWBQSORenE4nFULtAACMAADOq1TFbyJ9Xyu41UENgQ",
    "осуждаю": "CAACAgIAAxkBAAEPdFpo19EVgVC2wij6ttj25JD6BRqucwACdQgAArtzaEpy0PTtNpgzNzYE",
    "произошёл трооллинг": "CAACAgIAAxkBAAEPdFxo19E6yKgckgl0kZm1zhVGnRlIgAACiwkAAtQocEr90H-KQtzpJzYE",
    "троллинг не удался": "CAACAgIAAxkBAAEPdF5o19FPNJDXRO52Gcp8B8cZXEZp2QAC_wYAAvQ-cEoeKrdTePgmSjYE",
    "бан": "CAACAgIAAxkBAAEPdGBo19FmQvHn_96Z2qly4R9JZrlRqQAC-wMAAuNuOhnmkaQ8f5x7gTYE",
    "свинья-паук": "CAACAgIAAxkBAAEPdGJo19Fy0DD8C3HHwH59lPMxWs0fOAACzgMAAuNuOhlMdELsVNZG3jYE",
    "понял": "CAACAgIAAxkBAAEPdGRo19GGVfaPQ-v6l9to-JLyXY4-JwAC_QMAAuNuOhkFraxiObgg8zYE",
    "не понял": "CAACAgIAAxkBAAEPdGZo19GUetpNzFesvbxuWq68nruWoAAC-gMAAuNuOhneeFh_QWSGtjYE",
    "эндер дракон в обычном мире": "CAACAgIAAxkBAAEPdGho19GbamD0KQP-HNmEh6ztSXbdggADBAAC4246GQABQFWDEcL2XzYE",
    "скобочка-стив": "CAACAgIAAxkBAAEPdGpo19GqRHawbSovbaZYpOqC5cIB5QACCAQAAuNuOhm7QrCupBDTMjYE",
    "лиса XD": "CAACAgIAAxkBAAEPdGxo19G4Km47XPUO1bHabzWcfbStvQACIwQAAuNuOhnT35jgRYDNAAE2BA",
    "этачё": "CAACAgIAAxkBAAEPdG5o19HHAV5py_M62T5MIOsHTXkt9QACKAUAAuNuOhlDVV50N0cFpjYE",
    "мем скала джонсон": "CAACAgIAAxkBAAEPdHBo19HXN6AaSMuqLuw7BTp4uSjC6AACyhMAAoyvMUhmhlHyjrTwgDYE",
    "кот качает головой": "CAACAgIAAxkBAAEPdHJo19H_3wuTPMITxs88zV7bruAn9gAC-z8AAq6uoEi8wbAnaQ14ZDYE",
    "понял осознал": "CAACAgIAAxkBAAEPdHRo19ITIiiTVs23V53PR-ofKHZ6egACej4AAqBFoUjbnntNL52jzDYE",
    "мозги мёрзнут": "CAACAgIAAxkBAAEPdHZo19JixuhFPL1Yn5kKESa8OV0dIwACAicAArwU8UrPXAIByUQcjjYE",
    "порево": "CAACAgIAAxkBAAEPdHho19J3XIG66Onpdlh7azH6zk1ZeQAC5CYAAuDg8ErT0gytemmbSjYE",
    "пон": "CAACAgIAAxkBAAEPdsxo2LIEJfGB0EKt2ds-3B1m6DmY8wACU1IAApN08Emwj5nw-krF-TYE",
    "покажи жопа": "CAACAgIAAxkBAAEPds5o2LaV739UX29Bmr32VZElS_7ebAAC5QMAAuNuOhkzeLGCxEclKzYE",
    "ахаха оой": "CAACAgIAAxkBAAEPdtBo2LgOEIfvyO8Ir2RhDkjZMaMpRgACn2kAAo1l8EryJ35lqVbCeTYE",
    "на кота падает бомба": "CAACAgIAAxkBAAEPedho277CHhKyEIpP_uv3q_HTom0I1wAC0R4AAmskwEvcFTxGmIq-aDYE",
    "ёжик кушает": "CAACAgIAAxkBAAEPedZo276eNLuYEjK6qs_nLREhlsnzsAACNBIAAhPX2EsAAbFTK7Zm0XQ2BA",
    "избивание": "CAACAgIAAxkBAAEPedpo278keWMqpYuB2NzkRuUWWE8hrwACpWkAAouCuUsz-7BQnjwGrjYE",
    "крутой школьник с битой": "CAACAgIAAxkBAAEPedxo2788zONHyeNbN-ZjcWRcISVJwAACNwADCouFHhLbTCP_OKFzNgQ",
    "голем повесился": "CAACAgIAAxkBAAEPed5o279XwOiAvcDWnNrD54UKEa8V1AACSwADCouFHp346BXWmq2HNgQ",
    "потеет": 'CAACAgIAAxkBAAEPeeBo279yJ8elcCUszK0VopehIQwowwACz2kAAupcuUtMpGjn_EjAyDYE',
    "пепе с мечами": "CAACAgIAAxkBAAEPeeJo27-LuwxdCeZhkOe9LkLQXr6yggACVwADDnr7Ch9ftelV4vz7NgQ",
    "спидрань отсюда": "CAACAgIAAxkBAAEPeeRo27-ilQNaP9EPQrixhPv8D-1BywACO0QAAkj6kUrF3ZvhLYuXvjYE",
    "бан": "CAACAgIAAxkBAAEPeeZo27_Me2GBKJdfM2jXU4UFTNwG1gACTlkAAnujaUjYydCsai0IJTYE"
}

# RU: Проверка обязательных переменных окружения
if not BOT_TOKEN:
    raise SystemExit("Set BOT_TOKEN in .env")
if not OPENAI_API_KEY:
    raise SystemExit("Set OPENAI_API_KEY in .env")
if not MC_SERVER_HOST:
    raise SystemExit("Set MC_SERVER_HOST in .env")
if not JINA_KEY:
    raise RuntimeError("Set JINA_API_KEY in .env")
if not GOOGLE_API_KEY:
    raise RuntimeError("Set GOOGLE_API_KEY in .env")
