import os
import sys
import re
import asyncio
import subprocess
import logging
import tempfile
import shutil

# Common imports for video watermarking (from config) and for PDF watermarking:
from config import BOT_TOKEN, API_ID, API_HASH, FFMPEG_PATH
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

# Imports for video processing:
from moviepy.editor import VideoFileClip  # Importing MoviePy

# Imports for PDF watermarking:
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import red, black, white
import pytesseract
from PIL import Image, ImageDraw, ImageFont
import fitz  # PyMuPDF
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)

# Example usage of logging
logger.info("Logging is configured and ready to use.")
# Set the Tesseract OCR executable path.
pytesseract.pytesseract.tesseract_cmd = r"C:\Users\becom\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"

# ───────────────────────────────────────────────
# PDF Watermarking Functionality
# ───────────────────────────────────────────────

# Conversation states for PDF watermarking.
WAITING_FOR_PDF = "WAITING_FOR_PDF"
WAITING_FOR_LOCATION = "WAITING_FOR_LOCATION"
WAITING_FOR_FIND_TEXT = "WAITING_FOR_FIND_TEXT"  # For OCR Cover-Up (option 9)
WAITING_FOR_SIDE_TOP_LEFT = "WAITING_FOR_SIDE_TOP_LEFT"  # For Sides Cover-Up (option 10)
WAITING_FOR_SIDE_BOTTOM_RIGHT = "WAITING_FOR_SIDE_BOTTOM_RIGHT"
WAITING_FOR_WATERMARK_TEXT = "WAITING_FOR_WATERMARK_TEXT"
WAITING_FOR_TEXT_SIZE = "WAITING_FOR_TEXT_SIZE"
WAITING_FOR_COLOR = "WAITING_FOR_COLOR"

# Global dictionary to store PDF watermarking conversation data.
user_data = {}

def normalized_to_pdf_coords(norm_coord, page_width, page_height):
    """
    Converts a normalized coordinate (v,h) on a 0–10 scale into actual PDF coordinates.
    PDF coordinates have origin at bottom-left.
    """
    v, h = norm_coord
    pdf_x = (h / 10) * page_width
    pdf_y = page_height - ((v / 10) * page_height)
    return (pdf_x, pdf_y)

