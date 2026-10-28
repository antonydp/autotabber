"""alphaTex generation from tab columns (see fingering.py).

Rhythm: quantizes to a 16th-note grid at the estimated tempo and emits
standard durations (with dotted variants), inserting rests and bar lines so
every bar is metrically complete (4/4).

alphaTex string numbering: 1 = lowest string.
"""

from __future__ import annotations

from backend.fingering import Column

# duration units expressible in alphaTex: (sixteenths, token)
DURATIONS: list[tuple[int, str]] = [
    (16, ":1"),      # whole
    (12, ":2."),     # dotted half
    (8, ":2"),       # half
    (6, ":4."),      # dotted quarter
    (4, ":4"),       # quarter
    (3, ":8."),      # dotted eighth
    (2, ":8"),       # eighth
    (1, ":16"),      # sixteenth
]

BEATS_PER_BAR = 4
STEPS_PER_BAR = BEATS_PER_BAR * 4  # 16 sixteenths


def _decompose(steps: int) -> list[tuple[int, str]]:
    """Split a number of 16th-steps into standard duration tokens."""
    out = []
    remaining = max(0, steps)
    for value, token in DURATIONS:
        while remaining >= value:
            out.append((value, token))
            remaining -= value
    if remaining > 0:
        out.append((1, ":16"))
    return out


def _column_token(col: Column) -> str:
    return " ".join(f"{n.fret}.{n.string}" for n in col.notes)


def build_alphatex(columns: list[Column],
                   tempo: float,
                   title: str = "AutoTabber transcription",
                   instrument_program: int = 30,
                   tuning_names: list[str] | None = None,
                   ) -> str:
    """Build a complete alphaTex document.

    tuning_names: string names ordered HIGH -> LOW (alphaTex \\tuning order),
    e.g. ["E4", "B3", "G3", "D3", "A2", "E2"].
    """
    if tuning_names is None:
        tuning_names = ["E4", "B3", "G3", "D3", "A2", "E2"]

    beat = 60.0 / max(tempo, 1.0)
    grid = beat / 4.0  # one 16th note

    safe_title = (
        str(title)
        .replace("\\", "")
        .replace('"', "'")
        .replace("\n", " ")
        .strip() or "AutoTabber transcription"
    )
    lines = [
        f'\\title "{safe_title}"',
        f"\\tempo {int(round(tempo))}",
        f"\\instrument {instrument_program}",
        "\\tuning " + " ".join(tuning_names),
        ".",
    ]

    tokens: list[str] = []
    bar_steps = 0   # sixteenths accumulated in current bar
    abs_steps = 0   # total sixteenths emitted so far

    def new_bar_if_full():
        nonlocal bar_steps
        if bar_steps >= STEPS_PER_BAR:
            tokens.append("|")
            bar_steps = 0

    def emit_rests(steps: int):
        nonlocal bar_steps, abs_steps
        remaining = steps
        while remaining > 0:
            space = STEPS_PER_BAR - bar_steps
            if space == 0:
                tokens.append("|")
                bar_steps = 0
                space = STEPS_PER_BAR
            take = min(space, remaining)
            for value, token in _decompose(take):
                tokens.append(f"{token} r")
                bar_steps += value
                abs_steps += value
            remaining -= take
        new_bar_if_full()

    for idx, col in enumerate(columns):
        start_q = max(round(col.start / grid), abs_steps)
        gap = start_q - abs_steps
        if gap > 0:
            emit_rests(gap)

        if not col.notes:
            emit_rests(1)
            continue

        # duration: until next column start (fallback: one beat)
        if idx + 1 < len(columns):
            next_start = round(columns[idx + 1].start / grid)
        else:
            next_start = start_q + 4
        dur_q = max(1, next_start - start_q)

        # never cross a bar line with a single token; truncate at bar end
        space = STEPS_PER_BAR - bar_steps
        dur_q = min(dur_q, space if space > 0 else STEPS_PER_BAR)
        dur_q = max(1, dur_q)

        value, token = 1, ":16"
        for v, t in DURATIONS:
            if v <= dur_q:
                value, token = v, t
                break

        tokens.append(f"{token} {_column_token(col)}")
        bar_steps += value
        abs_steps += value
        new_bar_if_full()

    if bar_steps > 0:
        emit_rests(STEPS_PER_BAR - bar_steps)
    if tokens and tokens[-1] == "|":
        tokens.pop()

    lines.append(" ".join(tokens))
    return "\n".join(lines) + "\n"
