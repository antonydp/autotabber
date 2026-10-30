"""Costruzione delle tracce: note grezze per stem -> eventi su griglia.

- stem melodici: filtro durata/velocita', monodia per la voce, piegatura
  d'ottava nel range dello strumento, diteggiatura sulla tastiera;
- batteria: onsets dallo stem drums, classificazione per banda di
  frequenza (cassa/rullante/hi-hat/crash) -> note percussione GM.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

from frets import BASS_TUNING, GUITAR_TUNING, assign, fold_octave
from rhythm import quantize

log = logging.getLogger("autotabber.tracks")

# range playing-friendly: E2..E6 per gli strumenti mappati sulla chitarra
GTR_LO, GTR_HI = 40, 88
BASS_LO, BASS_HI = 28, 62

MIN_DURATION_SEC = 0.09
MIN_VELOCITY = 12


def load_notes(midi_path) -> List[dict]:
    """MIDI -> [{start, end, midi, vel}] in secondi (mido, tempo-aware)."""
    import mido

    mid = mido.MidiFile(str(midi_path))
    seconds = 0.0
    active: dict = {}
    notes: List[dict] = []
    for msg in mid:
        seconds += msg.time  # delta in secondi
        if msg.type == "note_on" and msg.velocity > 0:
            active.setdefault((msg.channel, msg.note), []).append(
                {"t": seconds, "vel": msg.velocity}
            )
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            starts = active.get((msg.channel, msg.note))
            if starts:
                st = starts.pop(0)
                if seconds > st["t"]:
                    notes.append({"start": st["t"], "end": seconds,
                                  "midi": msg.note, "vel": st["vel"]})
    notes.sort(key=lambda n: (n["start"], n["midi"]))
    return notes


def make_monophonic(notes: List[dict]) -> List[dict]:
    """Voce: una nota per volta — le sovrapposizioni tagliano la precedente."""
    out: List[dict] = []
    for n in sorted(notes, key=lambda x: x["start"]):
        if out and n["start"] < out[-1]["end"]:
            if n["end"] - n["start"] > out[-1]["end"] - out[-1]["start"] and \
                    n["start"] <= out[-1]["start"]:
                out[-1] = n  # la nuova copre quasi tutto: sostituisci
            else:
                out[-1]["end"] = max(out[-1]["start"] + 0.05, n["start"])
                if out[-1]["end"] - out[-1]["start"] < MIN_DURATION_SEC:
                    out.pop()
                out.append(n)
        else:
            out.append(n)
    return out


def merge_same_pitch(events: List[Tuple[int, int, int, int]],
                     max_gap: int = 1) -> List[Tuple[int, int, int, int]]:
    """Unisce note adiacenti della stessa altezza separate da un gap corto
    (artefatti tipici del vibrato nella trascrizione della voce)."""
    if not events:
        return events
    out = [list(events[0])]
    for s, dur, midi, vel in events[1:]:
        last = out[-1]
        if midi == last[2] and s - (last[0] + last[1]) <= max_gap:
            last[1] = s + dur - last[0]  # estendi la durata
        else:
            out.append([s, dur, midi, vel])
    return [tuple(x) for x in out]


def filter_notes(notes: List[dict], max_poly: int) -> List[dict]:
    """Durata/velocita' minime e tetto di polifonia per istante."""
    notes = [n for n in notes
             if (n["end"] - n["start"]) >= MIN_DURATION_SEC
             and n.get("vel", 96) >= MIN_VELOCITY]
    if max_poly >= 6:
        return notes
    by_time: Dict[int, List[dict]] = {}
    for i, n in enumerate(notes):
        by_time.setdefault(i, []).append(n)
    # tetto di polifonia per cluster di attacco ravvicinato (< 40 ms)
    kept: List[dict] = []
    i = 0
    while i < len(notes):
        j = i
        cluster = [notes[i]]
        while j + 1 < len(notes) and notes[j + 1]["start"] - notes[i]["start"] < 0.04:
            j += 1
            cluster.append(notes[j])
        if len(cluster) > max_poly:
            cluster = sorted(cluster, key=lambda n: (-n.get("vel", 96),
                                                     -(n["end"] - n["start"])))[:max_poly]
        kept.extend(cluster)
        i = j + 1
    kept.sort(key=lambda n: n["start"])
    return kept


def build_melodic_track(notes: List[dict], bpm: float, anchor: float,
                        *, name: str, instrument: str, tuning: List[int],
                        monophonic: bool = False, max_poly: int = 4,
                        range_lo: int = GTR_LO, range_hi: int = GTR_HI
                        ) -> Optional[dict]:
    """Stem melodico -> traccia con diteggiatura. None se troppo povera."""
    notes = [dict(n, midi=fold_octave(n["midi"], range_lo, range_hi))
             for n in notes]
    if monophonic:
        notes = make_monophonic(notes)
    notes = filter_notes(notes, max_poly=max_poly)
    if len(notes) < 6:
        log.info("traccia %s scartata: solo %d note utili", name, len(notes))
        return None
    events = quantize(notes, bpm, anchor)
    if monophonic:
        events = merge_same_pitch(events)
    chord_list, end_map = assign(events, tuning, range_lo=range_lo, range_hi=range_hi)
    if not chord_list:
        return None
    n_notes = sum(len(p) for _, p in chord_list)
    if n_notes < 6:
        return None
    return {"name": name, "instrument": instrument, "percussion": False,
            "tuning": tuning, "grouped": chord_list, "end_map": end_map,
            "notes": n_notes}