def annotate_first_page_image(pdf_path, dpi=150):
    """
    Opens the PDF's first page using PyMuPDF, renders it as an image,
    and draws a blue border with tick marks and normalized coordinate labels (0–10).
    Returns the path of the annotated image.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    scale = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 12)
    except Exception:
        font = ImageFont.load_default()
    
    img_width, img_height = image.size

    # Draw blue border.
    draw.rectangle([0, 0, img_width-1, img_height-1], outline="blue", width=2)
    
    # Draw tick marks along the top edge.
    for i in range(11):
        x = (i/10) * img_width
        draw.line([(x, 0), (x, 10)], fill="blue", width=2)
        draw.text((x+2, 12), f"{i}", fill="blue", font=font)
    
    # Draw tick marks along the left edge.
    for i in range(11):
        y = (i/10) * img_height
        draw.line([(0, y), (10, y)], fill="blue", width=2)
        draw.text((12, y-6), f"{i}", fill="blue", font=font)
    
    annotated_path = pdf_path.replace(".pdf", "_annotated.jpg")
    image.save(annotated_path)
    doc.close()
    return annotated_path

async def send_first_page_image(client: Client, chat_id: int):
    """
    Downloads the first PDF from the user's list, creates an annotated image of its first page,
    and sends it to the user.
    """
    try:
        pdf_info = user_data[chat_id]["pdfs"][0]
        temp_pdf_path = os.path.join(tempfile.gettempdir(), pdf_info["file_name"])
        await client.download_media(pdf_info["file_id"], file_name=temp_pdf_path)
        annotated_path = annotate_first_page_image(temp_pdf_path, dpi=150)
        await client.send_photo(
            chat_id,
            photo=annotated_path,
            caption=("This image shows a normalized grid:\n"
                     "• Top edge: horizontal (x) scale: 0 (left) to 10 (right)\n"
                     "• Left edge: vertical (y) scale: 0 (top) to 10 (bottom)\n\n"
                     "Please provide two normalized coordinates in the format 'v,h' (values between 0 and 10):\n"
                     "• LEFT TOP (e.g., 2,3)\n"
                     "• RIGHT BOTTOM (e.g., 8,7)")
        )
        os.remove(temp_pdf_path)
        os.remove(annotated_path)
    except Exception as e:
        await client.send_message(chat_id, f"Error sending annotated image: {e}")

def create_watermarked_pdf(input_pdf_path, watermark_text, text_size, color, location, find_text=None, cover_coords=None):
    """
    Creates a watermarked PDF.
      - For locations 1-8: standard watermark.
      - For location 9 (OCR Cover-Up): covers found text using OCR.
      - For location 10 (Sides Cover-Up): uses two normalized coordinates.
    """
    if location == 9 and find_text:
        doc = fitz.open(input_pdf_path)
        dpi = 150
        scale = dpi / 72
        for page in doc:
            page_width = page.rect.width
            page_height = page.rect.height
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            ocr_data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            n_boxes = len(ocr_data["text"])
            for i in range(n_boxes):
                word = ocr_data["text"][i].strip()
                if word.lower() == find_text.lower():
                    left = ocr_data["left"][i]
                    top = ocr_data["top"][i]
                    width = ocr_data["width"][i]
                    height = ocr_data["height"][i]
                    pdf_x = left / scale
                    pdf_width = width / scale
                    pdf_height = height / scale
                    pdf_y = page_height - ((top + height) / scale)
                    rect = fitz.Rect(pdf_x, pdf_y, pdf_x + pdf_width, pdf_y + pdf_height)
                    page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))
                    text_x = pdf_x
                    text_y = pdf_y + pdf_height + 2
                    wm_color = (color.red, color.green, color.blue)
                    page.insert_text((text_x, text_y), watermark_text, fontsize=text_size, color=wm_color)
        output_pdf_path = input_pdf_path.replace(".pdf", "_watermarked.pdf")
        doc.save(output_pdf_path)
        return output_pdf_path

    elif location == 10 and cover_coords and len(cover_coords) == 2:
        doc = fitz.open(input_pdf_path)
        for page in doc:
            page_width = page.rect.width
            page_height = page.rect.height
            left_top_pdf = normalized_to_pdf_coords(cover_coords[0], page_width, page_height)
            right_bottom_pdf = normalized_to_pdf_coords(cover_coords[1], page_width, page_height)
            x1, y1 = left_top_pdf
            x2, y2 = right_bottom_pdf
            rect = fitz.Rect(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))
            center_x = (rect.x0 + rect.x1) / 2
            center_y = (rect.y0 + rect.y1) / 2
            wm_color = (color.red, color.green, color.blue)
            text_box = fitz.Rect(center_x - 100, center_y - text_size, center_x + 100, center_y + text_size)
            page.insert_textbox(text_box, watermark_text, fontsize=text_size, color=wm_color, align=1)
        output_pdf_path = input_pdf_path.replace(".pdf", "_watermarked.pdf")
        doc.save(output_pdf_path)
        return output_pdf_path

    else:
        # Standard watermarking using ReportLab and PyPDF2.
        reader = PdfReader(input_pdf_path)
        first_page = reader.pages[0]
        page_width = float(first_page.mediabox.width)
        page_height = float(first_page.mediabox.height)
        watermark_stream = BytesIO()
        c = canvas.Canvas(watermark_stream, pagesize=(page_width, page_height))
        c.setFont("Helvetica", text_size)
        c.setFillColor(color)
        margin = 10

        x, y = 0, 0
        rotation = 0
        if location == 1:
            x = page_width - margin - 100
            y = page_height - margin - text_size
        elif location == 2:
            x = (page_width / 2) - 50
            y = page_height - margin - text_size
        elif location == 3:
            x = margin
            y = page_height - margin - text_size
        elif location == 4:
            x = (page_width / 2) - 50
            y = (page_height / 2) - (text_size / 2)
        elif location == 5:
            x = (page_width / 2) - 50
            y = (page_height / 2) - (text_size / 2)
            rotation = 45
        elif location == 6:
            x = page_width - margin - 100
            y = margin
        elif location == 7:
            x = (page_width / 2) - 50
            y = margin
        elif location == 8:
            x = margin
            y = margin

        if rotation:
            c.saveState()
            c.translate(x, y)
            c.rotate(rotation)
            c.drawString(0, 0, watermark_text)
            c.restoreState()
        else:
            c.drawString(x, y, watermark_text)
        c.save()
        watermark_stream.seek(0)
        watermark_reader = PdfReader(watermark_stream)
        watermark_page = watermark_reader.pages[0]
        writer = PdfWriter()
        for page in reader.pages:
            page.merge_page(watermark_page)
            writer.add_page(page)
        output_pdf_path = input_pdf_path.replace(".pdf", "_watermarked.pdf")
        with open(output_pdf_path, "wb") as out_file:
            writer.write(out_file)
        return output_pdf_path

async def process_pdfs_handler(client: Client, chat_id: int):
    data = user_data.get(chat_id)
    if not data:
        return
    pdfs = data.get("pdfs", [])
    location = data.get("location")
    watermark_text = data.get("watermark_text")
    text_size = data.get("text_size")
    color_name = data.get("color")
    color_mapping = {"red": red, "black": black, "white": white}
    watermark_color = color_mapping.get(color_name, black)
    
    find_text = data.get("find_text") if location == 9 else None
    cover_coords = data.get("side_coords") if location == 10 else None

    for pdf_info in pdfs:
        file_id = pdf_info["file_id"]
        file_name = pdf_info["file_name"]
        try:
            temp_pdf_path = os.path.join(tempfile.gettempdir(), file_name)
            await client.download_media(file_id, file_name=temp_pdf_path)
        except Exception as e:
            await client.send_message(chat_id, f"Error downloading {file_name}: {e}")
            continue

        watermarked_pdf_path = create_watermarked_pdf(
            temp_pdf_path, watermark_text, text_size, watermark_color,
            location, find_text=find_text, cover_coords=cover_coords
        )
        try:
            await client.send_document(chat_id, watermarked_pdf_path)
        except Exception as e:
            await client.send_message(chat_id, f"Error sending watermarked file {file_name}: {e}")
        try:
            os.remove(temp_pdf_path)
            os.remove(watermarked_pdf_path)
        except Exception:
            pass

# ───────────────────────────────────────────────
# Video Watermarking Functionality (Main Code)
# ───────────────────────────────────────────────

# Global flags and state dictionaries for video watermarking.
processing_active = False
user_state = {}
bulk_state = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

app = Client("watermark_robot_2", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def check_authorization(message: Message) -> bool:
    if message.chat.id not in [640815756, 5317760109]:
        await message.reply_text("You are not authorized.")
        return False
    return True

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
    parts = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir)
                    if f.startswith("part_") and f.endswith(".mp4")])
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

# ─── Video Watermarking Commands ───
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

# ─── Video Watermarking Text Handler ───
# (This handler will only run if the chat is not in PDF watermarking mode.)
@app.on_message(filters.text & filters.private)
async def text_handler(client, message: Message):
    if not await check_authorization(message):
        return
    global processing_active  # Declare the global variable once at the top.
    
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

async def get_video_duration(file_path):
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
        font_path = "cour.ttf"
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

    metadata = get_video_details(output_file)
    width = metadata.get("width", 0)
    height = metadata.get("height", 0)
    duration_value = int(metadata.get("duration", 0))
    thumb_path = os.path.join(temp_dir, f"{base_name}_thumbnail.jpg")
    if 'custom_thumbnail' in state:
        custom_thumb_path = os.path.join(temp_dir, f"{base_name}_custom_thumbnail.jpg")
        await state['custom_thumbnail'].download(file_name=custom_thumb_path)
        thumb = custom_thumb_path
    else:
        thumb = generate_thumbnail(output_file, thumb_path)

    try:
        upload_msg = await client.send_message(chat_id, "Watermarking complete. Uploading: 0%")
    except FloodWait:
        upload_msg = None
    upload_cb = create_upload_progress(client, chat_id, upload_msg) if upload_msg else None
    original_caption = video_msg.caption if video_msg.caption else "Here is your watermarked video."
    if 'custom_caption' in state:
        original_caption += "\n\n" + state['custom_caption']
    logger.info("Uploading watermarked video...")
    try:
        await client.send_video(
            chat_id,
            video=output_file,
            thumb=thumb,
            caption=original_caption,
            progress=upload_cb,
            width=width,
            height=height,
            duration=duration_value,
            supports_streaming=True
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

        metadata = get_video_details(output_file)
        width = metadata.get("width", 0)
        height = metadata.get("height", 0)
        duration_value = int(metadata.get("duration", 0))
        thumb_path = os.path.join(temp_dir, f"{base_name}_thumbnail.jpg")
        if 'custom_thumbnail' in state:
            custom_thumb_path = os.path.join(temp_dir, f"{base_name}_custom_thumbnail.jpg")
            await state['custom_thumbnail'].download(file_name=custom_thumb_path)
            thumb = custom_thumb_path
        else:
            thumb = generate_thumbnail(output_file, thumb_path)

        try:
            upload_msg = await client.send_message(chat_id, "Watermarking complete. Uploading: 0%")
        except FloodWait:
            upload_msg = None
        upload_cb = create_upload_progress(client, chat_id, upload_msg) if upload_msg else None
        original_caption = video_msg.caption if video_msg.caption else "Here is your bulk watermarked video."
        if 'custom_caption' in state:
            original_caption += "\n\n" + state['custom_caption']
        try:
            logger.info("Uploading watermarked video for bulk video...")
            await client.send_video(
                chat_id,
                video=output_file,
                thumb=thumb,
                caption=original_caption,
                progress=upload_cb,
                width=width,
                height=height,
                duration=duration_value,
                supports_streaming=True
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

# ───────────────────────────────────────────────
# PDF Watermarking Handlers (Integrated)
# ───────────────────────────────────────────────

@app.on_message(filters.command("pdfwatermark"))
async def start_pdfwatermark_handler(client: Client, message: Message):
    chat_id = message.chat.id
    user_data[chat_id] = {"state": WAITING_FOR_PDF, "pdfs": []}
    await message.reply_text("Please send all PDF files now.")

@app.on_message(filters.document)
async def receive_pdf_handler(client: Client, message: Message):
    chat_id = message.chat.id
    if chat_id not in user_data or user_data[chat_id].get("state") != WAITING_FOR_PDF:
        return
    document = message.document
    if document.mime_type != "application/pdf":
        await message.reply_text("This is not a PDF file. Please send a PDF.")
        return
    user_data[chat_id]["pdfs"].append({
        "file_id": document.file_id,
        "file_name": document.file_name
    })
    await message.reply_text(f"Received {document.file_name}. You can send more PDFs or type /pdfask when done.")

@app.on_message(filters.command("pdfask"))
async def start_pdfask_handler(client: Client, message: Message):
    chat_id = message.chat.id
    if chat_id not in user_data or not user_data[chat_id].get("pdfs"):
        await message.reply_text("No PDFs received. Please start with /pdfwatermark and then send PDF files.")
        return
    user_data[chat_id]["state"] = WAITING_FOR_LOCATION
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
async def pdf_text_handler(client: Client, message: Message):
    chat_id = message.chat.id
    if chat_id not in user_data:
        return
    state = user_data[chat_id].get("state")
    text = message.text.strip()
    
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
            await message.reply_text("Enter the text to find (the text you want to cover up):")
        elif loc == 10:
            await send_first_page_image(client, chat_id)
            user_data[chat_id]["state"] = WAITING_FOR_SIDE_TOP_LEFT
            await message.reply_text("Enter the LEFT TOP normalized coordinate (format: x,y in 0-10, e.g., 2,3):")
        else:
            user_data[chat_id]["state"] = WAITING_FOR_WATERMARK_TEXT
            await message.reply_text("Enter watermark text:")
    elif state == WAITING_FOR_FIND_TEXT:
        if not text:
            await message.reply_text("Text to find cannot be empty. Please enter the text to cover up:")
            return
        user_data[chat_id]["find_text"] = text
        user_data[chat_id]["state"] = WAITING_FOR_WATERMARK_TEXT
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
        await message.reply_text("Enter watermark text:")
    elif state == WAITING_FOR_WATERMARK_TEXT:
        if not text:
            await message.reply_text("Watermark text cannot be empty. Please enter the watermark text.")
            return
        user_data[chat_id]["watermark_text"] = text
        user_data[chat_id]["state"] = WAITING_FOR_TEXT_SIZE
        await message.reply_text("Enter watermark text size (e.g., 24):")
    elif state == WAITING_FOR_TEXT_SIZE:
        try:
            size = int(text)
        except ValueError:
            await message.reply_text("Please send a valid number for text size.")
            return
        user_data[chat_id]["text_size"] = size
        user_data[chat_id]["state"] = WAITING_FOR_COLOR
        await message.reply_text("Choose watermark text colour by sending a number:\n1. Red\n2. Black\n3. White")
    elif state == WAITING_FOR_COLOR:
        mapping = {"1": "red", "2": "black", "3": "white"}
        if text not in mapping:
            await message.reply_text("Invalid choice. Please choose 1, 2, or 3 for colour.")
            return
        user_data[chat_id]["color"] = mapping[text]
        await message.reply_text("PDF watermarking started.")
        await process_pdfs_handler(client, chat_id)
        user_data.pop(chat_id, None)
    # Optional: Test thumbnail and metadata functions before starting the bot.
    test_video = "path/to/your/test_video.mp4"
    thumbnail = "path/to/output_thumbnail.jpg"
    thumb = generate_thumbnail(test_video, thumbnail)
    if thumb:
        logging.info(f"Thumbnail generated at: {thumb}")
    metadata = get_video_details(test_video)
    if metadata:
        logging.info(f"Video metadata: {metadata}")
# ───────────────────────────────────────────────
# Main Execution
# ───────────────────────────────────────────────
if __name__ == '__main__':

    app.run()
