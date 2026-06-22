import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_USER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "mentor.db")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Amsterdam")
