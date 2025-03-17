import os
import tempfile
from io import BytesIO

from pyrogram import Client, filters
from pyrogram.types import Message
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import red, black, white

import pytesseract
from PIL import Image, ImageDraw, ImageFont
import fitz  # PyMuPDF

# Import configurations and functions from main.py
from main import (
    BOT_TOKEN, API_ID, API_HASH, app, user_data, logger, check_authorization
)

# Set the Tesseract OCR executable path for Unix-based systems
pytesseract.pytesseract.tesseract_cmd = r"/usr/bin/tesseract"

# Conversation states
WAITING_FOR_PDF = "WAITING_FOR_PDF"
WAITING_FOR_LOCATION = "WAITING_FOR_LOCATION"
WAITING_FOR_FIND_TEXT = "WAITING_FOR_FIND_TEXT"
WAITING_FOR_SIDE_TOP_LEFT = "WAITING_FOR_SIDE_TOP_LEFT"
WAITING_FOR_SIDE_BOTTOM_RIGHT = "WAITING_FOR_SIDE_BOTTOM_RIGHT"
WAITING_FOR_WATERMARK_TEXT = "WAITING_FOR_WATERMARK_TEXT"
WAITING_FOR_TEXT_SIZE = "WAITING_FOR_TEXT_SIZE"
WAITING_FOR_COLOR = "WAITING_FOR_COLOR"

def normalized_to_pdf_coords(norm_coord, page_width, page_height):
    v, h = norm_coord
    pdf_x = (h / 10) * page_width
    pdf_y = page_height - ((v / 10) * page_height)
    return (pdf_x, pdf_y)

def annotate_first_page_image(pdf_path, dpi=150):
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

    draw.rectangle([0, 0, img_width-1, img_height-1], outline="blue", width=2)
    
    for i in range(11):
        x = (i/10) * img_width
        draw.line([(x, 0), (x, 10)], fill="blue", width=2)
        draw.text((x+2, 12), f"{i}", fill="blue", font=font)
    
    for i in range(11):
        y = (i/10) * img_height
        draw.line([(0, y), (10, y)], fill="blue", width=2)
        draw.text((12, y-6), f"{i}", fill="blue", font=font)
    
    annotated_path = pdf_path.replace(".pdf", "_annotated.jpg")
    image.save(annotated_path)
    doc.close()
    return annotated_path

async def send_first_page_image(client: Client, chat_id: int):
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
    color_mapping = {"red": red, "black", "white"}
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
async def handle_text_handler(client: Client, message: Message):
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

if __name__ == '__main__':
    app.run()
