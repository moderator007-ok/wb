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

# Allowed admin IDs for /stop and /restart commands.
ALLOWED_ADMINS = [640815756, 5317760109]

# Global flag: only one processing task runs at a time.
processing_active = False

# ─── Logging Configuration ─────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ─── Initialize the Pyrogram Client ───────────────────────────
app = Client("watermark_robot_2", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
# Dictionary to track per-chat state.
user_state = {}

# ─── Progress Callback Factories (Throttled at 5% increments) ─────
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
                    logger.error("Error updating upload progress: " + str(e))
    return progress

# ─── Admin Commands: /stop and /restart ─────────────────────────
@app.on_message(filters.command("stop") & filters.private)
async def stop_cmd(client, message: Message):
    if message.chat.id not in ALLOWED_ADMINS:
        await message.reply_text("Unauthorized.")
        return
    global processing_active
    if processing_active:
        processing_active = False
        await message.reply_text("Processing task stopped.")
    else:
        await message.reply_text("No processing task is running.")

@app.on_message(filters.command("restart") & filters.private)
async def restart_cmd(client, message: Message):
    if message.chat.id not in ALLOWED_ADMINS:
        await message.reply_text("Unauthorized.")
        return
    await message.reply_text("Bot is restarting...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

# ─── Command Handlers for Watermark Modes ─────────────────────────
@app.on_message(filters.command("watermark") & filters.private)
async def watermark_cmd(client, message: Message):
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'watermark',
        'video_message': None,
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
        'temp_dir': None,
        'watermark_text': None,
        'font_size': None,
        'font_color': None,
        'step': 'await_video'
    }
    await message.reply_text("Send video.")

# ─── New Preset Command: /techmon ─────────────────────────
@app.on_message(filters.command("techmon") & filters.private)
async def techmon_cmd(client, message: Message):
    """
    Preset command for TECHMON.
    Sets the watermark text to @TechMonUPSC_2, font color to black, and font size to 36.
    This uses the watermarktm mode.
    """
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'watermarktm',
        'video_message': None,
        'temp_dir': None,
        'watermark_text': "@TechMonUPSC_2",
        'font_size': 36,
        'font_color': "black",
        'step': 'await_video'
    }
    await message.reply_text("TECHMON preset activated. Send video.")

@app.on_message(filters.command("harrypotter") & filters.private)
async def harrypotter_cmd(client, message: Message):
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'harrypotter',
        'video_message': None,
        'temp_dir': None,
        'watermark_text': "@VictoryAnthem",
        'font_size': 32,
        'font_color': "black",
        'step': 'await_video'
    }
    await message.reply_text("Harry Potter preset activated. Send video.")

@app.on_message(filters.command("overlay") & filters.private)
async def overlay_cmd(client, message: Message):
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
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'imgwatermark',
        'video_message': None,
        'image_message': None,
        'temp_dir': None,
        'step': 'await_video'
    }
    await message.reply_text("Send video for image watermarking.")

# ─── Video Handler ─────────────────────────────────────────────
@app.on_message(filters.private & (filters.video | filters.document))
async def video_handler(client, message: Message):
    global processing_active
    chat_id = message.chat.id
    if chat_id not in user_state:
        return
    state = user_state[chat_id]
    mode = state.get('mode')
    # For watermark and watermarktm modes:
    if mode in ['watermark', 'watermarktm']:
        # Check if watermark text is already preset.
        if state.get('watermark_text'):
            state['video_message'] = message
            state['step'] = 'processing'
            await message.reply_text("Video captured. Using preset watermark. Processing started.")
            processing_active = True
            try:
                await process_watermark(client, message, state, chat_id)
            finally:
                processing_active = False
        else:
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

# ─── Image Handler for /imgwatermark ─────────────────────────────
@app.on_message(filters.private & (filters.photo | filters.document))
async def image_handler(client, message: Message):
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

