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
from moviepy.editor import VideoFileClip  # Importing MoviePy

from utils import (
    BOT_TOKEN, API_ID, API_HASH, FFMPEG_PATH, app, logger, check_authorization,
    user_state, bulk_state, processing_active, user_data
)

# (Note: pdf.py functions have been merged here so no separate import of pdf.py)

# ─── Video Watermarking Functions ───

def generate_thumbnail(video_file, thumbnail_path, time_offset="00:00:01.000"):
    """
    Generate a thumbnail image from a video file using FFmpeg.
    """
    ffmpeg_executable = FFMPEG_PATH if FFMPEG_PATH else "ffmpeg"
    # Move -ss before -i for faster seeking
    command = [
        ffmpeg_executable,
        "-ss", time_offset,
        "-i", video_file,
        "-frames:v", "1",
        "-y",  # Overwrite if exists
        thumbnail_path
    ]
    try:
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logging.info("Thumbnail generated successfully.")
        return thumbnail_path
    except subprocess.CalledProcessError as e:
        logging.error(f"Thumbnail generation failed: {e.stderr.decode('utf-8')}")
        return None

def get_video_details(video_file):
    """
    Retrieve video details (width, height, duration).
    Attempts MoviePy first and falls back to ffprobe if needed.
    """
    try:
        clip = VideoFileClip(video_file)
        details = {
            "width": clip.w,
            "height": clip.h,
            "duration": clip.duration
        }
        clip.reader.close()
        if clip.audio:
            clip.audio.reader.close_proc()
        return details
    except Exception as e:
        logging.error(f"MoviePy failed to retrieve details: {e}. Falling back to ffprobe.")
        try:
            ffprobe_executable = FFMPEG_PATH.replace("ffmpeg", "ffprobe") if FFMPEG_PATH else "ffprobe"
            ffprobe_cmd = [
                ffprobe_executable,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_file
            ]
            result = subprocess.run(ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            output = result.stdout.decode('utf-8').strip().splitlines()
            if len(output) >= 3:
                details = {
                    "width": int(output[0]),
                    "height": int(output[1]),
                    "duration": float(output[2])
                }
                return details
            else:
                logging.error("ffprobe did not return enough data.")
        except Exception as ex:
            logging.error(f"ffprobe failed to retrieve details: {ex}")
        return {}

async def split_video_file(input_file: str, output_dir: str, segment_time: int) -> list:
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

# ─── Admin Commands ───
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

# ─── Command Handlers for Video Watermarking ───
# (Video watermarking uses user_state)
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

# ─── Video Watermarking Text Handler ───
# This handler is for video watermarking only. To ensure PDF watermarking texts are not intercepted,
# we check if the chat ID is in user_data (PDF watermarking state) and return if so.
@app.on_message(filters.text & filters.private)
async def video_text_handler(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    # If PDF watermarking state exists, skip this handler.
    if chat_id in user_data:
        return
    if chat_id not in user_state:
        return
    global processing_active
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
            state['step'] = 'ask_thumbnail'
            await message.reply_text("Do you want to use a custom thumbnail? (yes/no)")
        elif current_step == 'ask_thumbnail':
            answer = message.text.strip().lower()
            if answer in ['yes', 'y']:
                state['step'] = 'await_thumbnail'
                await message.reply_text("Please send your custom thumbnail image.")
            else:
                state['step'] = 'ask_caption'
                await message.reply_text("Do you want to add a custom extra caption? (yes/no)")
        elif current_step == 'ask_caption':
            answer = message.text.strip().lower()
            if answer in ['yes', 'y']:
                state['step'] = 'await_caption'
                await message.reply_text("Please send your custom extra caption text.")
            else:
                state['step'] = 'processing'
                await message.reply_text("All inputs collected. Watermarking started.")
                processing_active = True
                try:
                    await process_watermark(client, message, state, chat_id)
                finally:
                    processing_active = False
        elif current_step == 'await_caption':
            state['custom_caption'] = message.text.strip()
            state['step'] = 'processing'
            await message.reply_text("Custom caption received. Watermarking started.")
            processing_active = True
            try:
                await process_watermark(client, message, state, chat_id)
            finally:
                processing_active = False
    elif mode == 'harrypotter':
        pass
    elif mode == 'overlay':
        pass

# ─── Bulk Watermarking Commands and Handlers ───
@app.on_message(filters.command("inputwatermark") & filters.private)
async def inputwatermark_bulk(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    bulk_state[chat_id] = {'videos': []}
    await message.reply_text("Bulk watermark mode activated.\nNow, send all the videos you want to watermark.")

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

@app.on_message(filters.private & (filters.video | filters.document))
async def bulk_video_handler(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    if chat_id in bulk_state:
        bulk_state[chat_id].setdefault('videos', []).append(message)
        await message.reply_text("Video added for bulk watermarking.")

@app.on_message(filters.text & filters.private)
async def bulk_text_handler(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    if chat_id not in bulk_state:
        return  # Not in bulk mode
    state = bulk_state[chat_id]
    if state.get('step') == 'await_text':
        state['watermark_text'] = message.text.strip()
        state['step'] = 'await_size'
        await message.reply_text("Watermark text received. Please send font size (as a number).")
    elif state.get('step') == 'await_size':
        try:
            size = int(message.text.strip())
            state['font_size'] = size
            state['step'] = 'await_color'
            await message.reply_text("Font size received. Now send color choice: 1 for black, 2 for white, 3 for red.")
        except ValueError:
            await message.reply_text("Invalid font size. Please send a number.")
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
    elif state.get('step') == 'await_preset':
        preset = message.text.strip().lower()
        if preset not in {"medium", "fast", "superfast", "ultrafast"}:
            await message.reply_text("Invalid preset. Please send one of: medium, fast, superfast, ultrafast.")
            return
        state['preset'] = preset
        state['step'] = 'ask_thumbnail'
        await message.reply_text("Do you want to use a custom thumbnail? (yes/no)")
    elif state.get('step') == 'ask_thumbnail':
        answer = message.text.strip().lower()
        if answer in ['yes', 'y']:
            state['step'] = 'await_thumbnail'
            await message.reply_text("Please send your custom thumbnail image.")
        else:
            state['step'] = 'ask_caption'
            await message.reply_text("Do you want to add a custom extra caption? (yes/no)")
    elif state.get('step') == 'ask_caption':
        answer = message.text.strip().lower()
        if answer in ['yes', 'y']:
            state['step'] = 'await_caption'
            await message.reply_text("Please send your custom extra caption text.")
        else:
            state['step'] = 'processing'
            await message.reply_text("All inputs collected. Bulk watermarking started.")
            await process_bulk_watermark(client, message, state, chat_id)
    elif state.get('step') == 'await_caption':
        state['custom_caption'] = message.text.strip()
        state['step'] = 'processing'
        await message.reply_text("Custom caption received. Bulk watermarking started.")
        await process_bulk_watermark(client, message, state, chat_id)

# ─── Video Watermarking Handler (for non-PDF texts) ───
# (This handler now does not process texts if PDF watermarking state exists.)
@app.on_message(filters.text & filters.private)
async def video_text_handler(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    # If PDF watermarking state exists, skip this handler.
    if chat_id in user_data:
        return
    if chat_id not in user_state:
        return
    global processing_active
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
            state['step'] = 'ask_thumbnail'
            await message.reply_text("Do you want to use a custom thumbnail? (yes/no)")
        elif current_step == 'ask_thumbnail':
            answer = message.text.strip().lower()
            if answer in ['yes', 'y']:
                state['step'] = 'await_thumbnail'
                await message.reply_text("Please send your custom thumbnail image.")
            else:
                state['step'] = 'ask_caption'
                await message.reply_text("Do you want to add a custom extra caption? (yes/no)")
        elif current_step == 'ask_caption':
            answer = message.text.strip().lower()
            if answer in ['yes', 'y']:
                state['step'] = 'await_caption'
                await message.reply_text("Please send your custom extra caption text.")
            else:
                state['step'] = 'processing'
                await message.reply_text("All inputs collected. Watermarking started.")
                processing_active = True
                try:
                    await process_watermark(client, message, state, chat_id)
                finally:
                    processing_active = False
        elif current_step == 'await_caption':
            state['custom_caption'] = message.text.strip()
            state['step'] = 'processing'
            await message.reply_text("Custom caption received. Watermarking started.")
            processing_active = True
            try:
                await process_watermark(client, message, state, chat_id)
            finally:
                processing_active = False
    elif mode == 'harrypotter':
        pass
    elif mode == 'overlay':
        pass

# ─── Image Handler ───
@app.on_message(filters.private & (filters.photo | filters.document))
async def image_handler(client, message: Message):
    if not await check_authorization(message):
        return
    global processing_active
    chat_id = message.chat.id
    # Handle bulk mode custom thumbnail first
    if chat_id in bulk_state:
        bulk_state_obj = bulk_state[chat_id]
        if bulk_state_obj.get('step') == 'await_thumbnail':
            bulk_state_obj['custom_thumbnail'] = message
            bulk_state_obj['step'] = 'ask_caption'
            await message.reply_text("Custom thumbnail received. Do you want to add a custom extra caption? (yes/no)")
            return
    # Then handle single mode custom thumbnail
    if chat_id not in user_state:
        return
    state = user_state[chat_id]
    if state.get('step') == 'await_thumbnail':
        state['custom_thumbnail'] = message
        state['step'] = 'ask_caption'
        await message.reply_text("Custom thumbnail received. Do you want to add a custom extra caption? (yes/no)")
        return
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

# ─── PDF Watermarking Commands and Handlers ───
@app.on_message(filters.command("pdfwatermark"))
async def start_pdfwatermark_handler(client, message: Message):
    chat_id = message.chat.id
    logger.info(f"Starting PDF watermark process for chat_id: {chat_id}")
    user_data[chat_id] = {"state": WAITING_FOR_PDF, "pdfs": []}
    await message.reply_text("Please send all PDF files now.")

@app.on_message(filters.document)
async def receive_pdf_handler(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in user_data or user_data[chat_id].get("state") != WAITING_FOR_PDF:
        return
    document = message.document
    if document.mime_type != "application/pdf":
        await message.reply_text("This is not a PDF file. Please send a PDF.")
        return
    logger.info(f"Received PDF: {document.file_name} from chat_id: {chat_id}")
    user_data[chat_id]["pdfs"].append({
        "file_id": document.file_id,
        "file_name": document.file_name
    })
    await message.reply_text(f"Received {document.file_name}. You can send more PDFs or type /pdfask when done.")

@app.on_message(filters.command("pdfask"))
async def start_pdfask_handler(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in user_data or not user_data[chat_id].get("pdfs"):
        await message.reply_text("No PDFs received. Please start with /pdfwatermark and then send PDF files.")
        return
    user_data[chat_id]["state"] = WAITING_FOR_LOCATION
    logger.info(f"User {chat_id} set state to WAITING_FOR_LOCATION")
    await message.reply_text(
        "Choose watermark location by sending a number:\n"
        "1. Top right\n"
        "2. Top middle\n"
        "3. Top left\n"
        "4. Middle straight\n"
        "5. Middle 45 degree\n"
        "6. Bottom right\n"
        "7. Bottom centre\n"
        "8. Bottom left\n"
        "9. Cover-Up (using OCR)\n"
        "10. Sides Cover-Up (rectangle with normalized 0-10 coordinates)"
    )

@app.on_message(filters.text & ~filters.command(["pdfwatermark", "pdfask"]))
async def pdf_text_handler(client, message: Message):
    if not await check_authorization(message):
        return
    chat_id = message.chat.id
    if chat_id not in user_data:
        return
    state = user_data[chat_id].get("state")
    text = message.text.strip()
    logger.info(f"Handling PDF text for chat_id: {chat_id} in state: {state} with text: {text}")
    
    if state == WAITING_FOR_LOCATION:
        try:
            loc = int(text)
            if loc < 1 or loc > 10:
                await message.reply_text("Invalid choice. Please send a number between 1 and 10 for location.")
                return
        except ValueError:
            await message.reply_text("Please send a valid number for location.")
            return
        user_data[chat_id]["location"] = loc
        if loc == 9:
            user_data[chat_id]["state"] = WAITING_FOR_FIND_TEXT
            logger.info("PDF state changed to WAITING_FOR_FIND_TEXT")
            await message.reply_text("Enter the text to find (the text you want to cover up):")
        elif loc == 10:
            await send_first_page_image(client, chat_id)
            user_data[chat_id]["state"] = WAITING_FOR_SIDE_TOP_LEFT
            logger.info("PDF state changed to WAITING_FOR_SIDE_TOP_LEFT")
            await message.reply_text("Enter the LEFT TOP normalized coordinate (format: x,y in 0-10, e.g., 2,3):")
        else:
            user_data[chat_id]["state"] = WAITING_FOR_WATERMARK_TEXT
            logger.info("PDF state changed to WAITING_FOR_WATERMARK_TEXT")
            await message.reply_text("Enter watermark text:")
    elif state == WAITING_FOR_FIND_TEXT:
        if not text:
            await message.reply_text("Text to find cannot be empty. Please enter the text to cover up:")
            return
        user_data[chat_id]["find_text"] = text
        user_data[chat_id]["state"] = WAITING_FOR_WATERMARK_TEXT
        logger.info("PDF state changed to WAITING_FOR_WATERMARK_TEXT")
        await message.reply_text("Enter watermark text:")
    elif state == WAITING_FOR_SIDE_TOP_LEFT:
        try:
            x_str, y_str = text.split(",")
            coord = (float(x_str.strip()), float(y_str.strip()))
        except Exception:
            await message.reply_text("Invalid format. Please enter coordinate as x,y (e.g., 2,3).")
            return
        user_data[chat_id]["side_coords"] = [coord]
        user_data[chat_id]["state"] = WAITING_FOR_SIDE_BOTTOM_RIGHT
        logger.info("PDF state changed to WAITING_FOR_SIDE_BOTTOM_RIGHT")
        await message.reply_text("Enter the RIGHT BOTTOM normalized coordinate (format: x,y in 0-10, e.g., 8,7):")
    elif state == WAITING_FOR_SIDE_BOTTOM_RIGHT:
        try:
            x_str, y_str = text.split(",")
            coord = (float(x_str.strip()), float(y_str.strip()))
        except Exception:
            await message.reply_text("Invalid format. Please enter coordinate as x,y (e.g., 8,7).")
            return
        user_data[chat_id]["side_coords"].append(coord)
        user_data[chat_id]["state"] = WAITING_FOR_WATERMARK_TEXT
        logger.info("PDF state changed to WAITING_FOR_WATERMARK_TEXT")
        await message.reply_text("Enter watermark text:")
    elif state == WAITING_FOR_WATERMARK_TEXT:
        if not text:
            await message.reply_text("Watermark text cannot be empty. Please enter the watermark text.")
            return
        user_data[chat_id]["watermark_text"] = text
        user_data[chat_id]["state"] = WAITING_FOR_TEXT_SIZE
        logger.info("PDF state changed to WAITING_FOR_TEXT_SIZE")
        await message.reply_text("Enter watermark text size (e.g., 24):")
    elif state == WAITING_FOR_TEXT_SIZE:
        try:
            size = int(text)
        except ValueError:
            await message.reply_text("Please send a valid number for text size.")
            return
        user_data[chat_id]["text_size"] = size
        user_data[chat_id]["state"] = WAITING_FOR_COLOR
        logger.info("PDF state changed to WAITING_FOR_COLOR")
        await message.reply_text("Choose watermark text colour by sending a number:\n1. Red\n2. Black\n3. White")
    elif state == WAITING_FOR_COLOR:
        mapping = {"1": "red", "2": "black", "3": "white"}
        if text not in mapping:
            await message.reply_text("Invalid choice. Please choose 1, 2, or 3 for colour.")
            return
        user_data[chat_id]["color"] = mapping[text]
        logger.info("PDF watermarking parameters collected. Starting processing.")
        await message.reply_text("PDF watermarking started.")
        await process_pdfs_handler(client, chat_id)
        user_data.pop(chat_id, None)

# ─── Dummy Test Command for PDF Functions ───
@app.on_message(filters.command("testpdf"))
async def testpdf_cmd(client, message: Message):
    await message.reply_text("Test PDF command received! PDF functions are imported and active.")

# ─── End of Merged Code ───
if __name__ == '__main__':
    logger.info("Starting combined bot for video and PDF watermarking...")
    app.run()
