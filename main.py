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

# /watermark and /watermarktm commands
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
        'duration': None,
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
        'duration': None,
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
        'duration': None,
        'step': 'await_main'
    }
    await message.reply_text("Send the **main video** for overlay.")

# New /imgwatermark command: Overlay an image watermark (scaled to approx 32px high)
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
        await message.reply_text("Video captured. Now send the watermark text.")

    elif mode == 'overlay':
        current_step = state.get('step')
        if current_step == 'await_main':
            state['main_video_message'] = message
            state['step'] = 'await_overlay'
            await message.reply_text("Main video received. Now send the **overlay video** (with green screen background).")
        # We do not handle video here for the duration step.

    elif mode == 'imgwatermark':
        if state.get('step') != 'await_video':
            return
        state['video_message'] = message
        state['step'] = 'await_image'
        await message.reply_text("Video received. Now send the watermark image.")

# ─── Image Handler for /imgwatermark ─────────────────────────────
@app.on_message(filters.private & (filters.photo | filters.document))
async def image_handler(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in user_state:
        return
    state = user_state[chat_id]
    if state.get('mode') == 'imgwatermark' and state.get('step') == 'await_image':
        state['image_message'] = message
        state['step'] = 'processing'
        await message.reply_text("Image received. Processing video with image watermark, please wait...")
        await process_imgwatermark(client, message, state, chat_id)
    # (Ignore images sent in other contexts.)
    
# ─── Text Handler for Watermark/Overlay Duration and other inputs ──────────────
@app.on_message(filters.text & filters.private)
async def text_handler(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in user_state:
        return
    state = user_state[chat_id]
    current_step = state.get('step')
    mode = state.get('mode')

    # For watermark and watermarktm modes:
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
            state['step'] = 'await_duration'
            await message.reply_text("Color received. Now send the duration (in seconds) during which the watermark should appear.")
        elif current_step == 'await_duration':
            try:
                duration = float(message.text.strip())
                state['duration'] = duration
                state['step'] = 'processing'
                await message.reply_text("All inputs collected. Processing video, please wait...")
                await process_watermark(client, message, state, chat_id)
            except ValueError:
                await message.reply_text("Invalid duration. Please send a number in seconds.")

    # For overlay mode:
    elif mode == 'overlay':
        if current_step == 'await_duration':
            try:
                duration = float(message.text.strip())
                state['duration'] = duration
                state['step'] = 'processing'
                await message.reply_text("Duration received. Processing video, please wait...")
                await process_overlay(client, message, state, chat_id)
            except ValueError:
                await message.reply_text("Invalid duration. Please send a number in seconds.")

# ─── Processing Functions ───────────────────────────────────────

async def process_watermark(client, message, state, chat_id):
    temp_dir = tempfile.mkdtemp()
    state['temp_dir'] = temp_dir

    # Download the input video.
    video_msg = state['video_message']
    if video_msg.video:
        file_name = video_msg.video.file_name or f"{video_msg.video.file_id}.mp4"
    elif video_msg.document:
        file_name = video_msg.document.file_name or f"{video_msg.document.file_id}.mp4"
    else:
        file_name = "input_video.mp4"
    input_file_path = os.path.join(temp_dir, file_name)
    logger.info("Starting video download...")
    await video_msg.download(file_name=input_file_path, progress=download_progress)
    logger.info("Video download completed.")

    base_name = os.path.splitext(os.path.basename(input_file_path))[0]
    duration = state['duration']

    # Prepare segment file paths.
    seg1 = os.path.join(temp_dir, f"{base_name}_seg1.mp4")
    seg2 = os.path.join(temp_dir, f"{base_name}_seg2.mp4")
    output_file = os.path.join(temp_dir, f"{base_name}_watermarked.mp4")

    # Build the watermark filter (with enable option).
    if state['mode'] == 'watermark':
        filter_str = (
            f"drawtext=text='{state['watermark_text']}':"
            f"fontcolor={state['font_color']}:fontsize={state['font_size']}:"
            "x=(w-text_w)/2:y=h-text_h-10"
        )
    elif state['mode'] == 'watermarktm':
        filter_str = (
            f"drawtext=text='{state['watermark_text']}':"
            f"fontcolor={state['font_color']}:" 
            f"fontsize={state['font_size']}:borderw=2:bordercolor=black:"
            "x='mod(t\\,30)*30':y='mod(t\\,30)*15'"
        )

    # Process segment 1: re-encode first part (0 to duration) with watermark filter.
    ffmpeg_cmd1 = [
        FFMPEG_PATH,
        "-i", input_file_path,
        "-vf", f"{filter_str}:enable='lt(t,{duration})'",
        "-c:v", "libx264", "-crf", "23", "-preset", "medium",
        "-c:a", "copy",
        "-t", str(duration),
        seg1
    ]
    logger.info("Processing segment 1 with watermark filter...")
    proc1 = subprocess.Popen(ffmpeg_cmd1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    for line in proc1.stdout:
        logger.info(line.strip())
    proc1.wait()
    if proc1.returncode != 0:
        logger.error(f"Error processing segment 1. Return code: {proc1.returncode}")
        await message.reply_text("Error processing video (segment 1).")
        shutil.rmtree(temp_dir)
        del user_state[chat_id]
        return

    # Process segment 2: copy remaining part (from duration to end).
    ffmpeg_cmd2 = [
        FFMPEG_PATH,
        "-ss", str(duration),
        "-i", input_file_path,
        "-c", "copy",
        seg2
    ]
    logger.info("Copying remaining segment (segment 2) without watermark...")
    proc2 = subprocess.Popen(ffmpeg_cmd2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    for line in proc2.stdout:
        logger.info(line.strip())
    proc2.wait()
    if proc2.returncode != 0:
        logger.error(f"Error processing segment 2. Return code: {proc2.returncode}")
        await message.reply_text("Error processing video (segment 2).")
        shutil.rmtree(temp_dir)
        del user_state[chat_id]
        return

    # Create concat file list.
    concat_file = os.path.join(temp_dir, "concat_list.txt")
    with open(concat_file, "w") as f:
        f.write(f"file '{seg1}'\n")
        f.write(f"file '{seg2}'\n")

    # Concatenate segments.
    ffmpeg_concat_cmd = [
        FFMPEG_PATH,
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        output_file
    ]
    logger.info("Concatenating segments...")
    proc3 = subprocess.Popen(ffmpeg_concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    for line in proc3.stdout:
        logger.info(line.strip())
    proc3.wait()
    if proc3.returncode != 0:
        logger.error(f"Error during concatenation. Return code: {proc3.returncode}")
        await message.reply_text("Error concatenating video segments.")
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

async def process_overlay(client, message, state, chat_id):
    temp_dir = tempfile.mkdtemp()
    state['temp_dir'] = temp_dir

    # Download main video.
    main_msg = state['main_video_message']
    if main_msg.video:
        main_file_name = main_msg.video.file_name or f"{main_msg.video.file_id}.mp4"
    elif main_msg.document:
        main_file_name = main_msg.document.file_name or f"{main_msg.document.file_id}.mp4"
    else:
        main_file_name = "main_video.mp4"
    main_file_path = os.path.join(temp_dir, main_file_name)
    logger.info("Downloading main video...")
    await main_msg.download(file_name=main_file_path, progress=download_progress)
    logger.info("Main video downloaded.")

    # Download overlay video.
    overlay_msg = state['overlay_video_message']
    if overlay_msg.video:
        overlay_file_name = overlay_msg.video.file_name or f"{overlay_msg.video.file_id}.mp4"
    elif overlay_msg.document:
        overlay_file_name = overlay_msg.document.file_name or f"{overlay_msg.document.file_id}.mp4"
    else:
        overlay_file_name = "overlay_video.mp4"
    overlay_file_path = os.path.join(temp_dir, overlay_file_name)
    logger.info("Downloading overlay video...")
    await overlay_msg.download(file_name=overlay_file_path, progress=download_progress)
    logger.info("Overlay video downloaded.")

    # Pre-process overlay video to remove green screen.
    processed_overlay_path = os.path.join(temp_dir, "processed_overlay.mov")
    pre_process_cmd = [
        FFMPEG_PATH,
        "-i", overlay_file_path,
        "-vf", "colorkey=0x00FF00:0.3:0.2,format=yuva420p",
        "-c:v", "qtrle",
        processed_overlay_path
    ]
    logger.info("Pre-processing overlay video to remove green screen...")
    proc_pre = subprocess.Popen(pre_process_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    for line in proc_pre.stdout:
        logger.info(line.strip())
    proc_pre.wait()
    if proc_pre.returncode != 0:
        logger.error(f"Error pre-processing overlay video. Return code: {proc_pre.returncode}")
        await message.reply_text("Error pre-processing overlay video.")
        shutil.rmtree(temp_dir)
        del user_state[chat_id]
        return

    duration = state['duration']
    base_name = os.path.splitext(os.path.basename(main_file_path))[0]
    seg1 = os.path.join(temp_dir, f"{base_name}_seg1.mp4")
    seg2 = os.path.join(temp_dir, f"{base_name}_seg2.mp4")
    output_file = os.path.join(temp_dir, f"{base_name}_overlay.mp4")

    # Process segment 1: composite overlay for first 'duration' seconds.
    # Note the overlay filter uses the enable option.
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
    logger.info("Processing segment 1 with overlay filter...")
    proc1 = subprocess.Popen(ffmpeg_cmd1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    for line in proc1.stdout:
        logger.info(line.strip())
    proc1.wait()
    if proc1.returncode != 0:
        logger.error(f"Error processing overlay segment 1. Return code: {proc1.returncode}")
        await message.reply_text("Error processing overlay (segment 1).")
        shutil.rmtree(temp_dir)
        del user_state[chat_id]
        return

    # Process segment 2: copy the remaining part of the main video.
    ffmpeg_cmd2 = [
        FFMPEG_PATH,
        "-ss", str(duration),
        "-i", main_file_path,
        "-c", "copy",
        seg2
    ]
    logger.info("Copying remaining segment (segment 2) without overlay...")
    proc2 = subprocess.Popen(ffmpeg_cmd2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    for line in proc2.stdout:
        logger.info(line.strip())
    proc2.wait()
    if proc2.returncode != 0:
        logger.error(f"Error processing overlay segment 2. Return code: {proc2.returncode}")
        await message.reply_text("Error processing overlay (segment 2).")
        shutil.rmtree(temp_dir)
        del user_state[chat_id]
        return

    # Create concat file list.
    concat_file = os.path.join(temp_dir, "concat_list.txt")
    with open(concat_file, "w") as f:
        f.write(f"file '{seg1}'\n")
        f.write(f"file '{seg2}'\n")

    # Concatenate segments.
    ffmpeg_concat_cmd = [
        FFMPEG_PATH,
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        output_file
    ]
    logger.info("Concatenating overlay segments...")
    proc3 = subprocess.Popen(ffmpeg_concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    for line in proc3.stdout:
        logger.info(line.strip())
    proc3.wait()
    if proc3.returncode != 0:
        logger.error(f"Error during overlay concatenation. Return code: {proc3.returncode}")
        await message.reply_text("Error concatenating overlay segments.")
        shutil.rmtree(temp_dir)
        del user_state[chat_id]
        return

    try:
        logger.info("Uploading overlaid video...")
        await client.send_video(
            chat_id,
            video=output_file,
            caption="Here is your video with the overlay applied.",
            progress=upload_progress
        )
        logger.info("Upload completed successfully.")
    except Exception as e:
        logger.error(f"Error sending overlaid video for chat {chat_id}: {e}")
        await message.reply_text("Failed to send overlaid video.")
    
    shutil.rmtree(temp_dir)
    del user_state[chat_id]

async def process_imgwatermark(client, message, state, chat_id):
    temp_dir = tempfile.mkdtemp()
    state['temp_dir'] = temp_dir

    # Download the input video.
    video_msg = state['video_message']
    if video_msg.video:
        file_name = video_msg.video.file_name or f"{video_msg.video.file_id}.mp4"
    elif video_msg.document:
        file_name = video_msg.document.file_name or f"{video_msg.document.file_id}.mp4"
    else:
        file_name = "input_video.mp4"
    input_video_path = os.path.join(temp_dir, file_name)
    logger.info("Downloading video for image watermark...")
    await video_msg.download(file_name=input_video_path, progress=download_progress)
    logger.info("Video downloaded.")

    # Download the watermark image.
    image_msg = state['image_message']
    if image_msg.photo:
        # Get highest resolution photo.
        file_name_img = "watermark.png"
    elif image_msg.document:
        file_name_img = image_msg.document.file_name or f"{image_msg.document.file_id}.png"
    else:
        file_name_img = "watermark.png"
    input_image_path = os.path.join(temp_dir, file_name_img)
    logger.info("Downloading watermark image...")
    await image_msg.download(file_name=input_image_path, progress=download_progress)
    logger.info("Watermark image downloaded.")

    output_file = os.path.join(temp_dir, "output_imgwatermark.mp4")
    # The filter_complex scales the watermark image to height=32 (keeping aspect ratio)
    # and overlays it at position (10,10). (Overlaying an image requires re-encoding.)
    ffmpeg_cmd = [
        FFMPEG_PATH,
        "-i", input_video_path,
        "-i", input_image_path,
        "-filter_complex", "[1:v]scale=-1:32[wm];[0:v][wm]overlay=10:10",
        "-c:a", "copy",
        output_file
    ]
    logger.info("Processing video with image watermark...")
    proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    for line in proc.stdout:
        logger.info(line.strip())
    proc.wait()
    if proc.returncode != 0:
        logger.error(f"Error processing image watermark. Return code: {proc.returncode}")
        await message.reply_text("Error processing image watermark.")
        shutil.rmtree(temp_dir)
        del user_state[chat_id]
        return

    try:
        logger.info("Uploading image-watermarked video...")
        await client.send_video(
            chat_id,
            video=output_file,
            caption="Here is your video with the image watermark applied.",
            progress=upload_progress
        )
        logger.info("Upload completed successfully.")
    except Exception as e:
        logger.error(f"Error sending image-watermarked video for chat {chat_id}: {e}")
        await message.reply_text("Failed to send image-watermarked video.")
    
    shutil.rmtree(temp_dir)
    del user_state[chat_id]

if __name__ == "__main__":
    app.run()
