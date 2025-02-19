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

# /watermark and /watermarktm commands remain unchanged.
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

# New /overlay command: Ask for main video first.
@app.on_message(filters.command("overlay") & filters.private)
async def overlay_cmd(client, message: Message):
    chat_id = message.chat.id
    user_state[chat_id] = {
        'mode': 'overlay',
        'main_video_message': None,
        'overlay_video_message': None,
        'temp_dir': None,
        'step': 'await_main'
    }
    await message.reply_text("Send the **main video** for overlay.")

# ─── Video Handler ───────────────────────────────────────────────
@app.on_message(filters.private & (filters.video | filters.document))
async def video_handler(client, message: Message):
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
        await message.reply_text("Video captured. Now send text for watermark.")

    elif mode == 'overlay':
        current_step = state.get('step')
        if current_step == 'await_main':
            state['main_video_message'] = message
            state['step'] = 'await_overlay'
            await message.reply_text("Main video received. Now send the **overlay video** (green screen background).")
        elif current_step == 'await_overlay':
            state['overlay_video_message'] = message
            state['step'] = 'processing'
            await message.reply_text("Overlay video received. Processing, please wait...")
            await process_overlay(client, message, state, chat_id)

# ─── Text Handler for Watermark Modes (unchanged) ──────────────
@app.on_message(filters.text & filters.private)
async def text_handler(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in user_state:
        return
    state = user_state[chat_id]
    current_step = state.get('step')
    if state.get('mode') in ['watermark', 'watermarktm']:
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
            await message.reply_text("Color received. All inputs collected. Processing video, please wait...")
            await process_watermark(client, message, state, chat_id)

# ─── Processing Functions ───────────────────────────────────────

# Existing process_watermark function remains unchanged.
async def process_watermark(client, message, state, chat_id):
    temp_dir = tempfile.mkdtemp()
    state['temp_dir'] = temp_dir
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

    base_name = os.path.splitext(os.path.basename(input_file_path))[0]
    mode = state['mode']
    if mode == 'watermark':
        output_file = os.path.join(temp_dir, f"{base_name}_watermarked.mp4")
        filter_str = (
            f"drawtext=text='{state['watermark_text']}':"
            f"fontcolor={state['font_color']}:fontsize={state['font_size']}:"
            "x=(w-text_w)/2:"
            "y=h-text_h-10"
        )
    elif mode == 'watermarktm':
        output_file = os.path.join(temp_dir, f"{base_name}_techmonX.mp4")
        filter_str = (
            f"drawtext=text='{state['watermark_text']}':"
            f"fontcolor={state['font_color']}:fontsize={state['font_size']}:"
            "borderw=2:bordercolor=black:"
            "x='mod(t\\,30)*30':"
            "y='mod(t\\,30)*15'"
        )
    else:
        output_file = os.path.join(temp_dir, f"{base_name}_watermarked.mp4")
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

# New process_overlay function for /overlay command.
async def process_overlay(client, message, state, chat_id):
    # Create a temporary directory.
    temp_dir = tempfile.mkdtemp()
    state['temp_dir'] = temp_dir

    # Download the main video.
    main_msg = state['main_video_message']
    main_file_name = (main_msg.video.file_name if main_msg.video 
                      else main_msg.document.file_name if main_msg.document 
                      else "main_video.mp4")
    main_file_path = os.path.join(temp_dir, main_file_name)
    logger.info("Downloading main video...")
    await main_msg.download(file_name=main_file_path, progress=download_progress)
    logger.info("Main video downloaded.")

    # Download the overlay video.
    overlay_msg = state['overlay_video_message']
    overlay_file_name = (overlay_msg.video.file_name if overlay_msg.video 
                         else overlay_msg.document.file_name if overlay_msg.document 
                         else "overlay_video.mp4")
    overlay_file_path = os.path.join(temp_dir, overlay_file_name)
    logger.info("Downloading overlay video...")
    await overlay_msg.download(file_name=overlay_file_path, progress=download_progress)
    logger.info("Overlay video downloaded.")

    # Pre-process the overlay video to remove green background.
    # The command uses the colorkey filter and outputs a MOV file with an alpha channel.
    processed_overlay_path = os.path.join(temp_dir, "processed_overlay.mov")
    pre_process_cmd = [
        FFMPEG_PATH,
        "-i", overlay_file_path,
        "-vf", "colorkey=0x00FF00:0.3:0.2,format=yuva420p",
        "-c:v", "qtrle",  # qtrle supports alpha in MOV.
        processed_overlay_path
    ]
    logger.info("Pre-processing overlay video to remove green screen...")
    proc = subprocess.Popen(pre_process_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        logger.info(line.strip())
    proc.stdout.close()
    proc.wait()
    if proc.returncode != 0:
        logger.error(f"Error pre-processing overlay video. Return code: {proc.returncode}")
        await message.reply_text("Error pre-processing overlay video.")
        shutil.rmtree(temp_dir)
        del user_state[chat_id]
        return

    # Composite the processed overlay onto the main video.
    final_output_path = os.path.join(temp_dir, "output_overlay.mp4")
    composite_cmd = [
        FFMPEG_PATH,
        "-i", main_file_path,
        "-i", processed_overlay_path,
        "-filter_complex", "[0:v][1:v]overlay=0:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "copy",
        final_output_path
    ]
    logger.info(f"Running overlay composite command: {' '.join(composite_cmd)}")
    proc = subprocess.Popen(composite_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        logger.info(line.strip())
    proc.stdout.close()
    proc.wait()
    if proc.returncode != 0:
        logger.error(f"Error during overlay composite. Return code: {proc.returncode}")
        await message.reply_text("Error processing overlay video.")
        shutil.rmtree(temp_dir)
        del user_state[chat_id]
        return

    try:
        logger.info("Uploading overlaid video...")
        await client.send_video(
            chat_id,
            video=final_output_path,
            caption="Here is your video with the overlay applied.",
            progress=upload_progress
        )
        logger.info("Upload completed successfully.")
    except Exception as e:
        logger.error(f"Error sending overlaid video for chat {chat_id}: {e}")
        await message.reply_text("Failed to send overlaid video.")
    
    shutil.rmtree(temp_dir)
    del user_state[chat_id]

if __name__ == "__main__":
    app.run()
