"""Tempo, beat-grid e quantizzazione a sedicesimi.

Il BPM viene stimato con librosa (beat tracking sul mix); se il tracker
fallisce si ripiega sulla stima da inter-onset intervals delle note.
La griglia di quantizzazione e' ancorata al primo beat rilevato, cosi'
le battute cadono sui tempi veri del brano e non sul primo attacco.
"""

from __future__ import annotations

import logging
from statistics import median
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger("autotabber.rhythm")

SR = 22050
STEPS_PER_BAR = 16  # sedicesimi per battuta (4/4)


def _fit_bpm_from_notes(notes: List[dict]) -> float:
    """Fallback: BPM dai profili di attacco (IOI, mediana ripiegata in 70..200)."""
    if len(notes) < 4:
        return 120.0
    onsets = sorted(n["start"] for n in notes)
    iois = [b - a for a, b in zip(onsets, onsets[1:]) if 0.06 <= (b - a) <= 1.5]
    if not iois:
        return 120.0
    folded = []
    for t in (60.0 / i for i in iois):
        while t < 70:
            t *= 2
        while t >= 200:
            t /= 2
        folded.append(t)
    return max(50.0, min(200.0, float(median(folded))))


def estimate_rhythm(mix: Optional[object], sr: int, notes: List[dict]) -> Tuple[float, float]:
    """Ritorna (bpm, anchor) dove anchor = inizio della griglia in secondi.

    `mix` e' l'array mono del brano (None -> fallback sulle note).
    """
    bpm: Optional[float] = None
    anchor = 0.0
    if mix is not None and len(mix) > sr:  # almeno 1 secondo
        try:
            import librosa

            onset_env = librosa.onset.onset_strength(y=mix, sr=sr)
            tempo, beats = librosa.beat.beat_track(
                onset_envelope=onset_env, sr=sr, units="time", trim=False
            )
            tempo = float(np.asarray(tempo).ravel()[0]) if np.size(tempo) else 0.0
            beats = list(np.atleast_1d(beats))
            if 40.0 <= tempo <= 220.0 and len(beats) >= 4:
                # media dei delta dei beat: tempo piu' stabile del valore grezzo
                deltas = [b - a for a, b in zip(beats, beats[1:])]
                if deltas:
                    t2 = 60.0 / median(deltas)
                    while t2 < 60:
                        t2 *= 2
                    while t2 >= 190:
                        t2 /= 2
                    if 40.0 <= t2 <= 220.0:
                        tempo = t2
                bpm = tempo
                anchor = float(beats[0])
        except Exception as exc:  # noqa: BLE001
            log.warning("beat tracking fallito (%s), uso fallback IOI", exc)

    if bpm is None:
        bpm = _fit_bpm_from_notes(notes)
        anchor = min((n["start"] for n in notes), default=0.0)
    bpm = max(40.0, min(220.0, float(bpm)))
    log.info("tempo %.1f BPM, anchor %.2fs", bpm, anchor)
    return bpm, anchor


def quantize(notes: List[dict], bpm: float, anchor: float,
             min_dur: float = 0.07) -> List[Tuple[int, int, int, int]]:
    """Note (secondi) -> eventi su griglia di sedicesimi.

    Ritorna [(step, dur, midi, vel)] ordinati; dur >= 1 step.
    Le note prima dell'anchor vengono compresse su step 0 (rare: pickup).
    """
    step_sec = 60.0 / bpm / 4.0
    events: List[Tuple[int, int, int, int]] = []
    for n in notes:
        dur = n["end"] - n["start"]
        if dur < min_dur:
            continue
        s = int(round((n["start"] - anchor) / step_sec))
        e = int(round((n["end"] - anchor) / step_sec))
        if s < 0:
            s = 0
        if e <= s:
            e = s + 1
        if e - s > 32:  # note tenute oltre 2 battute: tronca
            e = s + 32
        events.append((s, e - s, int(n["midi"]), int(n.get("vel", 96))))
    events.sort(key=lambda ev: (ev[0], -ev[3], ev[2]))
    return events
