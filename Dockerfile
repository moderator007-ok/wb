FROM python:3.9-slim

# Install FFmpeg and build dependencies.
RUN apt-get update && \
    apt-get install -y ffmpeg gcc build-essential libssl-dev libffi-dev python3-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code.
COPY config.py .
COPY main.py .
COPY app.py .

CMD ["sh", "-c", "gunicorn app:app & python3 main.py"]
