from pathlib import Path

from core import run_bot
from bots.example_bot.handlers import router
from bots.example_bot.texts import TEXTS

if __name__ == "__main__":
    run_bot(Path(__file__).parent, routers=[router], texts=TEXTS)
