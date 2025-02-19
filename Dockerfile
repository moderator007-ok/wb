# Use an official Python runtime as a parent image.
FROM python:3.9-slim

# Install FFmpeg and clean up apt lists.
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory.
WORKDIR /app

# Copy dependency list and install them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code.
COPY config.py .
COPY main.py .
COPY app.py .

# Run Gunicorn in the background and then start the bot.
CMD gunicorn app:app & python3 main.py
