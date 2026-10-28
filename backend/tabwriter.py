"""Guitar Pro 5 export via pyguitarpro (optional; graceful degradation)."""

from __future__ import annotations

from collections import defaultdict

from backend.fingering import Column


def build_gp5(columns: list[Column],
              tempo: float,
              title: str = "AutoTabber transcription") -> bytes | None:
    """Build a .gp5 file from tab columns. Returns bytes or None on failure.

    Single guitar track, standard tuning, 4/4. Rhythm is on a 16th-note grid
    matching the alphaTex output.
    """
    try:
        import guitarpro
        from guitarpro import models as M
    except Exception:
        return None

    try:
        song = M.Song(versionTuple=(5, 1, 0))
        song.title = (title or "AutoTabber")[:120]
        song.tempo = int(round(tempo))
        song.pageSetup = M.PageSetup()
        song.notice = []

        beat = 60.0 / max(tempo, 1.0)
        grid = beat / 4.0  # seconds per 16th
        ticks_per_16th = 960 // 4  # 960 ticks per quarter

        # map: absolute 16th-step -> notes
        step_notes: dict[int, list] = defaultdict(list)
        for col in columns:
            step = int(round(col.start / grid))
            step_notes[step].extend(col.notes)

        total_steps = max(step_notes.keys(), default=0) + 1
        n_bars = (total_steps + 15) // 16

        headers = []
        for i in range(max(1, n_bars)):
            mh = M.MeasureHeader(
                number=i + 1,
                start=i * 4 * 960,
                timeSignature=M.TimeSignature(
                    numerator=4,
                    denominator=M.Duration(value=M.Duration.quarter),
                    beams=[8, 8, 8, 8],
                ),
            )
            headers.append(mh)
        song.measureHeaders = headers

        track = M.Track(
            song=song,
            number=1,
            name="Guitar",
            strings=[M.GuitarString(number=i + 1, value=v)
                     for i, v in enumerate([64, 59, 55, 50, 45, 40])],
            channel=M.MidiChannel(channel=0, effectChannel=1, instrument=30),
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
                notes_here = step_notes.get(step, [])
                if notes_here:
                    beat_obj = M.Beat(
                        voice=voice,
                        duration=M.Duration(value=M.Duration.sixteenth),
                        status=M.BeatStatus.normal,
                        start=step * ticks_per_16th,
                    )
                    used = set()
                    for n in notes_here[:6]:
                        gp_string = 6 - (n.string - 1)
                        if gp_string in used:
                            continue
                        used.add(gp_string)
                        note = M.Note(
                            beat=beat_obj,
                            value=n.fret,
                            string=gp_string,
                            velocity=M.Velocities.forte,
                            type=M.NoteType.normal,
                        )
                        beat_obj.notes.append(note)
                else:
                    beat_obj = M.Beat(
                        voice=voice,
                        duration=M.Duration(value=M.Duration.sixteenth),
                        status=M.BeatStatus.rest,
                        start=step * ticks_per_16th,
                    )
                voice.beats.append(beat_obj)
            measure.voices = [voice, M.Voice(measure=measure, beats=[])]
            track.measures.append(measure)

        import io
        buf = io.BytesIO()
        guitarpro.write(song, buf)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("autotabber").warning("GP5 export failed: %s", exc)
        return None
