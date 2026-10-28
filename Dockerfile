FROM python:3.12-slim

# ffmpeg for audio conversion, nodejs as yt-dlp JS runtime (YouTube workaround)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY modal_app.py .
COPY backend/ ./backend/
COPY frontend/ ./frontend/

ENV PYTHONUNBUFFERED=1 \
    AUTOTABBER_DATA_DIR=/data

EXPOSE 8000

# Modal credentials are provided at runtime via environment:
#   MODAL_TOKEN_ID / MODAL_TOKEN_SECRET (see .env.example)
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
