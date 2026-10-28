"""Generazione alphaTex: note trascritte -> tab chitarra con diteggiatura.

Strategia:
  - stima BPM dai profili di attacco (inter-onset intervals, mediana ripiegata)
  - quantizzazione a sedicesimi sul tempo stimato
  - assegnazione corda/tasto (diteggiatura) su accordatura standard,
    preferendo tasti bassi e continuita' di posizione della mano
  - note simultanee raggruppate in accordi (corde distinte)
"""

from statistics import median
from typing import Dict, List, Optional, Tuple

# Corde 1..6 (1 = Mi cantino): accordatura standard E4 B3 G3 D3 A2 E2
TUNING: List[int] = [64, 59, 55, 50, 45, 40]
MAX_FRET = 22
STEPS_PER_BAR = 16  # sedicesimi per battuta (4/4)
DUR_TOKEN = {1: ":16", 2: ":8", 4: ":4", 8: ":2", 16: ":1"}


def _fit_bpm(notes: List[dict]) -> int:
    if len(notes) < 4:
        return 120
    onsets = sorted(n["start"] for n in notes)
    iois = [b - a for a, b in zip(onsets, onsets[1:]) if 0.06 <= (b - a) <= 1.5]
    if not iois:
        return 120
    folded = []
    for t in (60.0 / i for i in iois):
        while t < 70:
            t *= 2
        while t >= 200:
            t /= 2
        folded.append(t)
    bpm = int(round(median(folded)))
    return max(50, min(200, bpm))


def _choose(
    midi: int, used: set, c_fret: float, c_str: float
) -> Optional[Tuple[int, int]]:
    """Ritorna (corda, tasto) per una nota, o None se fuori estensione."""
    if midi < TUNING[5]:
        midi += 12
        if midi < TUNING[5]:
            return None
    best: Optional[Tuple[int, int]] = None
    best_score = 0.0
    for s in range(1, 7):
        fret = midi - TUNING[s - 1]
        if fret < 0 or fret > MAX_FRET or s in used:
            continue
        score = (
            fret
            + 0.35 * abs(fret - c_fret)
            + 0.15 * abs(s - c_str)
            - (1.5 if fret == 0 else 0.0)
        )
        if best is None or score < best_score:
            best, best_score = (s, fret), score
    return best


def _escape(text: str) -> str:
    cleaned = (text or "").replace("\\", " ").replace('"', " ").strip()
    return " ".join(cleaned.split())[:80]


def render(notes: List[dict], meta: dict) -> Tuple[str, dict]:
    if not notes:
        raise ValueError("Nessuna nota rilevata: impossibile generare il tab")

    bpm = _fit_bpm(notes)
    step = 60.0 / bpm / 4.0  # durata di un sedicesimo in secondi

    # quantizza: (start16, end16, midi)
    events: List[Tuple[int, int, int]] = []
    for n in notes:
        s = int(round(n["start"] / step))
        e = max(int(round(n["end"] / step)), s + 1)
        if e - s > 32:  # note tenute oltre 2 battute: tronca
            e = s + 32
        events.append((s, e, n["midi"]))
    events.sort(key=lambda ev: (ev[0], ev[1]))

    # assegna corda/tasto, raggruppando le simultanee in accordi
    grouped: Dict[int, List[Tuple[int, int]]] = {}
    end_map: Dict[int, int] = {}
    c_fret, c_str = 5.0, 3.0
    for s, _e, midi in events:
        used = {p[0] for p in grouped.get(s, [])}
        pick = _choose(midi, used, c_fret, c_str)
        if pick is None:
            continue
        grouped.setdefault(s, []).append(pick)
        end_map[s] = max(end_map.get(s, _e), _e)
        c_fret = 0.7 * c_fret + 0.3 * pick[1]
        c_str = 0.7 * c_str + 0.3 * pick[0]
    if not grouped:
        raise ValueError("Nessuna nota in range chitarra (E2..E6 +22 tasti)")
    chord_list = sorted(grouped.items())

    # emissione token alphaTex
    out: List[str] = []
    pos = 0
    cur_dur = 0

    def emit_chunk(p: int, size: int, token: str) -> None:
        nonlocal pos, cur_dur
        if p % STEPS_PER_BAR == 0 and p > 0 and out and out[-1] != "|":
            out.append("|")
        if DUR_TOKEN[size] != cur_dur:
            out.append(DUR_TOKEN[size])
            cur_dur = size
        out.append(token)
        pos = p + size

    for i, (s, picks) in enumerate(chord_list):
        if s < pos:  # sovrapposizione residua: salta
            continue
        if s > pos:  # pause fino al prossimo attacco
            gap = s - pos
            while gap > 0:
                k = max(k for k in DUR_TOKEN if k <= gap)
                emit_chunk(pos, k, "r")
                gap -= k
        picks = sorted(picks)
        if len(picks) > 1:
            token = "(" + " ".join(f"{f}.{st}" for st, f in picks) + ")"
        else:
            token = f"{picks[0][1]}.{picks[0][0]}"
        next_s = chord_list[i + 1][0] if i + 1 < len(chord_list) else end_map[s]
        u = min(end_map[s], next_s) - s
        u = max(u, 1)
        while u > 0:
            k = max(k for k in DUR_TOKEN if k <= u)
            emit_chunk(pos, k, token)
            u -= k

    # completa l'ultima battuta di pause
    while pos % STEPS_PER_BAR != 0:
        rem = STEPS_PER_BAR - (pos % STEPS_PER_BAR)
        k = max(k for k in DUR_TOKEN if k <= rem)
        emit_chunk(pos, k, "r")
    if out and out[-1] != "|":
        out.append("|")

    # righe da ~12 token per leggibilità
    lines, line = [], []
    for t in out:
        line.append(t)
        if len(line) >= 12:
            lines.append(" ".join(line))
            line = []
    if line:
        lines.append(" ".join(line))

    title = _escape(meta.get("title") or "Tab")
    artist = _escape(meta.get("artist") or meta.get("uploader") or "")
    head = [f'\\title "{title}"']
    if artist:
        head.append(f'\\artist "{artist}"')
    head.append(f"\\tempo {bpm}")
    head.append("\\instrument electricguitarclean")
    head.append('\\track "Chitarra"')
    head.append(".")
    head.append("")

    tex = "\n".join(head) + "\n".join(lines) + "\n"
    info = {
        "tempo": bpm,
        "bars": out.count("|"),
        "chords": len(chord_list),
        "notes": sum(len(p) for _, p in chord_list),
    }
    return tex, info, (chord_list, end_map)