# ─── Text Handler for Inputs ─────────────────────────────────────
@app.on_message(filters.text & filters.private)
async def text_handler(client, message: Message):
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
    """
    Returns the video duration in seconds using ffprobe.
    First, it attempts to get the container (format) duration. If that duration
    is suspiciously low (e.g. less than 60 seconds) then it tries to get the 
    video stream's duration and returns the maximum of both.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    try:
        duration = float(stdout.decode().strip())
    except Exception as e:
        logger.error("Error getting format duration: " + str(e))
        duration = 0.0

    if duration < 60:
        proc2 = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout2, _ = await proc2.communicate()
        try:
            stream_duration = float(stdout2.decode().strip())
            duration = max(duration, stream_duration)
        except Exception as e:
            logger.error("Error getting stream duration: " + str(e))
    return duration

# ─── Modified process_watermark Function ──────────────────────────
async def process_watermark(client, message, state, chat_id):
    temp_dir = tempfile.mkdtemp()
    state['temp_dir'] = temp_dir
    progress_msg = await client.send_message(chat_id, "Downloading: 0%")
    video_msg = state['video_message']
    if video_msg.video:
        file_name = video_msg.video.file_name or f"{video_msg.video.file_id}.mp4"
    elif video_msg.document:
        file_name = video_msg.document.file_name or f"{video_msg.document.file_id}.mp4"
    else:
        file_name = "input_video.mp4"
    input_file_path = os.path.join(temp_dir, file_name)
    download_cb = create_download_progress(client, chat_id, progress_msg)
    logger.info("Starting video download...")
    await video_msg.download(file_name=input_file_path, progress=download_cb)
    logger.info("Video download completed.")
    await progress_msg.edit_text("Download complete. Watermarking started.")
    
    base_name = os.path.splitext(os.path.basename(input_file_path))[0]
    # Set font_path based on mode. For watermarktm (and /techmon) use your custom font.
    if state['mode'] == 'watermarktm':
        font_path = "cour.ttf"  # Ensure "cour.ttf" is available or specify its full path.
    else:
        font_path = "/usr/share/fonts/truetype/consola.ttf"

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
        "-c:v", "libx264", "-crf", "23", "-preset", "medium",
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
    
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        decoded_line = line.decode('utf-8').strip()
        logger.info(decoded_line)
    await proc.wait()
    
    if proc.returncode != 0:
        logger.error(f"Error processing watermark. Return code: {proc.returncode}")
        await message.reply_text("Error processing watermarked video.")
        shutil.rmtree(temp_dir)
        if chat_id in user_state:
            del user_state[chat_id]
        return
    
    await progress_msg.edit_text("Watermarking complete. Uploading: 0%")
    upload_cb = create_upload_progress(client, chat_id, progress_msg)
    try:
        logger.info("Uploading watermarked video...")
        await client.send_video(
            chat_id,
            video=output_file,
            caption="Here is your watermarked video.",
            progress=upload_cb
        )
        logger.info("Upload completed successfully.")
        await progress_msg.edit_text("Upload complete.")
    except Exception as e:
        logger.error(f"Error sending video for chat {chat_id}: {e}")
        await message.reply_text("Failed to send watermarked video.")
    shutil.rmtree(temp_dir)
    if chat_id in user_state:
        del user_state[chat_id]

# ─── Processing Functions for Overlay and Image Watermark (Unchanged) ─────────────────────────
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
        logger.error(f"Error pre-processing overlay video. Return code: {proc_pre.returncode}")
        await message.reply_text("Error pre-processing overlay video.")
        shutil.rmtree(temp_dir)
        if chat_id in user_state:
            del user_state[chat_id]
        return
    await progress_msg.edit_text("Pre-processing complete. Processing overlay: 0%")
    duration = state.get('duration', 30)
    base_name = os.path.splitext(os.path.basename(main_file_path))[0]
    seg1 = os.path.join(temp_dir, f"{base_name}_seg1.mp4")
    seg2 = os.path.join(temp_dir, f"{base_name}_seg2.mp4")
    output_file = os.path.join(temp_dir, f"{base_name}_overlay.mp4")
    ffmpeg_cmd1 = [
        FFMPEG_PATH,
        "-i", main_file_path,
        "-i", processed_overlay_path,
        "-filter_complex", f"[0:v][1:v]overlay=enable='lt(t,{duration})':x=0:y=0",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "copy",
        "-t", str(duration),
        seg1
    ]
    logger.info("Processing overlay segment 1...")
    proc1 = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd1,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
    while True:
        line = await proc1.stdout.readline()
        if not line:
            break
        line = line.decode('utf-8').strip()
        logger.info(line)
        m = time_pattern.search(line)
        if m:
            hours, minutes, seconds = m.groups()
            current_time = int(hours)*3600 + int(minutes)*60 + float(seconds)
            proc_percent = (current_time / duration) * 100
            await progress_msg.edit_text(f"Processing overlay: {proc_percent:.2f}%")
    await proc1.wait()
    if proc1.returncode != 0:
        logger.error(f"Error processing overlay segment 1. Return code: {proc1.returncode}")
        await message.reply_text("Error processing overlay (segment 1).")
        shutil.rmtree(temp_dir)
        if chat_id in user_state:
            del user_state[chat_id]
        return
    ffmpeg_cmd2 = [
        FFMPEG_PATH,
        "-ss", str(duration),
        "-i", main_file_path,
        "-c", "copy",
        seg2
    ]
    logger.info("Copying remaining overlay segment (segment 2)...")
    proc2 = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd2,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    while True:
        line = await proc2.stdout.readline()
        if not line:
            break
        logger.info(line.decode('utf-8').strip())
    await proc2.wait()
    if proc2.returncode != 0:
        logger.error(f"Error processing overlay segment 2. Return code: {proc2.returncode}")
        await message.reply_text("Error processing overlay (segment 2).")
        shutil.rmtree(temp_dir)
        if chat_id in user_state:
            del user_state[chat_id]
        return
    concat_file = os.path.join(temp_dir, "concat_list.txt")
    with open(concat_file, "w") as f:
        f.write(f"file '{seg1}'\n")
        f.write(f"file '{seg2}'\n")
    ffmpeg_concat_cmd = [
        FFMPEG_PATH,
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        output_file
    ]
    logger.info("Concatenating overlay segments...")
    proc3 = await asyncio.create_subprocess_exec(
        *ffmpeg_concat_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    while True:
        line = await proc3.stdout.readline()
        if not line:
            break
        logger.info(line.decode('utf-8').strip())
    await proc3.wait()
    if proc3.returncode != 0:
        logger.error(f"Error during concatenation. Return code: {proc3.returncode}")
        await message.reply_text("Error concatenating overlay segments.")
        shutil.rmtree(temp_dir)
        if chat_id in user_state:
            del user_state[chat_id]
        return
    await progress_msg.edit_text("Processing complete. Uploading: 0%")
    upload_cb = create_upload_progress(client, chat_id, progress_msg)
    try:
        logger.info("Uploading overlaid video...")
        await client.send_video(
            chat_id,
            video=output_file,
            caption="Here is your video with the overlay applied.",
            progress=upload_cb
        )
        logger.info("Upload completed successfully.")
        await progress_msg.edit_text("Upload complete.")
    except Exception as e:
        logger.error(f"Error sending video for chat {chat_id}: {e}")
        await message.reply_text("Failed to send overlaid video.")
    shutil.rmtree(temp_dir)
    if chat_id in user_state:
        del user_state[chat_id]

async def process_imgwatermark(client, message, state, chat_id):
    temp_dir = tempfile.mkdtemp()
    state['temp_dir'] = temp_dir
    progress_msg = await client.send_message(chat_id, "Downloading: 0%")
    video_msg = state['video_message']
    if video_msg.video:
        file_name = video_msg.video.file_name or f"{video_msg.video.file_id}.mp4"
    elif video_msg.document:
        file_name = video_msg.document.file_name or f"{video_msg.document.file_id}.mp4"
    else:
        file_name = "input_video.mp4"
    input_file_path = os.path.join(temp_dir, file_name)
    download_cb = create_download_progress(client, chat_id, progress_msg)
    logger.info("Starting video download for image watermark...")
    await video_msg.download(file_name=input_file_path, progress=download_cb)
    logger.info("Video download completed.")
    await progress_msg.edit_text("Video downloaded. Downloading image...")
    image_msg = state['image_message']
    if image_msg.photo:
        image_file = await client.download_media(image_msg)
    elif image_msg.document:
        image_file = await client.download_media(image_msg)
    else:
        image_file = None
    if not image_file:
        await message.reply_text("Error downloading image.")
        shutil.rmtree(temp_dir)
        if chat_id in user_state:
            del user_state[chat_id]
        return
    await progress_msg.edit_text("Image downloaded. Processing watermark...")
    output_file = os.path.join(temp_dir, "img_watermarked.mp4")
    filter_str = f"movie={image_file}[wm];[in][wm]overlay=10:10[out]"
    ffmpeg_cmd = [
        FFMPEG_PATH,
        "-i", input_file_path,
        "-vf", filter_str,
        "-c:v", "libx264", "-crf", "23", "-preset", "medium",
        "-c:a", "copy",
        output_file
    ]
    logger.info("Starting image watermarking process...")
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        logger.info(line.decode('utf-8').strip())
    await proc.wait()
    if proc.returncode != 0:
        logger.error(f"Error processing image watermark. Return code: {proc.returncode}")
        await message.reply_text("Error processing image watermarked video.")
        shutil.rmtree(temp_dir)
        if chat_id in user_state:
            del user_state[chat_id]
        return
    await progress_msg.edit_text("Processing complete. Uploading...")
    try:
        logger.info("Uploading image watermarked video...")
        await client.send_video(
            chat_id,
            video=output_file,
            caption="Here is your video with image watermark applied."
        )
        logger.info("Upload completed successfully.")
    except Exception as e:
        logger.error(f"Error sending video for chat {chat_id}: {e}")
        await message.reply_text("Failed to send image watermarked video.")
    shutil.rmtree(temp_dir)
    if chat_id in user_state:
        del user_state[chat_id]

if __name__ == "__main__":
    app.run()
