FROM python:3.9-slim

# Install FFmpeg and build dependencies.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        gcc \
        build-essential \
        libssl-dev \
        libffi-dev \
        python3-dev && \
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean

WORKDIR /app

# Copy requirements and install dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code.
COPY . .

# Run gunicorn binding to Heroku's dynamic $PORT.
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:$PORT"]
