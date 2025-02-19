import os
import subprocess
import logging
import tempfile
import shutil
from pyrogram import Client, filters
from pyrogram.types import Message
from config import BOT_TOKEN, API_ID, API_HASH, FFMPEG_PATH

# ─── Logging Configuration ─────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ─── Initialize the Pyrogram Client ────────────────────────────
app = Client("watermark_robot_2", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Dictionary to track per-user session state.
user_state = {}

# ─── Progress Callbacks ──────────────────────────────────────────
async def download_progress(current, total):
    if total:
        percent = (current / total) * 100
        logger.info(f"Downloading: {current}/{total} bytes ({percent:.2f}%)")

async def upload_progress(current, total):
    if total:
        percent = (current / total) * 100
        logger.info(f"Uploading: {current}/{total} bytes ({percent:.2f}%)")

# ─── Command Handlers ────────────────────────────────────────────
@app.on_message(filters.command("watermark") & filters.private)
async def watermark_cmd(client, message: Message):
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'watermark',
        'video_message': None,
        'video_file': None,
        'temp_dir': None,
        'watermark_text': None,
        'font_size': None,
        'font_color': None,
        'step': 'await_video'
    }
    await message.reply_text("Send video.")

@app.on_message(filters.command("watermarktm") & filters.private)
async def watermarktm_cmd(client, message: Message):
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'watermarktm',
        'video_message': None,
        'video_file': None,
        'temp_dir': None,
        'watermark_text': None,
        'font_size': None,
        'font_color': None,
        'step': 'await_video'
    }
    await message.reply_text("Send video.")

# ─── Video Handler ───────────────────────────────────────────────
@app.on_message(filters.private & (filters.video | filters.document))
async def video_handler(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in user_state or user_state[chat_id].get('step') != 'await_video':
        return
    # Store the video message for later download.
    user_state[chat_id]['video_message'] = message
    user_state[chat_id]['step'] = 'await_text'
    await message.reply_text("Video captured. Now send text for watermark.")

# ─── Text Handler for Watermark Text, Font Size, and Color ──────
@app.on_message(filters.text & filters.private)
async def text_handler(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in user_state:
        return

    state = user_state[chat_id]
    current_step = state.get('step')

    if current_step == 'await_text':
        state['watermark_text'] = message.text.strip()
        state['step'] = 'await_size'
        await message.reply_text("Watermark text received. Please send font size (as a number).")
    elif current_step == 'await_size':
        try:
            size = int(message.text.strip())
            state['font_size'] = size
            state['step'] = 'await_color'
            await message.reply_text("Font size received. Now send color choice: 1 for black, 2 for white, 3 for red.")
        except ValueError:
            await message.reply_text("Invalid font size. Please send a number.")
    elif current_step == 'await_color':
        choice = message.text.strip()
        if choice == "1":
            state['font_color'] = "black"
        elif choice == "2":
            state['font_color'] = "white"
        elif choice == "3":
            state['font_color'] = "red"
        else:
            state['font_color'] = "white"  # default if unrecognized
        
        await message.reply_text("Color received. All inputs collected. Processing video, please wait...")

        # Create a temporary directory for processing.
        temp_dir = tempfile.mkdtemp()
        state['temp_dir'] = temp_dir

        # Download the video using the stored video message with progress logging.
        video_msg = state['video_message']
        file_name = None
        if video_msg.video:
            file_name = video_msg.video.file_name or f"{video_msg.video.file_id}.mp4"
        elif video_msg.document:
            file_name = video_msg.document.file_name or f"{video_msg.document.file_id}.mp4"
        if not file_name:
            file_name = "input_video.mp4"
        input_file_path = os.path.join(temp_dir, file_name)
        logger.info("Starting video download...")
        await video_msg.download(file_name=input_file_path, progress=download_progress)
        logger.info("Video download completed.")
        state['video_file'] = input_file_path

        # Prepare output file path based on mode.
        base_name = os.path.splitext(os.path.basename(input_file_path))[0]
        mode = state['mode']
        if mode == 'watermark':
            output_file = os.path.join(temp_dir, f"{base_name}_watermarked.mp4")
        elif mode == 'watermarktm':
            output_file = os.path.join(temp_dir, f"{base_name}_techmonX.mp4")
        else:
            output_file = os.path.join(temp_dir, f"{base_name}_watermarked.mp4")
        
        wm_text = state['watermark_text']
        font_size = state['font_size']
        font_color = state['font_color']
        
        # Choose the FFmpeg filter based on the mode.
        if mode == 'watermark':
            filter_str = (
                f"drawtext=text='{wm_text}':"
                f"fontcolor={font_color}:fontsize={font_size}:"
                "x=(w-text_w)/2:"
                "y=h-text_h-((h-text_h)*mod(t\\,30)/30)"
            )
        elif mode == 'watermarktm':
            # Add border to simulate bold text.
            filter_str = (
                f"drawtext=text='{wm_text}':"
                f"fontcolor={font_color}:fontsize={font_size}:"
                "borderw=2:bordercolor=black:"
                "x='mod(t\\,30)*30':"
                "y='mod(t\\,30)*15'"
            )
        else:
            filter_str = ""
        
        ffmpeg_command = [
            FFMPEG_PATH,
            "-i", input_file_path,
            "-vf", filter_str,
            "-c:v", "libx264", "-crf", "23", "-preset", "medium",
            "-c:a", "copy",
            "-progress", "pipe:1",
            "-nostats",
            output_file
        ]
        logger.info(f"Running FFmpeg command: {' '.join(ffmpeg_command)}")
        
        # Run FFmpeg with progress logging.
        proc = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    out_time_ms = int(line.split("=")[1])
                    out_time_sec = out_time_ms / 1e6
                    logger.info(f"Watermarking progress: {out_time_sec:.2f} seconds processed.")
                except Exception as e:
                    logger.error(f"Error parsing watermarking progress: {e}")
        proc.stdout.close()
        proc.wait()
        if proc.returncode != 0:
            logger.error(f"FFmpeg error for chat {chat_id}. Return code: {proc.returncode}")
            await message.reply_text("Error processing video.")
            shutil.rmtree(temp_dir)
            del user_state[chat_id]
            return

        try:
            logger.info("Uploading watermarked video...")
            await client.send_video(
                chat_id, 
                video=output_file, 
                caption="Here is your watermarked video.",
                progress=upload_progress
            )
            logger.info("Upload completed successfully.")
        except Exception as e:
            logger.error(f"Error sending video for chat {chat_id}: {e}")
            await message.reply_text("Failed to send watermarked video.")
        
        shutil.rmtree(temp_dir)
        del user_state[chat_id]

if __name__ == "__main__":
    app.run()
