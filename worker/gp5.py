"""Guitar Pro 5 export via pyguitarpro (opzionale; degrada con grazia).

Numerazione corde: 1 = Mi cantino (E4) — la stessa usata in tex.py;
pyguitarpro usa la stessa convenzione per track.strings/values.
"""

from __future__ import annotations

import io
import logging
from typing import Dict, List, Tuple


def build_gp5(
    chord_list: List[Tuple[int, List[Tuple[int, int]]]],
    end_map: Dict[int, int],
    bpm: int,
    title: str = "Autotabber",
) -> bytes | None:
    """Tab -> .gp5. chord_list = [(step16, [(string, fret), ...])], 4/4 su griglia di sedicesimi."""
    try:
        import guitarpro
        from guitarpro import models as M
    except Exception:  # noqa: BLE001
        return None

    try:
        song = M.Song(versionTuple=(5, 1, 0))
        song.title = (title or "Autotabber")[:120]
        song.tempo = int(round(bpm))
        song.pageSetup = M.PageSetup()
        song.notice = []

        steps: Dict[int, List[Tuple[int, int]]] = {}
        for s, picks in chord_list:
            steps.setdefault(s, []).extend(picks)
        total_steps = max(steps.keys(), default=0) + 1
        n_bars = (total_steps + 15) // 16
        if n_bars <= 0:
            return None

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

        track = M.Track(
            song=song,
            number=1,
            name="Guitar",
            strings=[
                M.GuitarString(number=i + 1, value=v)
                for i, v in enumerate([64, 59, 55, 50, 45, 40])
            ],
            channel=M.MidiChannel(channel=0, effectChannel=1, instrument=27),
            fretCount=24,
        )
        track.measures = []
        song.tracks = [track]

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
                for st, fret in sorted(picks):
                    if st in used:
                        continue
                    used.add(st)
                    beat_obj.notes.append(
                        M.Note(
                            beat=beat_obj,
                            value=int(fret),
                            string=int(st),
                            velocity=M.Velocities.forte,
                            type=M.NoteType.normal,
                        )
                    )
                voice.beats.append(beat_obj)
            measure.voices = [voice, M.Voice(measure=measure, beats=[])]
            track.measures.append(measure)

        buf = io.BytesIO()
        guitarpro.write(song, buf)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("autotabber.gp5").warning("GP5 export fallito: %s", exc)
        return None
