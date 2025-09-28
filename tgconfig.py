import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL = os.getenv("CHANNEL", "@MineBridgeOfficial")

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
    "порево": "CAACAgIAAxkBAAEPdHho19J3XIG66Onpdlh7azH6zk1ZeQAC5CYAAuDg8ErT0gytemmbSjYE"
}

if not BOT_TOKEN:
    raise SystemExit("Set BOT_TOKEN in .env")