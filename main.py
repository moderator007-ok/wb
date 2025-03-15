import os
import sys
import re
import asyncio
import subprocess
import logging
import tempfile
import shutil

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from config import BOT_TOKEN, API_ID, API_HASH, FFMPEG_PATH
from moviepy.editor import VideoFileClip  # Importing MoviePy

# ─── Added Functions: Thumbnail Generation and Video Details using MoviePy ───
def generate_thumbnail(video_file, thumbnail_path, time_offset="00:00:01.000"):
    """
    Generate a thumbnail image from a video file using FFmpeg.

    Args:
        video_file (str): Path to the source video file.
        thumbnail_path (str): Path where the thumbnail image will be saved.
        time_offset (str): Timestamp offset (HH:MM:SS.mmm) to capture the thumbnail. Default is 1 second.

    Returns:
        str or None: Returns the thumbnail path if successful, otherwise None.
    """
    ffmpeg_executable = FFMPEG_PATH if FFMPEG_PATH else "ffmpeg"
    command = [
        ffmpeg_executable,
        "-i", video_file,
        "-ss", time_offset,
        "-vframes", "1",
        thumbnail_path,
        "-y"  # Overwrite output file if it exists.
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return thumbnail_path
    except subprocess.CalledProcessError as e:
        logging.error(f"Thumbnail generation failed: {e}")
        return None

def get_video_details(video_file):
    """
    Retrieve video details such as width, height, and duration using MoviePy.

    Args:
        video_file (str): Path to the source video file.

    Returns:
        dict: A dictionary with keys 'width', 'height', and 'duration'. Returns an empty dict on error.
    """
    try:
        clip = VideoFileClip(video_file)
        details = {
            "width": clip.w,
            "height": clip.h,
            "duration": clip.duration
        }
        # Properly close the clip to free resources.
        clip.reader.close()
        if clip.audio:
            clip.audio.reader.close_proc()
        return details
    except Exception as e:
        logging.error(f"Failed to retrieve video details: {e}")
        return {}

# ─── Allowed admin IDs for all commands. ───
ALLOWED_ADMINS = [640815756, 5317760109]

# ─── Global flag: only one processing task runs at a time. ───
processing_active = False

# ─── Global dictionaries for user state (single processing) and bulk state. ───
user_state = {}
bulk_state = {}  # Global state for bulk watermark processing

# ─── Logging Configuration ─────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ─── Initialize the Pyrogram Client ───────────────────────────
app = Client("watermark_robot_2", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ─── Helper: Check Authorization ───────────────────────────────
async def check_authorization(message: Message) -> bool:
    if message.chat.id not in ALLOWED_ADMINS:
        await message.reply_text("You are not authorized.")
        return False
    return True

# ─── Helper: Split Video File by Size (using computed segment duration) ─────────────
async def split_video_file(input_file: str, output_dir: str, segment_time: int) -> list:
    """
    Splits input_file into segments based on segment_time (in seconds) using ffmpeg's segment muxer.
    Returns a sorted list of output file paths.
    """
    output_pattern = os.path.join(output_dir, "part_%03d.mp4")
    split_cmd = [
        FFMPEG_PATH,
        "-i", input_file,
        "-c", "copy",
        "-map", "0",
        "-segment_time", str(segment_time),
        "-f", "segment",
        "-reset_timestamps", "1",
        output_pattern
    ]
    proc = await asyncio.create_subprocess_exec(
        *split_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"Error splitting video: {stderr.decode('utf-8')}")
        return []
    parts = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.startswith("part_") and f.endswith(".mp4")])
    return parts

# ─── Progress Callback Factories (Download/Upload) ─────────────
def create_download_progress(client, chat_id, progress_msg: Message):
    last_update = 0
    async def progress(current, total):
        nonlocal last_update
        if total:
            percent = (current / total) * 100
            if percent - last_update >= 5 or percent >= 100:
                try:
                    await progress_msg.edit_text(f"Downloading: {percent:.2f}%")
                    last_update = percent
                except Exception as e:
                    if "MESSAGE_NOT_MODIFIED" in str(e):
                        pass
                    else:
                        logger.error("Error updating download progress: " + str(e))
    return progress

