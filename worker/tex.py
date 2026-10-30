r"""Generazione alphaTex multi-traccia: una traccia per strumento rilevato.

Formato (alphaTab alphaTex, sintassi verificata sul bundle venduto):
  \title / \artist / \tempo
  \track "Voce" \instrument choiraahs
  \tuning E4 B3 G3 D3 A2 E2        (obbligatoria per le tracce non percussive)
  :4 3.2 5.1 | ...
  \track "Batteria" \instrument percussion
  :8 36 :8 38 ...                   (numeri GM senza suffisso corda)
Ogni traccia ha lo stesso numero di battute (pause per riempire).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

STEPS_PER_BAR = 16  # sedicesimi per battuta (4/4)
DUR_TOKEN = {1: ":16", 2: ":8", 4: ":4", 8: ":2", 16: ":1"}

NOTE_NAME = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _note_name(midi: int) -> str:
    return f"{NOTE_NAME[midi % 12]}{midi // 12 - 1}"


def _escape(text: str) -> str:
    cleaned = (text or "").replace("\\", " ").replace('"', " ").strip()
    return " ".join(cleaned.split())[:80]


def _tuning_token(tuning: List[int]) -> str:
    """\\tuning con cantino per primo (corda 1)."""
    return " ".join(_note_name(m) for m in tuning)


def _emit_track_tokens(track: dict, bars: int) -> List[str]:
    """Token alphaTex di una traccia: note, pause, barre."""
    grouped: List[Tuple[int, List[Tuple[int, int]]]] = track["grouped"]
    end_map: Dict[int, int] = track["end_map"]
    percussion: bool = track["percussion"]

    def token_for(picks) -> str:
        if percussion:
            # percussionArticulation = numero GM (36 cassa, 38 rullante, ...)
            picks = [p if isinstance(p, int) else p[0] for p in picks]
            return "(" + " ".join(str(art) for art in picks) + ")" \
                if len(picks) > 1 else str(picks[0])
        picks = sorted(picks)  # corda crescente
        if len(picks) > 1:
            return "(" + " ".join(f"{fret}.{st}" for st, fret in picks) + ")"
        return f"{picks[0][1]}.{picks[0][0]}"

    out: List[str] = []
    pos = 0
    cur_dur = 0
    total_steps = bars * STEPS_PER_BAR

    def emit(p: int, size: int, tok: str) -> None:
        nonlocal pos, cur_dur
        if p % STEPS_PER_BAR == 0 and p > 0 and out and out[-1] != "|":
            out.append("|")
        if DUR_TOKEN[size] != cur_dur:
            out.append(DUR_TOKEN[size])
            cur_dur = size
        out.append(tok)
        pos = p + size

    for i, (s, picks) in enumerate(grouped):
        if s < pos:  # sovrapposizione residua: salta
            continue
        if s > pos:  # pause fino al prossimo attacco
            gap = min(s, total_steps) - pos
            while gap > 0:
                k = max(k for k in DUR_TOKEN if k <= gap)
                emit(pos, k, "r")
                gap -= k
        if s >= total_steps:
            break
        next_s = grouped[i + 1][0] if i + 1 < len(grouped) else end_map.get(s, s + 1)
        u = min(end_map.get(s, s + 1), next_s) - s
        u = max(u, 1)
        tok = token_for(picks)
        while u > 0:
            k = max(k for k in DUR_TOKEN if k <= u)
            emit(pos, k, tok)
            u -= k
    # riempi fino alla fine (battute complete)
    while pos < total_steps:
        rem = min(STEPS_PER_BAR - (pos % STEPS_PER_BAR), total_steps - pos)
        k = max(k for k in DUR_TOKEN if k <= rem)
        emit(pos, k, "r")
    if out and out[-1] != "|":
        out.append("|")
    return out


def _wrap(tokens: List[str], width: int = 12) -> List[str]:
    lines, line = [], []
    for t in tokens:
        line.append(t)
        if len(line) >= width:
            lines.append(" ".join(line))
            line = []
    if line:
        lines.append(" ".join(line))
    return lines


def render(tracks: List[dict], meta: dict, bpm: float) -> Tuple[str, dict]:
    """Tracce costruite -> alphaTex completo. Solleva se non c'e' nulla."""
    if not tracks:
        raise ValueError("Nessuna traccia generata: impossibile creare il tab")

    max_end = 1
    for t in tracks:
        for s, picks in t["grouped"]:
            max_end = max(max_end, t["end_map"].get(s, s + 1))
    bars = max(2, (max_end + STEPS_PER_BAR - 1) // STEPS_PER_BAR)

    head: List[str] = [
        f'\\title "{_escape(meta.get("title") or "Tab")}"',
    ]
    artist = _escape(meta.get("artist") or "")
    if artist:
        head.append(f'\\artist "{artist}"')
    head.append(f"\\tempo {max(30, min(300, int(round(bpm))))}")
    head.append(".")

    body: List[str] = []
    for t in tracks:
        # sintassi verificata sul bundle: \track + \instrument inline (niente
        # graffe), \tuning su riga separata obbligatoria per le non percussive
        body.append(f'\\track "{_escape(t["name"])}" \\instrument {t["instrument"]}')
        if t.get("tuning") and not t["percussion"]:
            body.append(f"\\tuning {_tuning_token(t['tuning'])}")
        body.extend(_wrap(_emit_track_tokens(t, bars)))
        body.append("")

    tex = "\n".join(head) + "\n".join(body).rstrip() + "\n"
    info = {
        "tempo": int(round(bpm)),
        "bars": bars,
        "tracks": [
            {"name": t["name"], "instrument": t["instrument"],
             "notes": t["notes"], "percussion": t["percussion"]}
            for t in tracks
        ],
        "notes": sum(t["notes"] for t in tracks),
    }
    return tex, info
