import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_USER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "mentor.db")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Amsterdam")

# Квест дня (Telegram Mini App)
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # https://<твой-домен>.up.railway.app/
PORT = int(os.getenv("PORT", "8080"))
