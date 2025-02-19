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

# Copy the rest of the application code.
COPY config.py .
COPY main.py .

# Set environment variables here if you wish to provide defaults (optional).
# ENV FFMPEG_PATH=ffmpeg

# Run the bot.
CMD ["python", "main.py"]
