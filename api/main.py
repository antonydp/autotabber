"""Autotabber API — orchestrazione job file -> tab.

Niente YouTube/Cobalt: l'utente carica direttamente un file audio (o un
video da cui estrarre l'audio, es. .mp4/.webm) e la pipeline lo trasforma
in tablatura suonabile.
"""

import json
import os
import shutil
import time
import uuid
from pathlib import Path

import redis
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from rq import Queue

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
JOB_TTL = int(os.getenv("JOB_TTL_SEC", str(7 * 86400)))
UPLOAD_MAX_MB = int(os.getenv("UPLOAD_MAX_MB", "300"))

# formati audio/video accettati (l'audio viene estratto con ffmpeg)
ALLOWED_EXT = {
    ".mp3", ".wav", ".m4a", ".m4b", ".flac", ".ogg", ".oga",
    ".opus", ".aac", ".wma", ".webm", ".mp4",
}

# NB: due client — RQ richiede una connessione SENZA decode_responses
r_h = redis.Redis.from_url(REDIS_URL, decode_responses=True)   # hash/metadati
r_q = redis.Redis.from_url(REDIS_URL)                          # coda RQ
q = Queue("jobs", connection=r_q)

app = FastAPI(title="Autotabber", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _job_dir(jid: str) -> Path:
    return DATA_DIR / "jobs" / jid


@app.get("/api/health")
def health():
    try:
        r_h.ping()
        return {"ok": True}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail=f"redis: {exc}")


@app.post("/api/upload", status_code=201)
async def upload(file: UploadFile = File(...)):
    """Carica un file audio/video e crea il job di trascrizione."""
    fname = file.filename or ""
    ext = Path(fname).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Formato '{ext or fname or '?'}' non supportato. "
                   f"Usa uno di: {', '.join(sorted(ALLOWED_EXT))}",
        )

    jid = uuid.uuid4().hex[:12]
    d = _job_dir(jid)
    d.mkdir(parents=True)
    dest = d / f"input{ext}"

    size = 0
    limit = UPLOAD_MAX_MB * 1024 * 1024
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File troppo grande ({UPLOAD_MAX_MB} MB di limite). "
                               "Alza UPLOAD_MAX_MB se serve.",
                    )
                out.write(chunk)
    except HTTPException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Salvataggio fallito: {exc}")

    r_h.hset(
        f"job:{jid}",
        mapping={
            "id": jid,
            "filename": fname,
            "title": Path(fname).stem or "Senza titolo",
            "source_file": dest.name,
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
    return {"id": jid, "status": "queued", "size": size}


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
