import os
import logging
from pyrogram.types import Message
from config import app, BOT_TOKEN, API_ID, API_HASH, FFMPEG_PATH

# Allowed admin IDs
ALLOWED_ADMINS = [640815756, 5317760109]

# Global flag and state dictionaries
processing_active = False
user_state = {}
bulk_state = {}
user_data = {}  # Ensure user_data is defined

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
