import os


def get_env(name: str, default=None):
    return os.environ.get(name, default)


API_ID = int(get_env("API_ID", "0"))
API_HASH = get_env("API_HASH", "")
BOT_TOKEN = get_env("BOT_TOKEN", "")

OWNER_ID = int(get_env("OWNER_ID", "0"))

FORCE_SUB_CHANNEL = get_env("FORCE_SUB_CHANNEL", "")
LOG_CHANNEL = get_env("LOG_CHANNEL", "")

MONGO_URI = get_env("MONGO_URI", "")
