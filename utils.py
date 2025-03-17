import os
import logging
from pyrogram import Client, filters
from pyrogram.types import Message

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")  # Defaults to using 'ffmpeg' from the system PATH

if not BOT_TOKEN or API_ID == 0 or not API_HASH:
    raise ValueError("Missing required bot configuration. Please set BOT_TOKEN, API_ID, and API_HASH as environment variables.")

app = Client("watermark_robot_2", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Allowed admin IDs
ALLOWED_ADMINS = [640815756, 5317760109]

# Global flag and state dictionaries
processing_active = False
user_state = {}
bulk_state = {}

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# Helper: Check Authorization
async def check_authorization(message: Message) -> bool:
    if message.chat.id not in ALLOWED_ADMINS:
        await message.reply_text("You are not authorized.")
        return False
    return True
