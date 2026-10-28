"""Entrypoint worker RQ — esegue la pipeline sui job in coda."""

import os
import subprocess
import sys

# YouTube cambia spesso: opzione per aggiornare yt-dlp all'avvio
if os.getenv("YT_DLP_UPDATE", "0") == "1":
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp"],
        check=False,
    )

import redis
from rq import Queue, Worker

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


def main() -> None:
    conn = redis.Redis.from_url(REDIS_URL)
    worker = Worker([Queue("jobs", connection=conn)], connection=conn)
    worker.work(logging_level=os.getenv("LOG_LEVEL", "INFO"))


if __name__ == "__main__":
    main()
