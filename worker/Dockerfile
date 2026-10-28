FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch CPU prima: demucs/basic-pitch trovano torch già presente
# e evitano i wheel CUDA (~2.5 GB inutili)
RUN pip install --no-cache-dir torch==2.5.1 torchaudio==2.5.1 \
    --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker.py pipeline.py transcribe.py tex.py gp5.py ./

CMD ["python", "worker.py"]
