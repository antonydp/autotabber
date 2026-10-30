"""Assegnazione corda/tasto su tastiera (diteggiatura Songsterr-like).

Per ogni accordo (note simultanee sullo stesso step) si sceglie la
combinazione di corde/tasti che minimizza:
  - movimento della mano rispetto alla posizione precedente
  - tasti alti e salti di corda
  - span troppo ampio all'interno dell'accordo
Le note fuori estensione vengono spostate d'ottava, se possibile.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

GUITAR_TUNING = [64, 59, 55, 50, 45, 40]  # corda 1..6 (1 = Mi cantino)
BASS_TUNING = [43, 38, 33, 28]            # corda 1..4 (1 = Sol cantino)
MAX_FRET = 22


def fold_octave(midi: int, lo: int, hi: int) -> int:
    """Riporta la nota nell'intervallo [lo, hi] spostandola d'ottava."""
    while midi < lo:
        midi += 12
    while midi > hi:
        midi -= 12
    return midi


def _candidates(midi: int, tuning: List[int], max_fret: int,
                used: set, hand_f: float, hand_s: float) -> List[Tuple[float, int, int]]:
    """Candidati (costo, corda, tasto) per una nota, ordinati per costo."""
    cands: List[Tuple[float, int, int]] = []
    for si, open_midi in enumerate(tuning, start=1):
        if si in used:
            continue
        fret = midi - open_midi
        if fret < 0 or fret > max_fret:
            continue
        cost = (
            fret
            + 2.5 * max(0, fret - 15)          # tasti molto alti
            + 0.45 * abs(fret - hand_f)        # continuita' posizione mano
            + 0.35 * abs(si - hand_s)          # continuita' corde
            - (1.2 if fret == 0 else 0.0)      # leggero bonus corde a vuoto
        )
        cands.append((cost, si, fret))
    cands.sort()
    return cands


def _pick_chord(midis: List[int], tuning: List[int], max_fret: int,
                hand_f: float, hand_s: float) -> Optional[List[Tuple[int, int]]]:
    """Migliore combinazione (corda, tasto) per un gruppo di note simultanee."""
    per_note: List[List[Tuple[float, int, int]]] = []
    # riduci esplosione combinatoria: max 4 candidati per nota
    # (la vincola "corde distinte" la applica la ricorsione)
    for m in midis:
        c = _candidates(m, tuning, max_fret, set(), hand_f, hand_s)
        if not c:
            return None
        per_note.append(c[:4])

    best: Optional[List[Tuple[int, int]]] = None
    best_score = float("inf")

    def rec(i: int, chosen: List[Tuple[float, int, int]], used_now: set) -> None:
        nonlocal best, best_score
        if i == len(per_note):
            frets = [f for _, _, f in chosen]
            fretted = [f for f in frets if f > 0]
            span = (max(fretted) - min(fretted)) if len(fretted) > 1 else 0
            total = sum(c for c, _, _ in chosen) + 3.0 * max(0, span - 4)
            if total < best_score:
                best_score = total
                best = [(s, f) for _, s, f in chosen]
            return
        for cand in per_note[i]:
            if cand[1] in used_now:
                continue
            rec(i + 1, chosen + [cand], used_now | {cand[1]})

    rec(0, [], set())
    return best


def assign(events: List[Tuple[int, int, int, int]], tuning: List[int],
           max_fret: int = MAX_FRET, range_lo: Optional[int] = None,
           range_hi: Optional[int] = None) -> Tuple[List[Tuple[int, List[Tuple[int, int]]]], Dict[int, int]]:
    """Eventi [(step, dur, midi, vel)] -> accordi [(step, [(corda, tasto), ...])].

    Ritorna anche end_map {step: end_step} per la durata delle note.
    Gli eventi devono essere ordinati per step.
    """
    if range_lo is None:
        range_lo = min(tuning)
    if range_hi is None:
        range_hi = max(tuning) + max_fret

    grouped: Dict[int, List[Tuple[int, int]]] = {}
    end_map: Dict[int, int] = {}
    hand_f, hand_s = 5.0, float(len(tuning) / 2)

    by_step: Dict[int, List[Tuple[int, int, int, int]]] = {}
    for step, dur, midi, vel in events:
        by_step.setdefault(step, []).append((step, dur, midi, vel))

    for step in sorted(by_step):
        group = by_step[step]
        dur = max(d for _, d, _, _ in group)
        end_map[step] = max(end_map.get(step, 0), step + dur)

        # tolleranza polifonia: troppi eventi -> tieni i piu' forti
        if len(group) > len(tuning):
            group = sorted(group, key=lambda e: (-e[3], -e[1]))[: len(tuning)]

        midis: List[int] = []
        for _, _, midi, _vel in group:
            m = fold_octave(midi, range_lo, range_hi)
            midis.append(m)

        picks = _pick_chord(midis, tuning, max_fret, hand_f, hand_s)
        if not picks:
            continue
        grouped[step] = picks
        fretted = [f for _, f in picks if f > 0]
        if fretted:
            hand_f = 0.72 * hand_f + 0.28 * (sum(fretted) / len(fretted))
        hand_s = 0.72 * hand_s + 0.28 * (sum(s for s, _ in picks) / len(picks))

    chord_list = sorted(grouped.items())
    return chord_list, end_map
