"""Pipeline orchestration: YouTube -> stem separation -> transcription -> tab.

Runs inside the Docker web app. Heavy AI stages are delegated to Modal
functions (see modal_app.py) using the credentials provided via environment.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pretty_midi
import yt_dlp

from . import settings
from .alphatex import build_alphatex
from .fingering import midi_notes_to_tab
from .tabwriter import build_gp5


@dataclass
class Job:
    id: str
    url: str
    instrument: str = "electric"
    status: str = "queued"          # queued|running|done|error
    stage: str = "In coda"          # human readable stage
    progress: int = 0               # 0..100
    error: str | None = None
    title: str = "Trascrizione"
    tempo: float = 120.0
    alphatex: str | None = None
    files: dict[str, str] = field(default_factory=dict)  # name -> path
    created: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "error": self.error,
            "title": self.title,
            "tempo": self.tempo,
            "instrument": self.instrument,
            "files": {k: f"/api/jobs/{self.id}/files/{v}" for k, v in self.files.items()},
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, url: str, instrument: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], url=url, instrument=instrument)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job: Job, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(job, k, v)


STORE = JobStore()


# ---------------------------------------------------------------------------
# Modal invocation helpers
# ---------------------------------------------------------------------------

def _import_modal_app():
    import modal_app  # shipped at repo root / image root
    return modal_app


def _ensure_modal_deployed() -> None:
    if not settings.AUTO_DEPLOY_MODAL:
        return
    subprocess.run(
        ["python", "-m", "modal", "deploy", "modal_app.py", "--yes"],
        capture_output=True, timeout=1200,
    )


def _call_modal(fn_name: str, payload: bytes):
    import modal
    from modal.exception import NotFoundError, ExecutionError, ServiceError, RemoteError

    retryable = (NotFoundError, ExecutionError, ServiceError, RemoteError)

    try:
        fn = modal.Function.from_name("autotabber", fn_name)
    except NotFoundError:
        _ensure_modal_deployed()
        fn = modal.Function.from_name("autotabber", fn_name)
    try:
        fn.hydrate()
        return fn.remote(payload)
    except retryable:
        _ensure_modal_deployed()
        fn = modal.Function.from_name("autotabber", fn_name)
        return fn.remote(payload)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _download_audio(job: Job, tmp: Path) -> tuple[Path, str]:
    """yt-dlp download bestaudio; returns (source_path, title)."""
    out_base = str(tmp / "source")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": out_base + ".%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": 400 * 1024 * 1024,
        "socket_timeout": 30,
        "retries": 3,
        # new YouTube throttling requires a JS runtime; use node when present
        "js_runtimes": {"node": {}},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(job.url, download=True)
        title = (info or {}).get("title") or "Trascrizione"
        # if playlist extraction sneaked in, take the first entry
        if "entries" in (info or {}):
            entries = [e for e in info["entries"] if e]
            info = entries[0] if entries else info
            title = (info or {}).get("title") or title
    src = Path(out_base + "." + (info.get("ext", "m4a") if info else "m4a"))
    if not src.exists():
        # fallback: find any downloaded source.*
        cands = sorted(tmp.glob("source.*"))
        if not cands:
            raise RuntimeError("Download fallito: nessun file audio trovato")
        src = cands[0]
    return src, title


def _convert_wav(src: Path, dst: Path, max_duration: int) -> None:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-ac", "2", "-ar", "44100",
        "-t", str(max_duration),
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr[-1000:]}")


def _midi_to_notes(midi_bytes: bytes) -> list[tuple[float, float, int]]:
    import io
    pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
    notes: list[tuple[float, float, int]] = []
    for inst in pm.instruments:
        for n in inst.notes:
            dur = max(0.05, n.end - n.start)
            notes.append((float(n.start), float(n.start + dur), int(n.pitch)))
    notes.sort()
    return notes


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_job(job: Job) -> None:
    """Main pipeline; executed in a background thread."""
    tmp = Path(tempfile.mkdtemp(prefix="autotabber_"))
    try:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        out_dir = settings.DATA_DIR / job.id
        out_dir.mkdir(parents=True, exist_ok=True)

        preset = settings.INSTRUMENTS.get(job.instrument, settings.INSTRUMENTS["electric"])

        # 1) download
        STORE.update(job, status="running", stage="Download audio da YouTube", progress=5)
        src, title = _download_audio(job, tmp)
        STORE.update(job, title=title, stage="Conversione audio", progress=15)
        wav = tmp / "mix.wav"
        _convert_wav(src, wav, settings.MAX_DURATION)

        # 2) stem separation (Modal GPU)
        STORE.update(job, stage="Separazione traccia chitarra (GPU Modal, Mel-Band RoFormer)", progress=25)
        wav_bytes = wav.read_bytes()
        stem_bytes = _call_modal("separate_guitar", wav_bytes)

        stem_path = out_dir / "stem_guitar.wav"
        stem_path.write_bytes(stem_bytes)

        # 3) transcription (Modal CPU)
        STORE.update(job, stage="Trascrizione note (Basic Pitch)", progress=55)
        result = _call_modal("transcribe_guitar", stem_bytes)

        midi_b = base64.b64decode(result["midi"])
        tempo = float(result.get("tempo") or 120.0)
        midi_path = out_dir / "track.mid"
        midi_path.write_bytes(midi_b)

        # 4) fingering + tab
        STORE.update(job, stage="Assegnazione corde e tasti", progress=80)
        notes = _midi_to_notes(midi_b)
        if not notes:
            raise RuntimeError(
                "Nessuna nota rilevata nella traccia chitarra. "
                "Prova con un brano con la chitarra piu presente."
            )
        columns = midi_notes_to_tab(
            notes, tuning=preset["tuning"], max_fret=preset["max_fret"]
        )
        tex = build_alphatex(
            columns, tempo=tempo, title=title,
            instrument_program=preset["program"],
            tuning_names=preset["tuning_names"],
        )
        tex_path = out_dir / "tab.alphatex"
        tex_path.write_text(tex, encoding="utf-8")

        files = {
            "stem_guitar.wav": stem_path.name,
            "track.mid": midi_path.name,
            "tab.alphatex": tex_path.name,
        }

        # 5) optional GP5 export
        gp5_b = build_gp5(columns, tempo=tempo, title=title)
        if gp5_b:
            gp5_path = out_dir / "tab.gp5"
            gp5_path.write_bytes(gp5_b)
            files["tab.gp5"] = gp5_path.name

        STORE.update(
            job, status="done", stage="Completato", progress=100,
            alphatex=tex, tempo=tempo, files=files,
        )

    except Exception as exc:  # noqa: BLE001
        STORE.update(job, status="error", error=str(exc)[:1500], stage="Errore")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
