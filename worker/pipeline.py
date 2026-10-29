"""Pipeline: file caricato -> audio.wav (ffmpeg) -> stem chitarra -> MIDI -> alphaTex.

Niente YouTube/Cobalt: il file audio/video e' stato caricato dall'utente
via POST /api/upload e sta nella cartella del job (source_file).
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

import redis

log = logging.getLogger("autotabber.pipeline")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
MAX_DURATION = int(os.getenv("MAX_DURATION_SEC") or os.getenv("YT_MAX_DURATION_SEC") or 900)
DEMUCS_ENABLED = os.getenv("DEMUCS_ENABLED", "1") == "1"
DEMUCS_MODEL = os.getenv("DEMUCS_MODEL", "htdemucs_6s")
KEEP_AUDIO = os.getenv("KEEP_AUDIO", "0") == "1"

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

STAGE_PROGRESS = {
    "preparing": 10,
    "separating": 40,
    "transcribing": 70,
    "rendering": 90,
    "done": 100,
}


def _set(job_id: str, **fields) -> None:
    r.hset(f"job:{job_id}", mapping=fields)


def _stage(job_id: str, stage: str) -> None:
    _set(job_id, stage=stage, status=stage, progress=str(STAGE_PROGRESS.get(stage, 0)))


# Il file audio/video e' stato caricato dall'utente via /api/upload:
# qui si estrae l'audio con ffmpeg e si prosegue con la pipeline.


def _ffmpeg_to_wav(src: Path, dst: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-vn", "-acodec", "pcm_s16le", str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not dst.exists():
        tail = (proc.stderr or proc.stdout or "")[-400:]
        raise RuntimeError(f"ffmpeg: {tail}")


def _ffprobe_duration(path: Path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return None


def separate_guitar(audio: Path, d: Path) -> Path:
    out_dir = d / "separated"
    cmd = [
        sys.executable, "-m", "demucs.separate",
        "-n", DEMUCS_MODEL,
        "--two-stems=guitar",
        "-o", str(out_dir),
        str(audio),
    ]
    log.info("demucs: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-800:]
        raise RuntimeError(f"Demucs fallito: {tail}")
    hits = list(out_dir.rglob("guitar.wav"))
    if not hits:
        raise RuntimeError("Demucs: stem chitarra non trovato")
    stem = d / "guitar.wav"
    shutil.copy(hits[0], stem)
    return stem


def load_notes(midi_path: Path) -> list:
    """MIDI -> [{start, end, midi}] in secondi (usa mido).

    NB: iterando MidiFile, mido converte gia' msg.time in secondi
    (delta) applicando i set_tempo incontrati lungo il brano.
    """
    import mido

    mid = mido.MidiFile(str(midi_path))
    seconds = 0.0
    active: dict = {}
    notes = []
    for msg in mid:
        seconds += msg.time  # secondi (delta), tempo-aware
        if msg.type == "note_on" and msg.velocity > 0:
            active.setdefault((msg.channel, msg.note), []).append(seconds)
        elif msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0
        ):
            starts = active.get((msg.channel, msg.note))
            if starts:
                s = starts.pop(0)
                if seconds > s:
                    notes.append({"start": s, "end": seconds, "midi": msg.note})
    notes.sort(key=lambda n: (n["start"], n["midi"]))
    return notes


def run_job(job_id: str) -> None:
    d = DATA_DIR / "jobs" / job_id
    try:
        d.mkdir(parents=True, exist_ok=True)
        source = r.hget(f"job:{job_id}", "source_file")
        title = r.hget(f"job:{job_id}", "title")
        if not source:
            raise RuntimeError("Nessun file caricato per questo job")
        src = d / source
        if not src.exists():
            raise RuntimeError(f"File caricato non trovato: {source}")
        meta = {"title": title or "Senza titolo", "artist": "", "duration": 0}

        # 1) estrazione audio -> wav (ffmpeg)
        _stage(job_id, "preparing")
        _ffmpeg_to_wav(src, d / "audio.wav")
        duration = _ffprobe_duration(d / "audio.wav") or 0
        if duration > MAX_DURATION:
            raise RuntimeError(
                f"Brano troppo lungo ({int(duration)}s > {MAX_DURATION}s): "
                "aumenta MAX_DURATION_SEC se serve"
            )
        meta["duration"] = duration
        _set(job_id, duration=str(int(duration or 0)))
        audio = d / "audio.wav"
        if not audio.exists():
            raise RuntimeError("Estrazione audio fallita (audio.wav mancante)")

        # 2) separazione stem chitarra
        _stage(job_id, "separating")
        stem = separate_guitar(audio, d) if DEMUCS_ENABLED else audio

        # 3) trascrizione audio -> MIDI (Modal GPU se configurata, altrimenti Basic Pitch)
        _stage(job_id, "transcribing")
        from transcribe import transcribe_file

        midi_path = d / "out.mid"
        transcribe_file(stem, midi_path)

        # 4) rendering alphaTex con diteggiatura + export Guitar Pro 5
        _stage(job_id, "rendering")
        from tex import render
        from gp5 import build_gp5

        notes = load_notes(midi_path)
        tex, info, (chord_list, end_map) = render(notes, meta)
        result = {"tex": tex, "meta": {**meta, **info}}
        (d / "result.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
        gp5_b = build_gp5(
            chord_list, end_map, info["tempo"], meta.get("title") or "Autotabber"
        )
        gp5_flag = "0"
        if gp5_b:
            (d / "tab.gp5").write_bytes(gp5_b)
            gp5_flag = "1"
        _set(job_id, gp5=gp5_flag)

        if not KEEP_AUDIO:
            for name in ("audio.wav", "guitar.wav", "input.tmp"):
                p = d / name
                if p.exists():
                    p.unlink()
            shutil.rmtree(d / "separated", ignore_errors=True)

        _stage(job_id, "done")
        log.info("job %s completato: %s", job_id, info)
    except Exception as exc:  # noqa: BLE001
        log.error("job %s fallito:\n%s", job_id, traceback.format_exc())
        _set(job_id, status="error", stage="error", progress="0",
             error=str(exc)[:500])
