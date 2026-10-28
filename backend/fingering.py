"""Guitar fingering assignment (MIDI notes -> string/fret positions).

Implements a dynamic-programming search over "columns" (notes sharing the
same onset) choosing the string/fret assignment that minimizes hand movement,
spreading and awkward positions.

String numbering used everywhere in this module follows the alphaTex
convention: string 1 = lowest string (low E), string N = highest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Standard guitar tuning, low -> high (MIDI pitch of the open string)
STANDARD_TUNING = [40, 45, 50, 55, 59, 64]  # E2 A2 D3 G3 B3 E4
BASS_TUNING = [28, 33, 38, 43]              # E1 A1 D2 G2

MAX_FRET = 20
MAX_SPREAD = 4          # playable span between lowest and highest fretted note
ONSET_TOL = 0.04        # seconds; notes starting closer than this form a chord
MAX_CANDIDATES = 3      # per note
MAX_COMBOS = 256        # per column (pruned by local cost)


@dataclass
class TabNote:
    string: int   # 1..N (1 = lowest)
    fret: int     # 0..MAX_FRET
    pitch: int


@dataclass
class Column:
    start: float
    notes: list[TabNote] = field(default_factory=list)

    def hand_pos(self) -> int:
        fretted = [n.fret for n in self.notes if n.fret > 0]
        return min(fretted) if fretted else 0

    def cost(self) -> float:
        c = 0.0
        fretted = [n.fret for n in self.notes if n.fret > 0]
        if fretted:
            spread = max(fretted) - min(fretted)
            if spread > MAX_SPREAD:
                c += 2.0 * (spread - MAX_SPREAD)
            hp = min(fretted)
            c += 0.04 * hp
            if hp > 12:
                c += 0.6 + 0.04 * (hp - 12)
        for n in self.notes:
            if n.fret == 0:
                c -= 0.05
            if n.string == 1 and n.fret > 9:
                c += 0.3
            c += 0.01 * n.fret
        return c

    def transition_cost(self, prev: "Column") -> float:
        if prev is None or not prev.notes:
            return 0.0
        c = abs(self.hand_pos() - prev.hand_pos()) * 1.2
        prev_by_pitch: dict[int, int] = {}
        for n in prev.notes:
            prev_by_pitch[n.pitch] = n.string
        for n in self.notes:
            ps = prev_by_pitch.get(n.pitch)
            if ps is not None and ps != n.string:
                c += 0.8
            elif ps is None:
                dists = [abs(n.string - p) for p in prev_by_pitch.values()]
                if dists:
                    c += 0.06 * min(dists)
        return c


def _candidates_for_pitch(pitch: int, tuning: list[int], max_fret: int) -> list[tuple[int, int]]:
    out = []
    for idx, open_pitch in enumerate(tuning):
        string = idx + 1
        fret = pitch - open_pitch
        if 0 <= fret <= max_fret:
            out.append((string, fret))
    return out


def _group_columns(notes: list[tuple[float, float, int]]) -> list[list[tuple[float, float, int]]]:
    notes = sorted(notes, key=lambda n: (n[0], n[2]))
    groups = []
    i = 0
    while i < len(notes):
        start = notes[i][0]
        group = [notes[i]]
        j = i + 1
        while j < len(notes) and notes[j][0] - start <= ONSET_TOL:
            group.append(notes[j])
            j += 1
        groups.append(group)
        i = j
    return groups


def _score_assignment(pitches: list[int], combo: list[tuple[int, int]]) -> Column | None:
    strings_used = [s for s, _ in combo]
    if len(set(strings_used)) != len(strings_used):
        return None
    col = Column(
        start=0.0,
        notes=[TabNote(string=s, fret=f, pitch=p)
               for (s, f), p in zip(combo, pitches)],
    )
    return col


def _variants_for_group(group: list[tuple[float, float, int]], tuning: list[int],
                        max_fret: int) -> list[Column]:
    pitches = sorted({int(g[2]) for g in group})
    cand_lists: list[list[tuple[int, int]]] = []
    playable: list[int] = []
    for p in pitches:
        cands = _candidates_for_pitch(p, tuning, max_fret)
        if not cands:
            continue  # unplayable pitch: skip note
        cands.sort(key=lambda sf: 0.05 * sf[1] + (0.2 if sf[1] == 0 else 0.0))
        cand_lists.append(cands[:MAX_CANDIDATES])
        playable.append(p)
    pitches = playable
    if not cand_lists:
        return []
    combos: list[list[tuple[int, int]]] = [[]]
    for cl in cand_lists:
        combos = [c + [x] for c in combos for x in cl]
        if len(combos) > MAX_COMBOS:
            combos = combos[:MAX_COMBOS]
            break
    scored: list[tuple[float, Column]] = []
    for combo in combos:
        col = _score_assignment(pitches, combo)
        if col is not None:
            scored.append((col.cost(), col))
    scored.sort(key=lambda t: t[0])
    return [c for _, c in scored[:MAX_CANDIDATES]]


def midi_notes_to_tab(notes: list[tuple[float, float, int]],
                      tuning: list[int] | None = None,
                      max_fret: int = MAX_FRET) -> list[Column]:
    """Assign string/fret to every note via DP over columns.

    notes: list of (start_sec, end_sec, midi_pitch)
    Returns ordered columns; each column carries its start time and the
    chosen (string, fret) notes.
    """
    tuning = tuning or STANDARD_TUNING
    groups = _group_columns(notes)

    variants: list[list[Column]] = []
    for g in groups:
        start = g[0][0]
        vs = _variants_for_group(g, tuning, max_fret)
        for v in vs:
            v.start = start
        variants.append(vs)  # may be empty -> rest column

    # DP over variant pools
    n = len(variants)
    dp: list[list[tuple[float, int]]] = []
    for i, pool in enumerate(variants):
        if not pool:
            rest = Column(start=groups[i][0][0])
            pool = [rest]
            variants[i] = pool
        if i == 0:
            dp.append([(v.cost(), -1) for v in pool])
            continue
        cur: list[tuple[float, int]] = []
        for v in pool:
            base = v.cost()
            best_cost, best_j = float("inf"), 0
            for j, (pc, _pj) in enumerate(dp[i - 1]):
                pcol = variants[i - 1][j]
                tot = pc + base + v.transition_cost(pcol)
                if tot < best_cost:
                    best_cost, best_j = tot, j
            cur.append((best_cost, best_j))
        dp.append(cur)

    # backtrack
    last = dp[-1]
    j = min(range(len(last)), key=lambda x: last[x][0])
    chain = [j]
    for i in range(n - 1, 0, -1):
        j = dp[i][j][1]
        chain.append(j)
    chain.reverse()

    columns = [variants[i][chain[i]] for i in range(n)]
    for i, g in enumerate(groups):
        columns[i].start = g[0][0]
    return columns

