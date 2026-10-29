"""Entrypoint worker RQ — esegue la pipeline sui job in coda."""

import os

import redis
from rq import Queue, Worker

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


def main() -> None:
    conn = redis.Redis.from_url(REDIS_URL)
    worker = Worker([Queue("jobs", connection=conn)], connection=conn)
    worker.work(logging_level=os.getenv("LOG_LEVEL", "INFO"))


if __name__ == "__main__":
    main()