# --- batteria -------------------------------------------------------------

DRUM_KICK, DRUM_SNARE, DRUM_HH, DRUM_OPENHH, DRUM_CRASH = 36, 38, 42, 46, 49


def transcribe_drums(drums_wav, sr: int, bpm: float, anchor: float) -> Optional[dict]:
    """Stem batteria -> traccia percussione (notation, come Songsterr)."""
    import librosa

    y, sr = librosa.load(str(drums_wav), sr=None, mono=True)  # sr nativa
    if len(y) < sr // 2:
        return None

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=256)
    onsets = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=256, units="time",
        backtrack=True,
    )
    onsets = [t for i, t in enumerate(onsets)
              if i == 0 or t - onsets[i - 1] > 0.045]
    if len(onsets) < 8:
        log.info("traccia Batteria scartata: %d onsets", len(onsets))
        return None

    # spettro per ogni onset -> bande low/mid/high
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=256))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    band_low = (freqs >= 30) & (freqs < 150)
    band_mid = (freqs >= 200) & (freqs < 2500)
    band_high = (freqs >= 5000) & (freqs < 11000)

    hits: List[Tuple[int, int]] = []  # (step, articolazione GM)
    hi_energy: List[float] = []
    raw: List[Tuple[float, int, float]] = []
    for t in onsets:
        f0 = int(t * sr / 256)
        f1 = min(f0 + int(0.10 * sr / 256), S.shape[1])
        if f1 <= f0:
            continue
        seg = S[:, f0:f1]
        e_low = float(np.mean(seg[band_low])) if band_low.any() else 0.0
        e_mid = float(np.mean(seg[band_mid])) if band_mid.any() else 0.0
        e_high = float(np.mean(seg[band_high])) if band_high.any() else 0.0
        tot = e_low + e_mid + e_high + 1e-9
        if tot <= 0:
            continue
        # posizione sul beat: per il bias rullante su 2/4
        step_sec = 60.0 / bpm
        beat_pos = ((t - anchor) / step_sec) % 4.0
        on_beat_24 = abs(beat_pos - 1.0) < 0.12 or abs(beat_pos - 3.0) < 0.12

        if e_low / tot > 0.45 and e_low > e_mid * 1.6 and e_low > e_high * 2.2:
            art = DRUM_KICK
            # cassa + hi-hat simultanei (schemas comuni): se c'e' energia alta
            # accanto alla cassa, aggiungi anche lo chiuso
            if e_high > 0.5 * e_mid:
                raw.append((t, DRUM_HH, float(e_high)))
                hi_energy.append(float(e_high))
        elif e_high / tot > 0.42 and e_high > e_mid * 1.4:
            art = DRUM_HH
            hi_energy.append(float(e_high))
        elif on_beat_24 and e_mid > e_high * 0.9:
            art = DRUM_SNARE
        elif e_high > e_mid * 1.1:
            art = DRUM_HH
            hi_energy.append(float(e_high))
        else:
            art = DRUM_SNARE
        raw.append((t, art, float(tot)))

    # crash: i picchi high-energy piu' forti (top 5%, max 1 ogni 2 beat)
    if hi_energy:
        thr = float(np.quantile(hi_energy, 0.95))
        seen_crash = -10.0
        for idx, (t, art, tot) in enumerate(raw):
            if art == DRUM_HH and tot >= thr and t - seen_crash > 2.0 * (60.0 / bpm):
                raw[idx] = (t, DRUM_CRASH, tot)
                seen_crash = t

    step16 = 60.0 / bpm / 4.0
    by_step: Dict[int, List[int]] = {}
    for t, art, _tot in raw:
        s = int(round((t - anchor) / step16))
        if s < 0:
            s = 0
        by_step.setdefault(s, [])
        if art not in by_step[s]:
            by_step[s].append(art)

    # dedup ravvicinata: piu' colpi sullo stesso step -> max 3 articolazioni
    grouped = sorted(
        (s, sorted(arts)[:3]) for s, arts in by_step.items()
    )
    if len(grouped) < 8:
        return None
    end_map = {s: s + 2 for s, _ in grouped}  # colpi da ottavo
    n_notes = sum(len(a) for _, a in grouped)
    log.info("batteria: %d colpi su %d step", n_notes, len(grouped))
    return {"name": "Batteria", "instrument": "percussion", "percussion": True,
            "tuning": [], "grouped": grouped, "end_map": end_map,
            "notes": n_notes}
