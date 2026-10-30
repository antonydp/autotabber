"""Guitar Pro 5 export multi-traccia via pyguitarpro (opzionale; degrada con grazia).

Una Track per strumento rilevato; la batteria come traccia percussione
(canale MIDI 9, note = articolazioni GM 36/38/42/46/49).
Convenzione corde: 1 = cantino — la stessa di tex.py/frets.py.
"""

from __future__ import annotations

import io
import logging
from typing import Dict, List, Tuple

# programmi GM (0-based) per il playback, in linea con gli strumenti alphaTex
PROGRAMS = {
    "choiraahs": 52,
    "electricguitarclean": 27,
    "electricbassfinger": 33,
    "acousticgrandpiano": 0,
    "stringensemble1": 48,
    "percussion": 0,  # canale 9: il program non conta
}


def build_gp5(tracks: List[dict], title: str = "Autotabber", bpm: int = 120) -> bytes | None:
    """Tracce costruite -> .gp5.

    tracks = [{"name", "instrument", "percussion", "tuning", "grouped",
               "end_map"}] con grouped = [(step16, [(corda, tasto)|int, ...])].
    Per la percussione i "tasti" sono direttamente numeri GM.
    """
    try:
        import guitarpro
        from guitarpro import models as M
    except Exception:  # noqa: BLE001
        return None

    try:
        song = M.Song(versionTuple=(5, 1, 0))
        song.title = (title or "Autotabber")[:120]
        song.tempo = max(30, min(300, int(round(bpm))))
        song.pageSetup = M.PageSetup()
        song.notice = []

        # numero battute = max tra le tracce
        total_steps = 1
        for t in tracks:
            for s, _picks in t["grouped"]:
                total_steps = max(total_steps, t["end_map"].get(s, s + 1))
        n_bars = max(1, (total_steps + 15) // 16)

        ticks_per_16th = 960 // 4
        headers = [
            M.MeasureHeader(
                number=i + 1,
                start=i * 4 * 960,
                timeSignature=M.TimeSignature(
                    numerator=4,
                    denominator=M.Duration(value=M.Duration.quarter),
                    beams=[8, 8, 8, 8],
                ),
            )
            for i in range(n_bars)
        ]
        song.measureHeaders = headers

        song_tracks: List[M.Track] = []
        for idx, t in enumerate(tracks):
            percussion = bool(t.get("percussion"))
            program = PROGRAMS.get(t.get("instrument", ""), 27)
            if percussion:
                strings = [M.GuitarString(number=1, value=64)]
                channel = M.MidiChannel(channel=9, effectChannel=10, instrument=0)
            else:
                tuning = t.get("tuning") or [64, 59, 55, 50, 45, 40]
                strings = [
                    M.GuitarString(number=i + 1, value=v)
                    for i, v in enumerate(tuning)
                ]
                channel = M.MidiChannel(channel=idx if idx < 9 else idx + 1,
                                        effectChannel=(idx if idx < 9 else idx + 1) + 1,
                                        instrument=program)
            track = M.Track(
                song=song,
                number=idx + 1,
                name=(t.get("name") or "Track")[:30],
                strings=strings,
                channel=channel,
                isPercussionTrack=percussion,
                fretCount=24,
            )
            track.measures = []

            steps: Dict[int, List] = {}
            for s, picks in t["grouped"]:
                steps.setdefault(s, []).extend(picks)

            for bar_idx, mh in enumerate(headers):
                measure = M.Measure(track=track, header=mh)
                voice = M.Voice(measure=measure)
                voice.beats = []
                for sub in range(16):
                    step = bar_idx * 16 + sub
                    picks = steps.get(step, [])
                    beat_obj = M.Beat(
                        voice=voice,
                        duration=M.Duration(value=M.Duration.sixteenth),
                        status=M.BeatStatus.normal if picks else M.BeatStatus.rest,
                        start=step * ticks_per_16th,
                    )
                    used = set()
                    for pick in sorted(picks):
                        if percussion:
                            st, fret = 1, int(pick)
                        else:
                            st, fret = int(pick[0]), int(pick[1])
                        if st in used:
                            continue
                        used.add(st)
                        beat_obj.notes.append(
                            M.Note(
                                beat=beat_obj,
                                value=fret,
                                string=st,
                                velocity=M.Velocities.forte,
                                type=M.NoteType.normal,
                            )
                        )
                    voice.beats.append(beat_obj)
                measure.voices = [voice, M.Voice(measure=measure, beats=[])]
                track.measures.append(measure)

            song_tracks.append(track)

        song.tracks = song_tracks
        buf = io.BytesIO()
        guitarpro.write(song, buf)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("autotabber.gp5").warning("GP5 export fallito: %s", exc)
        return None
