# Multi-process container: Telegram Bot API server + Python bot
# Base on image that already contains telegram-bot-api binary
FROM aiogram/telegram-bot-api:latest

# Install Python, build tools, and ffmpeg for video processing
RUN apk add --no-cache python3 py3-pip bash curl build-base openssl-dev libffi-dev python3-dev \
    ffmpeg ffmpeg-dev \
    && python3 -m venv /opt/venv

# Ensure venv Python & pip are used
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# Copy requirements and install
COPY requirements.txt /app/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

# Copy app code
COPY . /app

# Default environment
ENV PYTHONUNBUFFERED=1 \
    HEALTH_PORT=10000

# Start both the Bot API server and the Python bot
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

ENTRYPOINT ["/app/start.sh"]
