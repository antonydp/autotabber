"""Pipeline: file caricato -> audio -> stems Demucs -> trascrizione per
strumento -> tab multi-traccia (alphaTex + Guitar Pro 5).

Niente YouTube/Cobalt: il file audio/video e' stato caricato dall'utente
via POST /api/upload e sta nella cartella del job (source_file).

Strumenti rilevati automaticamente dall'energia degli stem Demucs:
voce, chitarra, basso, piano, altro (synth/strings) e batteria —
ognuno diventa una traccia del tab, anche la voce (mappata sulla
tastiera, come le tab di melodia).
"""

import json
import logging
import os
import shutil
import subprocess
import traceback
from pathlib import Path

import redis

import analysis
import build_tracks
import rhythm
from transcribe import transcribe_file

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
SR = analysis.SR

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

STAGE_PROGRESS = {
    "preparing": 10,
    "separating": 35,
    "transcribing": 75,
    "rendering": 90,
    "done": 100,
}


def _set(job_id: str, **fields) -> None:
    r.hset(f"job:{job_id}", mapping=fields)


def _stage(job_id: str, stage: str) -> None:
    _set(job_id, stage=stage, status=stage, progress=str(STAGE_PROGRESS.get(stage, 0)))


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


def _build_track_for_stem(stem: str, midi_path: Path, bpm: float, anchor: float):
    """Stem trascritto in MIDI -> traccia con nome/strumento/diteggiatura."""
    from frets import BASS_TUNING, GUITAR_TUNING

    notes = build_tracks.load_notes(midi_path)
    if stem == "vocals":
        return build_tracks.build_melodic_track(
            notes, bpm, anchor, name="Voce", instrument="choiraahs",
            tuning=GUITAR_TUNING, monophonic=True, max_poly=1)
    if stem == "guitar":
        return build_tracks.build_melodic_track(
            notes, bpm, anchor, name="Chitarra", instrument="electricguitarclean",
            tuning=GUITAR_TUNING, monophonic=False, max_poly=4)
    if stem == "bass":
        return build_tracks.build_melodic_track(
            notes, bpm, anchor, name="Basso", instrument="electricbassfinger",
            tuning=BASS_TUNING, monophonic=False, max_poly=2,
            range_lo=build_tracks.BASS_LO, range_hi=build_tracks.BASS_HI)
    if stem == "piano":
        return build_tracks.build_melodic_track(
            notes, bpm, anchor, name="Piano", instrument="acousticgrandpiano",
            tuning=GUITAR_TUNING, monophonic=False, max_poly=6)
    if stem == "other":
        return build_tracks.build_melodic_track(
            notes, bpm, anchor, name="Synth", instrument="stringensemble1",
            tuning=GUITAR_TUNING, monophonic=False, max_poly=6)
    return None


