import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Telegram API credentials
API_ID = int(os.getenv('API_ID', '2040'))
API_HASH = os.getenv('API_HASH', 'b18441a1ff607e10a989891a5462e627')
BOT_TOKEN = os.getenv('BOT_TOKEN')  # Read from environment for security

if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN is not set in environment variables!")
    exit(1)

# Optional: Use a Local Bot API server (to send files up to 2GB)
# If you run a local telegram-bot-api server, set these in your .env:
# BOT_API_BASE_URL=http://<host>:8081/bot
# BOT_API_BASE_FILE_URL=http://<host>:8081/file/bot
BOT_API_BASE_URL = os.getenv('BOT_API_BASE_URL')
BOT_API_BASE_FILE_URL = os.getenv('BOT_API_BASE_FILE_URL')

# Optional: Free large-file workaround without Local Bot API
# Generate a Pyrogram session string locally and set TG_SESSION_STRING,
# and create a private channel, add both your user and the bot as admins,
# then set its ID as BRIDGE_CHANNEL_ID.
TG_SESSION_STRING = os.getenv('TG_SESSION_STRING')
BRIDGE_CHANNEL_ID = int(os.getenv('BRIDGE_CHANNEL_ID', '0'))

# Telegram API credentials
TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')

# Reddit API credentials
REDDIT_CLIENT_ID = os.getenv('REDDIT_CLIENT_ID')
REDDIT_CLIENT_SECRET = os.getenv('REDDIT_CLIENT_SECRET')
# Optional: allow override of redirect URI via environment; default matches Reddit app config
REDDIT_REDIRECT_URI = os.getenv('REDDIT_REDIRECT_URI', 'http://localhost:8080')

# Authorization settings
_auth_users_raw = os.getenv('AUTHORIZED_USERS', '').strip()
AUTHORIZED_USERS = set()
if _auth_users_raw:
    try:
        AUTHORIZED_USERS = {int(x.strip()) for x in _auth_users_raw.split(',') if x.strip()}
    except Exception:
        # Ignore parse errors; will fall back to defaults in bot.py
        AUTHORIZED_USERS = set()

ALLOW_ALL = os.getenv('ALLOW_ALL', 'false').lower() in {'1', 'true', 'yes', 'on'}
