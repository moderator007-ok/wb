import os
from pyrogram import Client

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")  # Defaults to using 'ffmpeg' from the system PATH

if not BOT_TOKEN or API_ID == 0 or not API_HASH:
    raise ValueError("Missing required bot configuration. Please set BOT_TOKEN, API_ID, and API_HASH as environment variables.")

# Create a single Client instance for the bot
app = Client("watermark_robot_2", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