def process(d: Path, source_name: str, title: str, progress=None) -> dict:
    """Core della pipeline, indipendente da redis (usabile nei test).

    progress(stage: str, pct: int) -> None
    """
    progress = progress or (lambda stage, pct: None)
    src = d / source_name
    if not src.exists():
        raise RuntimeError(f"File caricato non trovato: {source_name}")
    meta = {"title": title or "Senza titolo", "artist": "", "duration": 0}

    # 1) estrazione audio -> wav (ffmpeg)
    progress("preparing", STAGE_PROGRESS["preparing"])
    _ffmpeg_to_wav(src, d / "audio.wav")
    duration = _ffprobe_duration(d / "audio.wav") or 0
    if duration > MAX_DURATION:
        raise RuntimeError(
            f"Brano troppo lungo ({int(duration)}s > {MAX_DURATION}s): "
            "aumenta MAX_DURATION_SEC se serve"
        )
    meta["duration"] = duration
    audio = d / "audio.wav"

    # 2) separazione stems + rilevamento strumenti
    progress("separating", 15)
    if DEMUCS_ENABLED:
        stems = analysis.separate_all(audio, d, DEMUCS_MODEL)
        detection = analysis.detect_instruments(stems, audio)
    else:
        # niente separazione: tutto il mix su una traccia chitarra
        stems = {"other": audio}
        detection = {"other": {"present": True, "rel_db": 0.0, "abs_db": 0.0}}

    # 3) tempo/beat dal mix (una sola volta, vale per tutte le tracce)
    import librosa
    mix_y, mix_sr = librosa.load(str(audio), sr=None, mono=True)

    # 4) trascrizione per ogni strumento presente
    tracks = []
    order = ["vocals", "guitar", "bass", "piano", "other"]
    present_melodic = [s for s in order if detection.get(s, {}).get("present")]
    drums_present = detection.get("drums", {}).get("present", False)
    n_steps = len(present_melodic) + (1 if drums_present else 0)
    if n_steps == 0:
        raise RuntimeError(
            "Nessuno strumento rilevato nel brano: prova con un file piu' "
            "chiaro o alza il volume della sorgente"
        )
    done = 0

    notes_for_rhythm: list = []
    for stem in present_melodic:
        progress("transcribing", 40 + int(35 * done / max(n_steps, 1)))
        midi_path = d / f"{stem}.mid"
        transcribe_file(stems[stem], midi_path)
        notes = build_tracks.load_notes(midi_path)
        notes_for_rhythm.extend(notes)
        done += 1

    bpm, anchor = rhythm.estimate_rhythm(
        mix_y, mix_sr, notes_for_rhythm if notes_for_rhythm else
        [{"start": 0.0, "end": duration, "midi": 60}]
    )
    meta["tempo"] = int(round(bpm))

    for stem in present_melodic:
        progress("transcribing", 40 + int(35 * done / max(n_steps, 1)))
        track = _build_track_for_stem(stem, d / f"{stem}.mid", bpm, anchor)
        if track:
            tracks.append(track)
            log.info("traccia %s: %d note", track["name"], track["notes"])
        done += 1

    if drums_present:
        progress("transcribing", 40 + int(35 * done / max(n_steps, 1)))
        drum_track = build_tracks.transcribe_drums(
            stems["drums"], mix_sr, bpm, anchor)
        if drum_track:
            tracks.append(drum_track)
        done += 1

    if not tracks:
        raise RuntimeError(
            "Strumenti rilevati ma nessuna nota trascrivibile: brano troppo "
            "complesso o registrazione di bassa qualita'"
        )

    # 5) rendering alphaTex multi-traccia + export Guitar Pro 5
    progress("rendering", STAGE_PROGRESS["rendering"])
    from gp5 import build_gp5
    from tex import render

    tex, info = render(tracks, meta, bpm)
    result = {"tex": tex, "meta": {**meta, **info}}
    (d / "result.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8")

    gp5_b = build_gp5(tracks, meta.get("title") or "Autotabber", int(round(bpm)))
    gp5_flag = "0"
    if gp5_b:
        (d / "tab.gp5").write_bytes(gp5_b)
        gp5_flag = "1"

    if not KEEP_AUDIO:
        for p in d.glob("*.mid"):
            p.unlink()
        shutil.rmtree(d / "stems", ignore_errors=True)
        for name in ("audio.wav", "input.tmp"):
            p = d / name
            if p.exists():
                p.unlink()

    return {"result": result, "gp5": gp5_flag, "detection": detection}


def run_job(job_id: str) -> None:
    d = DATA_DIR / "jobs" / job_id
    try:
        d.mkdir(parents=True, exist_ok=True)
        source = r.hget(f"job:{job_id}", "source_file")
        title = r.hget(f"job:{job_id}", "title")
        if not source:
            raise RuntimeError("Nessun file caricato per questo job")

        def progress(stage: str, pct: int) -> None:
            _set(job_id, stage=stage, status=stage, progress=str(int(pct)))

        out = process(d, source, title, progress)
        info = out["result"]["meta"]
        _set(job_id,
             duration=str(int(info.get("duration") or 0)),
             tracks=json.dumps(info.get("tracks", []), ensure_ascii=False),
             gp5=out["gp5"],
             detection=json.dumps(out["detection"], ensure_ascii=False))
        _stage(job_id, "done")
        log.info("job %s completato: tempo=%s battute=%s tracce=%s",
                 job_id, info.get("tempo"), info.get("bars"),
                 [t["name"] for t in info.get("tracks", [])])
    except Exception as exc:  # noqa: BLE001
        log.error("job %s fallito:\n%s", job_id, traceback.format_exc())
        _set(job_id, status="error", stage="error", progress="0",
             error=str(exc)[:500])
