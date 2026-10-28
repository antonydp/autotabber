"""AutoTabber web API (FastAPI)."""

from __future__ import annotations

import json
import mimetypes
import threading
from pathlib import Path

# ES modules must be served with a JS MIME type
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from . import settings
from .pipeline import STORE, run_job

app = FastAPI(title="AutoTabber", version="1.0.0")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class JobRequest(BaseModel):
    url: str
    instrument: str = "electric"

    @field_validator("instrument")
    @classmethod
    def _check_instrument(cls, v: str) -> str:
        if v not in settings.INSTRUMENTS:
            raise ValueError(f"instrument must be one of {list(settings.INSTRUMENTS)}")
        return v


@app.post("/api/jobs")
def create_job(req: JobRequest):
    url = req.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "Inserisci un URL valido (http/https)")
    job = STORE.create(url=url, instrument=req.instrument)
    thread = threading.Thread(target=run_job, args=(job,), daemon=True)
    thread.start()
    return {"id": job.id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = STORE.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    data = job.public()
    if job.status == "done":
        data["alphatex"] = job.alphatex
    return data


@app.get("/api/jobs/{job_id}/files/{name}")
def get_file(job_id: str, name: str):
    job = STORE.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if name not in job.files.values():
        raise HTTPException(404, "File not found")
    path = settings.DATA_DIR / job_id / name
    if not path.exists():
        raise HTTPException(404, "File missing on disk")
    media = "application/octet-stream"
    if name.endswith(".wav"):
        media = "audio/wav"
    elif name.endswith(".mid"):
        media = "audio/midi"
    elif name.endswith(".alphatex"):
        media = "text/plain; charset=utf-8"
    return FileResponse(path, media_type=media, filename=name)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "modal_configured": bool(settings.MODAL_TOKEN_ID and settings.MODAL_TOKEN_SECRET),
    }


DEMO_ALPHATEX = r"""\title "AutoTabber demo"
\tempo 96
\instrument 30
\tuning E4 B3 G3 D3 A2 E2
.
:8 0.1 :8 3.1 :8 5.1 :8 7.1 :8 5.1 :8 3.1 :8 0.1 :8 3.1 | :8 5.1 :8 7.1 :8 8.1 :8 7.1 :8 5.1 :8 3.1 :4 0.1 | :4 2.3 :4 0.4 :2 0.5 | :8 5.1 :8 5.1 :8 7.1 :8 8.1 :8 10.1 :8 8.1 :8 7.1 :8 5.1 | :4 3.1 :4 0.1 :2 0.1 |
"""


@app.get("/test-player")
def test_player():
    """Player demo (no GPU needed) to verify the alphaTab setup."""
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    inject = f"""
<script type="module">
  import * as alphaTab from '/vendor/alphatab/alphaTab.min.mjs';
  window.__fakeResult = function () {{
    const job = {{
      title: 'AutoTabber demo (player test)',
      tempo: 96,
      instrument: 'electric',
      files: {{}},
      alphatex: {json.dumps(DEMO_ALPHATEX)},
    }};
    document.getElementById('url').value = 'https://example.com/demo';
    showResult(job);
  }};
  window.addEventListener('load', () => setTimeout(() => window.__fakeResult(), 300));
</script>
</body>"""
    html = html.replace("</body>", inject)
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html)


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)[:800]})


# --- static frontend (kept last so /api routes take priority) ---
app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")
app.mount("/vendor", StaticFiles(directory=str(FRONTEND_DIR / "vendor")), name="vendor")


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")