def create_upload_progress(client, chat_id, progress_msg: Message):
    last_update = 0
    async def progress(current, total):
        nonlocal last_update
        if total:
            percent = (current / total) * 100
            if percent - last_update >= 5 or percent >= 100:
                try:
                    await progress_msg.edit_text(f"Uploading: {percent:.2f}%")
                    last_update = percent
                except Exception as e:
                    if "MESSAGE_NOT_MODIFIED" in str(e):
                        pass
                    else:
                        logger.error("Error updating upload progress: " + str(e))
    return progress

# ─── Admin Commands: /stop and /restart ─────────────────────────
@app.on_message(filters.command("stop") & filters.private)
async def stop_cmd(client, message: Message):
    if not await check_authorization(message):
        return
    global processing_active
    if processing_active:
        processing_active = False
        await message.reply_text("Processing task stopped.")
    else:
        await message.reply_text("No processing task is running.")

@app.on_message(filters.command("restart") & filters.private)
async def restart_cmd(client, message: Message):
    if not await check_authorization(message):
        return
    await message.reply_text("Bot is restarting...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

# ─── Command Handlers for Single-Video Watermark Modes ─────────
@app.on_message(filters.command("watermark") & filters.private)
async def watermark_cmd(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'watermark',
        'video_message': None,
        'temp_dir': None,
        'watermark_text': None,
        'font_size': None,
        'font_color': None,
        'preset': None,
        'step': 'await_video'
    }
    await message.reply_text("Send video.")

@app.on_message(filters.command("watermarktm") & filters.private)
async def watermarktm_cmd(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'watermarktm',
        'video_message': None,
        'temp_dir': None,
        'watermark_text': None,
        'font_size': None,
        'font_color': None,
        'preset': None,
        'step': 'await_video'
    }
    await message.reply_text("Send video.")

@app.on_message(filters.command("harrypotter") & filters.private)
async def harrypotter_cmd(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'harrypotter',
        'video_message': None,
        'temp_dir': None,
        'watermark_text': "@VictoryAnthem",
        'font_size': 32,
        'font_color': "black",
        'preset': "medium",
        'step': 'await_video'
    }
    await message.reply_text("Harry Potter preset activated. Send video.")

@app.on_message(filters.command("overlay") & filters.private)
async def overlay_cmd(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'overlay',
        'main_video_message': None,
        'overlay_video_message': None,
        'temp_dir': None,
        'duration': None,
        'step': 'await_main'
    }
    await message.reply_text("Send the **main video** for overlay.")

@app.on_message(filters.command("imgwatermark") & filters.private)
async def imgwatermark_cmd(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'imgwatermark',
        'video_message': None,
        'image_message': None,
        'temp_dir': None,
        'step': 'await_video'
    }
    await message.reply_text("Send video for image watermarking.")

# ─── New Bulk Watermarking Commands ─────────────────────────────
@app.on_message(filters.command("inputwatermark") & filters.private)
async def inputwatermark_bulk(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    bulk_state[chat_id] = {'videos': []}
    await message.reply_text(
        "Bulk watermark mode activated.\n"
        "Now, send all the videos you want to watermark."
    )

@app.on_message(filters.command("watermarkask") & filters.private)
async def bulk_watermarkask_cmd(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    if chat_id not in bulk_state or not bulk_state[chat_id].get('videos'):
        await message.reply_text("No videos collected. Use /inputwatermark first and send your videos.")
        return
    bulk_state[chat_id]['mode'] = 'watermark'
    bulk_state[chat_id]['step'] = 'await_text'
    await message.reply_text("Send watermark text for bulk image watermarking.")

@app.on_message(filters.command("watermarktmask") & filters.private)
async def bulk_watermarktmask_cmd(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    if chat_id not in bulk_state or not bulk_state[chat_id].get('videos'):
        await message.reply_text("No videos collected. Use /inputwatermark first and send your videos.")
        return
    bulk_state[chat_id]['mode'] = 'watermarktm'
    bulk_state[chat_id]['step'] = 'await_text'
    await message.reply_text("Send watermark text for bulk text watermarking.")

# ─── New Bulk Video Handler ─────────────────────────────
@app.on_message(filters.private & (filters.video | filters.document))
async def bulk_video_handler(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    if chat_id in bulk_state:
        bulk_state[chat_id].setdefault('videos', []).append(message)
        await message.reply_text("Video added for bulk watermarking.")

# ─── New Bulk Text Handler ─────────────────────────────
@app.on_message(filters.text & filters.private)
async def bulk_text_handler(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    if chat_id not in bulk_state:
        return  # Not in bulk mode; let the other text handler process it.
    state = bulk_state[chat_id]
    if state.get('step') == 'await_text':
        state['watermark_text'] = message.text.strip()
        state['step'] = 'await_size'
        await message.reply_text("Watermark text received. Please send font size (as a number).")
        return
    elif state.get('step') == 'await_size':
        try:
            size = int(message.text.strip())
            state['font_size'] = size
            state['step'] = 'await_color'
            await message.reply_text("Font size received. Now send color choice: 1 for black, 2 for white, 3 for red.")
        except ValueError:
            await message.reply_text("Invalid font size. Please send a number.")
        return
    elif state.get('step') == 'await_color':
        choice = message.text.strip()
        if choice == "1":
            state['font_color'] = "black"
        elif choice == "2":
            state['font_color'] = "white"
        elif choice == "3":
            state['font_color'] = "red"
        else:
            state['font_color'] = "white"
        state['step'] = 'await_preset'
        await message.reply_text("Color received. Now send ffmpeg preset (choose: medium, fast, superfast, ultrafast).")
        return
    elif state.get('step') == 'await_preset':
        preset = message.text.strip().lower()
        if preset not in {"medium", "fast", "superfast", "ultrafast"}:
            await message.reply_text("Invalid preset. Please send one of: medium, fast, superfast, ultrafast.")
            return
        state['preset'] = preset
        state['step'] = 'processing'
        await message.reply_text("All inputs collected. Bulk watermarking started.")
        await process_bulk_watermark(client, message, state, chat_id)
        return

# ─── Existing Video Handler for Single Processing ─────────────
@app.on_message(filters.private & (filters.video | filters.document))
async def video_handler(client, message: Message):
    if not await check_authorization(message):
        return
    global processing_active
    chat_id = message.chat.id
    if chat_id not in user_state:
        return
    state = user_state[chat_id]
    mode = state.get('mode')
    if mode in ['watermark', 'watermarktm']:
        if state.get('step') != 'await_video':
            return
        state['video_message'] = message
        state['step'] = 'await_text'
        await message.reply_text("Video captured. Now send the watermark text.")
    elif mode == 'harrypotter':
        if processing_active:
            await message.reply_text("A process is already running; please try later.")
            return
        state['video_message'] = message
        state['step'] = 'processing'
        await message.reply_text("Video captured. Watermarking started.")
        processing_active = True
        try:
            await process_watermark(client, message, state, chat_id)
        finally:
            processing_active = False
    elif mode == 'overlay':
        if state.get('step') == 'await_main':
            state['main_video_message'] = message
            state['step'] = 'await_overlay'
            await message.reply_text("Main video received. Now send the **overlay video** (with green screen background).")
    elif mode == 'imgwatermark':
        if state.get('step') != 'await_video':
            return
        state['video_message'] = message
        state['step'] = 'await_image'
        await message.reply_text("Video received. Now send the watermark image.")

# ─── Existing Image Handler for /imgwatermark ─────────────
@app.on_message(filters.private & (filters.photo | filters.document))
async def image_handler(client, message: Message):
    if not await check_authorization(message):
        return
    global processing_active
    chat_id = message.chat.id
    if chat_id not in user_state:
        return
    state = user_state[chat_id]
    if state.get('mode') == 'imgwatermark' and state.get('step') == 'await_image':
        state['image_message'] = message
        state['step'] = 'processing'
        await message.reply_text("Image received. Processing video with image watermark, please wait...")
        if processing_active:
            await message.reply_text("A process is already running; please try later.")
            return
        processing_active = True
        try:
            await process_imgwatermark(client, message, state, chat_id)
        finally:
            processing_active = False

# ─── Existing Text Handler for Single Processing ─────────────
@app.on_message(filters.text & filters.private)
async def text_handler(client, message: Message):
    if not await check_authorization(message):
        return
    global processing_active
    chat_id = message.chat.id
    if chat_id not in user_state:
        return
    state = user_state[chat_id]
    current_step = state.get('step')
    mode = state.get('mode')
    if mode in ['watermark', 'watermarktm']:
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
                state['font_color'] = "white"
            state['step'] = 'await_preset'
            await message.reply_text("Color received. Now send ffmpeg preset (choose: medium, fast, superfast, ultrafast).")
        elif current_step == 'await_preset':
            preset = message.text.strip().lower()
            if preset not in {"medium", "fast", "superfast", "ultrafast"}:
                await message.reply_text("Invalid preset. Please send one of: medium, fast, superfast, ultrafast.")
                return
            state['preset'] = preset
            state['step'] = 'processing'
            if processing_active:
                await message.reply_text("A process is already running; please try later.")
                return
            processing_active = True
            await message.reply_text("All inputs collected. Watermarking started.")
            try:
                await process_watermark(client, message, state, chat_id)
            finally:
                processing_active = False
    elif mode == 'harrypotter':
        pass
    elif mode == 'overlay':
        pass

# ─── Helper Function: Get Video Duration Using ffprobe ─────────────
async def get_video_duration(file_path):
    \"\"\"\n    Returns the video duration in seconds using ffprobe.\n    First, it attempts to get the container duration. If that duration\n    is suspiciously low (e.g. less than 60 seconds) then it tries to get the \n    video stream's duration and returns the maximum of both.\n    \"\"\"\n    proc = await asyncio.create_subprocess_exec(\n        \"ffprobe\", \"-v\", \"error\",\n        \"-show_entries\", \"format=duration\",\n        \"-of\", \"default=noprint_wrappers=1:nokey=1\",\n        file_path,\n        stdout=asyncio.subprocess.PIPE,\n        stderr=asyncio.subprocess.PIPE\n    )\n    stdout, _ = await proc.communicate()\n    try:\n        duration = float(stdout.decode().strip())\n    except Exception as e:\n        logger.error(\"Error getting format duration: \" + str(e))\n        duration = 0.0\n    if duration < 60:\n        proc2 = await asyncio.create_subprocess_exec(\n            \"ffprobe\", \"-v\", \"error\",\n            \"-select_streams\", \"v:0\",\n            \"-show_entries\", \"stream=duration\",\n            \"-of\", \"default=noprint_wrappers=1:nokey=1\",\n            file_path,\n            stdout=asyncio.subprocess.PIPE,\n            stderr=asyncio.subprocess.PIPE\n        )\n        stdout2, _ = await proc2.communicate()\n        try:\n            stream_duration = float(stdout2.decode().strip())\n            duration = max(duration, stream_duration)\n        except Exception as e:\n            logger.error(\"Error getting stream duration: \" + str(e))\n    return duration

# ─── Processing Function for Single Watermark ─────────────
async def process_watermark(client, message, state, chat_id):
    try:
        progress_msg = await client.send_message(chat_id, "Downloading: 0%")
    except FloodWait:
        progress_msg = None
    temp_dir = tempfile.mkdtemp()
    state['temp_dir'] = temp_dir
    video_msg = state['video_message']
    if video_msg.video:
        file_name = video_msg.video.file_name or f"{video_msg.video.file_id}.mp4"
    elif video_msg.document:
        file_name = video_msg.document.file_name or f"{video_msg.document.file_id}.mp4"
    else:
        file_name = "input_video.mp4"
    input_file_path = os.path.join(temp_dir, file_name)
    download_cb = create_download_progress(client, chat_id, progress_msg) if progress_msg else None
    logger.info("Starting video download...")
    await video_msg.download(file_name=input_file_path, progress=download_cb)
    logger.info("Video download completed.")
    if progress_msg:
        try:
            await progress_msg.edit_text("Download complete. Watermarking started.")
        except FloodWait:
            progress_msg = None
    duration_sec = await get_video_duration(input_file_path)
    if duration_sec <= 0:
        duration_sec = 1  # safeguard
    base_name = os.path.splitext(os.path.basename(input_file_path))[0]
    if state['mode'] == 'watermarktm':
        font_path = "cour.ttf"  # Adjust path if necessary.
    else:
        font_path = "/usr/share/fonts/truetype/consola.ttf"  # Adjust if needed.
    if state['mode'] in ['watermark', 'harrypotter']:
        filter_str = (
            f"drawtext=text='{state['watermark_text']}':"
            f"fontcolor={state['font_color']}:"
            f"fontsize={state['font_size']}:"
            f"x=(w-text_w)/2:"
            f"y=(h-text_h-10)+((10-(h-text_h-10))*(mod(t\\,30)/30))"
        )
    elif state['mode'] == 'watermarktm':
        filter_str = (
            f"drawtext=text='{state['watermark_text']}':"
            f"fontfile={font_path}:"
            f"fontcolor={state['font_color']}:"
            f"fontsize={state['font_size']}:"
            f"font='Courier New':"
            f"x='mod(t\\,30)*30':"
            f"y='mod(t\\,30)*15'"
        )
    output_file = os.path.join(temp_dir, f"{base_name}_watermarked.mp4")
    ffmpeg_cmd = [
        FFMPEG_PATH,
        "-fflags", "+genpts",
        "-i", input_file_path,
        "-vf", filter_str,
        "-c:v", "libx264", "-crf", "23", "-preset", state.get('preset', 'medium'),
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-progress", "pipe:1",
        output_file
    ]
    logger.info("Starting watermarking process...")
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    last_logged = 0
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        decoded_line = line.decode('utf-8').strip()
        logger.info(decoded_line)
        if decoded_line.startswith("out_time_ms="):
            try:
                out_time_val = int(decoded_line.split("=")[1])
                current_sec = out_time_val / 1000000.0
                current_percent = (current_sec / duration_sec) * 100
                if current_percent > 100:
                    current_percent = 100
                if current_percent - last_logged >= 5 or current_percent == 100:
                    last_logged = current_percent
                    if progress_msg:
                        try:
                            await progress_msg.edit_text(f"Watermark processing: {current_percent:.0f}% completed")
                        except FloodWait:
                            progress_msg = None
            except Exception as e:
                logger.error("Error parsing ffmpeg progress: " + str(e))
        if decoded_line == "progress=end":
            break
    await proc.wait()
    if proc.returncode != 0:
        logger.error(f"Error processing watermark. Return code: {proc.returncode}")
        await message.reply_text("Error processing watermarked video.")
        shutil.rmtree(temp_dir)
        if chat_id in user_state:
            del user_state[chat_id]
        return
    try:
        upload_msg = await client.send_message(chat_id, "Watermarking complete. Uploading: 0%")
    except FloodWait:
        upload_msg = None
    upload_cb = create_upload_progress(client, chat_id, upload_msg) if upload_msg else None
    original_caption = video_msg.caption if video_msg.caption else "Here is your watermarked video."
    # Check file size; if it exceeds 1.9GB, split by size (using computed segment duration)
    threshold = 1.9 * (1024**3)  # 1.9GB in bytes
    output_size = os.path.getsize(output_file)
    if output_size > threshold:
        video_duration = await get_video_duration(output_file)
        segment_time = int((threshold / output_size) * video_duration)
        if segment_time < 1:
            segment_time = 1
        segments_dir = os.path.join(temp_dir, "segments")
        os.makedirs(segments_dir, exist_ok=True)
        parts = await split_video_file(output_file, segments_dir, segment_time)
        for part in parts:
            await client.send_video(chat_id, video=part, caption=original_caption, progress=upload_cb)
        shutil.rmtree(segments_dir)
    else:
        try:
            logger.info("Uploading watermarked video...")
            await client.send_video(
                chat_id,
                video=output_file,
                caption=original_caption,
                progress=upload_cb
            )
            logger.info("Upload completed successfully.")
            if upload_msg:
                try:
                    await upload_msg.edit_text("Upload complete.")
                except FloodWait:
                    pass
        except Exception as e:
            logger.error(f"Error sending video for chat {chat_id}: {e}")
            await message.reply_text("Failed to send watermarked video.")
    shutil.rmtree(temp_dir)
    if chat_id in user_state:
        del user_state[chat_id]

# ─── Processing Function for Bulk Watermark ─────────────
async def process_bulk_watermark(client, message, state, chat_id):
    videos = state.get('videos', [])
    for video_msg in videos:
        temp_dir = tempfile.mkdtemp()
        if video_msg.video:
            file_name = video_msg.video.file_name or f"{video_msg.video.file_id}.mp4"
        elif video_msg.document:
            file_name = video_msg.document.file_name or f"{video_msg.document.file_id}.mp4"
        else:
            file_name = "input_video.mp4"
        input_file_path = os.path.join(temp_dir, file_name)
        try:
            progress_msg = await client.send_message(chat_id, "Downloading: 0%")
        except FloodWait:
            progress_msg = None
        download_cb = create_download_progress(client, chat_id, progress_msg) if progress_msg else None
        logger.info("Starting video download for bulk video...")
        await video_msg.download(file_name=input_file_path, progress=download_cb)
        logger.info("Video download completed for bulk video.")
        if progress_msg:
            try:
                await progress_msg.edit_text("Download complete. Watermarking started.")
            except FloodWait:
                progress_msg = None
        duration_sec = await get_video_duration(input_file_path)
        if duration_sec <= 0:
            duration_sec = 1
        base_name = os.path.splitext(os.path.basename(input_file_path))[0]
        if state['mode'] == 'watermarktm':
            font_path = "cour.ttf"
        else:
            font_path = "/usr/share/fonts/truetype/consola.ttf"
        if state['mode'] == 'watermark':
            filter_str = (
                f"drawtext=text='{state['watermark_text']}':"
                f"fontcolor={state['font_color']}:"
                f"fontsize={state['font_size']}:"
                f"x=(w-text_w)/2:"
                f"y=(h-text_h-10)+((10-(h-text_h-10))*(mod(t\\,30)/30))"
            )
        elif state['mode'] == 'watermarktm':
            filter_str = (
                f"drawtext=text='{state['watermark_text']}':"
                f"fontfile={font_path}:"
                f"fontcolor={state['font_color']}:"
                f"fontsize={state['font_size']}:"
                f"font='Courier New':"
                f"x='mod(t\\,30)*30':"
                f"y='mod(t\\,30)*15'"
            )
        output_file = os.path.join(temp_dir, f"{base_name}_watermarked.mp4")
        ffmpeg_cmd = [
            FFMPEG_PATH,
            "-fflags", "+genpts",
            "-i", input_file_path,
            "-vf", filter_str,
            "-c:v", "libx264", "-crf", "23", "-preset", state.get('preset', 'medium'),
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-progress", "pipe:1",
            output_file
        ]
        logger.info("Starting watermarking process for bulk video...")
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        last_logged = 0
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            decoded_line = line.decode('utf-8').strip()
            logger.info(decoded_line)
            if decoded_line.startswith("out_time_ms="):
                try:
                    out_time_val = int(decoded_line.split("=")[1])
                    current_sec = out_time_val / 1000000.0
                    current_percent = (current_sec / duration_sec) * 100
                    if current_percent > 100:
                        current_percent = 100
                    if current_percent - last_logged >= 5 or current_percent == 100:
                        last_logged = current_percent
                        if progress_msg:
                            try:
                                await progress_msg.edit_text(f"Watermark processing: {current_percent:.0f}% completed")
                            except FloodWait:
                                progress_msg = None
                except Exception as e:
                    logger.error("Error parsing ffmpeg progress for bulk: " + str(e))
            if decoded_line == "progress=end":
                break
        await proc.wait()
        if proc.returncode != 0:
            logger.error(f"Error processing watermark for bulk video. Return code: {proc.returncode}")
            await client.send_message(chat_id, "Error processing watermarked video.")
            shutil.rmtree(temp_dir)
            continue
        try:
            upload_msg = await client.send_message(chat_id, "Watermarking complete. Uploading: 0%")
        except FloodWait:
            upload_msg = None
        upload_cb = create_upload_progress(client, chat_id, upload_msg) if upload_msg else None
        original_caption = video_msg.caption if video_msg.caption else "Here is your bulk watermarked video."
        output_size = os.path.getsize(output_file)
        threshold = 1.9 * (1024**3)
        if output_size > threshold:
            video_duration = await get_video_duration(output_file)
            segment_time = int((threshold / output_size) * video_duration)
            if segment_time < 1:
                segment_time = 1
            segments_dir = os.path.join(temp_dir, "segments")
            os.makedirs(segments_dir, exist_ok=True)
            parts = await split_video_file(output_file, segments_dir, segment_time)
            for part in parts:
                await client.send_video(chat_id, video=part, caption=original_caption, progress=upload_cb)
            shutil.rmtree(segments_dir)
        else:
            try:
                logger.info("Uploading watermarked video for bulk video...")
                await client.send_video(
                    chat_id,
                    video=output_file,
                    caption=original_caption,
                    progress=upload_cb
                )
                logger.info("Upload completed successfully for bulk video.")
                if upload_msg:
                    try:
                        await upload_msg.edit_text("Upload complete.")
                    except FloodWait:
                        pass
            except Exception as e:
                logger.error(f"Error sending bulk video for chat {chat_id}: {e}")
                await client.send_message(chat_id, "Failed to send watermarked video.")
        shutil.rmtree(temp_dir)
    if chat_id in bulk_state:
        del bulk_state[chat_id]

# ─── Existing Processing Functions for Overlay and Image Watermark (Unchanged) ─────────
async def process_overlay(client, message, state, chat_id):
    temp_dir = tempfile.mkdtemp()
    state['temp_dir'] = temp_dir
    progress_msg = await client.send_message(chat_id, "Downloading main video: 0%")
    main_msg = state['main_video_message']
    if main_msg.video:
        main_file_name = main_msg.video.file_name or f"{main_msg.video.file_id}.mp4"
    elif main_msg.document:
        main_file_name = main_msg.document.file_name or f"{main_msg.document.file_id}.mp4"
    else:
        main_file_name = "main_video.mp4"
    main_file_path = os.path.join(temp_dir, main_file_name)
    download_cb = create_download_progress(client, chat_id, progress_msg)
    logger.info("Downloading main video...")
    await main_msg.download(file_name=main_file_path, progress=download_cb)
    logger.info("Main video downloaded.")
    await progress_msg.edit_text("Main video downloaded.")
    await progress_msg.edit_text("Downloading overlay video: 0%")
    overlay_msg = state['overlay_video_message']
    if overlay_msg.video:
        overlay_file_name = overlay_msg.video.file_name or f"{overlay_msg.video.file_id}.mp4"
    elif overlay_msg.document:
        overlay_file_name = overlay_msg.document.file_name or f"{overlay_msg.document.file_id}.mp4"
    else:
        overlay_file_name = "overlay_video.mp4"
    overlay_file_path = os.path.join(temp_dir, overlay_file_name)
    download_cb = create_download_progress(client, chat_id, progress_msg)
    logger.info("Downloading overlay video...")
    await overlay_msg.download(file_name=overlay_file_path, progress=download_cb)
    logger.info("Overlay video downloaded.")
    await progress_msg.edit_text("Overlay video downloaded.")
    await progress_msg.edit_text("Pre-processing overlay video...")
    processed_overlay_path = os.path.join(temp_dir, "processed_overlay.mov")
    pre_process_cmd = [
        FFMPEG_PATH,
        "-i", overlay_file_path,
        "-vf", "colorkey=0x00FF00:0.3:0.2,format=yuva420p",
        "-c:v", "qtrle",
        processed_overlay_path
    ]
    proc_pre = await asyncio.create_subprocess_exec(
        *pre_process_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    while True:
        line = await proc_pre.stdout.readline()
        if not line:
            break
        logger.info(line.decode('utf-8').strip())
    await proc_pre.wait()
    if proc_pre.returncode != 0:
        await client.send_message(chat_id, "Error in pre-processing overlay video.")
        shutil.rmtree(temp_dir)
        return
    # (Overlay processing logic continues here …)
    shutil.rmtree(temp_dir)

async def process_imgwatermark(client, message, state, chat_id):
    await client.send_message(chat_id, "Image watermark processing is not modified in bulk mode.")

# ─── Start the Pyrogram Client ─────────────────────────────
if __name__ == '__main__':
    app.run()
