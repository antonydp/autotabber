"""Separazione stems (Demucs htdemucs_6s) + rilevamento strumenti presenti.

Demucs 6 stems: drums, bass, other, vocals, guitar, piano.
L'energia relativa di ogni stem rispetto al mix decide se lo strumento
e' presente nel brano: solo gli stem rilevati diventano tracce di tab.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

log = logging.getLogger("autotabber.analysis")

SR = 22050  # usata solo come default per batteria/rhythm; i load restano nativi
HOP = 512

# Rilevamento via 90° percentile: uno strumento e' presente se il suo
# stem tocca un'energia vicina a quella del mix in ALMENO una parte
# rumorosa del brano. Soglie regolabili via env senza rebuild.
REL_THRESHOLD_DB = float(os.getenv("DETECT_REL_DB", "-25"))
DRUMS_REL_THRESHOLD_DB = float(os.getenv("DETECT_DRUMS_REL_DB", "-28"))
ABS_FLOOR_DB = -60.0

MELODIC_STEMS = ["vocals", "guitar", "bass", "piano", "other"]


def _load_mono(path: Path) -> np.ndarray:
    """Carica mono alla sample rate NATIVA (niente resampling: piu' robusto
    e sufficiente per la misura di energia)."""
    import librosa

    y, _ = librosa.load(str(path), sr=None, mono=True)
    return y


def frame_rms_db(y: np.ndarray) -> np.ndarray:
    """RMS per frame in dB (floored a -240)."""
    if len(y) == 0:
        return np.array([-240.0])
    n = len(y) // HOP
    if n == 0:
        return np.array([20 * np.log10(max(np.sqrt(np.mean(y**2)), 1e-12))])
    frames = y[: n * HOP].reshape(n, HOP)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-12))


def stem_p90_db(y: np.ndarray, active: np.ndarray) -> float:
    """90° percentile dell'energia dello stem sui frame in cui il mix suona.

    Il p90 (e non la media) rende il rilevamento robusto agli strumenti
    che suonano solo in alcune sezioni del brano: basta che siano
    presenti in una parte rumorosa per essere rilevati.
    """
    db = frame_rms_db(y)
    idx = active[: len(db)]
    sel = db[idx] if idx.any() else db
    return float(np.percentile(sel, 90))


def separate_all(audio: Path, d: Path, model: str = "htdemucs_6s") -> dict[str, Path]:
    """Demucs completo: ritorna {stem: wav} copiato in d/stems/."""
    out_dir = d / "separated"
    cmd = [
        sys.executable, "-m", "demucs.separate",
        "-n", model,
        "-o", str(out_dir),
        str(audio),
    ]
    log.info("demucs: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-800:]
        raise RuntimeError(f"Demucs fallito: {tail}")

    stems_dir = d / "stems"
    stems_dir.mkdir(exist_ok=True)
    stems: dict[str, Path] = {}
    for wav in out_dir.rglob("*.wav"):
        name = wav.stem.lower()
        if name in {"drums", "bass", "other", "vocals", "guitar", "piano"}:
            dst = stems_dir / f"{name}.wav"
            shutil.copy(wav, dst)
            stems[name] = dst
    shutil.rmtree(out_dir, ignore_errors=True)
    missing = [s for s in MELODIC_STEMS + ["drums"] if s not in stems]
    if missing:
        raise RuntimeError(f"Demucs: stems mancanti: {missing}")
    return stems


def detect_instruments(stems: dict[str, Path], mix: Path) -> dict[str, dict]:
    """Energia per stem -> strumenti presenti.

    Ritorna {stem: {"present": bool, "rel_db": float}}.
    """
    mix_y = _load_mono(mix)
    mix_db = frame_rms_db(mix_y)
    # frame attivi: il mix sta suonando (evita di misurare stem sul silenzio)
    active = mix_db > (float(np.max(mix_db)) - 45.0)
    mix_p90 = float(np.percentile(mix_db[active], 90))

    out: dict[str, dict] = {}
    for name in MELODIC_STEMS + ["drums"]:
        y = _load_mono(stems[name])
        p90 = stem_p90_db(y, active)
        rel = p90 - mix_p90
        thr = DRUMS_REL_THRESHOLD_DB if name == "drums" else REL_THRESHOLD_DB
        present = (p90 > ABS_FLOOR_DB) and (rel > thr)
        out[name] = {"present": bool(present), "rel_db": round(float(rel), 1),
                     "abs_db": round(float(p90), 1)}
        log.info("stem %-7s rel=%+6.1f dB p90=%6.1f dB -> %s",
                 name, rel, p90, "PRESENTE" if present else "assente")
    return out
