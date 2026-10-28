"""Autotabber API — orchestrazione job YouTube -> tab."""

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from rq import Queue

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
JOB_TTL = int(os.getenv("JOB_TTL_SEC", str(7 * 86400)))

# NB: due client — RQ richiede una connessione SENZA decode_responses
r_h = redis.Redis.from_url(REDIS_URL, decode_responses=True)   # hash/metadati
r_q = redis.Redis.from_url(REDIS_URL)                          # coda RQ
q = Queue("jobs", connection=r_q)

app = FastAPI(title="Autotabber", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
)


class JobIn(BaseModel):
    url: str


def _job_dir(jid: str) -> Path:
    return DATA_DIR / "jobs" / jid


@app.get("/api/health")
def health():
    try:
        r_h.ping()
        return {"ok": True}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail=f"redis: {exc}")


@app.post("/api/jobs", status_code=201)
def create_job(body: JobIn):
    url = body.url.strip()
    if not YT_RE.search(url):
        raise HTTPException(status_code=400, detail="URL YouTube non valido")
    jid = uuid.uuid4().hex[:12]
    r_h.hset(
        f"job:{jid}",
        mapping={
            "id": jid,
            "url": url,
            "status": "queued",
            "stage": "queued",
            "progress": "0",
            "created_at": str(int(time.time())),
        },
    )
    r_h.expire(f"job:{jid}", JOB_TTL)
    q.enqueue(
        "pipeline.run_job",
        jid,
        job_timeout=int(os.getenv("JOB_TIMEOUT_SEC", "3600")),
        result_ttl=JOB_TTL,
        failure_ttl=JOB_TTL,
    )
    return {"id": jid, "status": "queued"}


@app.get("/api/jobs")
def list_jobs():
    jobs = [r_h.hgetall(k) for k in r_h.scan_iter("job:*")]
    jobs.sort(key=lambda j: int(j.get("created_at", 0)), reverse=True)
    return jobs


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    job = r_h.hgetall(f"job:{jid}")
    if not job:
        raise HTTPException(status_code=404, detail="Job non trovato")
    return job


@app.get("/api/jobs/{jid}/tex")
def get_tex(jid: str):
    f = _job_dir(jid) / "result.json"
    if not f.exists():
        raise HTTPException(status_code=404, detail="Tab non disponibile")
    data = json.loads(f.read_text(encoding="utf-8"))
    return PlainTextResponse(data.get("tex", ""), media_type="text/plain; charset=utf-8")


@app.get("/api/jobs/{jid}/midi")
def get_midi(jid: str):
    f = _job_dir(jid) / "out.mid"
    if not f.exists():
        raise HTTPException(status_code=404, detail="MIDI non disponibile")
    return FileResponse(f, media_type="audio/midi", filename="autotabber.mid")


@app.get("/api/jobs/{jid}/gp5")
def get_gp5(jid: str):
    f = _job_dir(jid) / "tab.gp5"
    if not f.exists():
        raise HTTPException(status_code=404, detail="GP5 non disponibile")
    return FileResponse(f, media_type="application/octet-stream", filename="autotabber.gp5")


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str):
    shutil.rmtree(_job_dir(jid), ignore_errors=True)
    r_h.delete(f"job:{jid}")
    return {"ok": True}
