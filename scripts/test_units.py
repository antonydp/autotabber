"""Local unit test: synthetic notes -> fingering -> alphaTex -> gp5."""
import sys
sys.path.insert(0, "/home/z/my-project/autotabber")

from backend.fingering import midi_notes_to_tab
from backend.alphatex import build_alphatex
from backend.tabwriter import build_gp5

# Simple riff: E minor pentatonic-ish, 8 columns, 0.25s apart at 120bpm
tempo = 120.0
beat = 60 / tempo
step = beat / 2  # eighth notes
pitches = [40, 43, 45, 47, 50, 47, 45, 43]  # E2 G2 A2 B3 D3 B3 A2 G2
notes = []
for i, p in enumerate(pitches):
    notes.append((i * step, i * step + step * 0.9, p))

# add a chord at the end (E minor: E3 G3 B3 E4)
t_end = 8 * step
notes += [
    (t_end, t_end + beat * 2, 52),  # E3
    (t_end, t_end + beat * 2, 55),  # G3
    (t_end, t_end + beat * 2, 59),  # B3
    (t_end, t_end + beat * 2, 64),  # E4
]

cols = midi_notes_to_tab(notes)
print(f"columns: {len(cols)}")
for c in cols[:12]:
    print(f"  t={c.start:.3f} -> " + ", ".join(f"s{n.string}f{n.fret}" for n in c.notes))

tex = build_alphatex(cols, tempo=tempo, title="Unit test riff")
print("\n--- alphaTex ---")
print(tex)

# sanity checks
assert "\\tempo 120" in tex
assert ". " in tex or "\n." in tex
assert "|" in tex, "bar lines missing"

gp5 = build_gp5(cols, tempo=tempo, title="Unit test riff")
print("gp5 bytes:", len(gp5) if gp5 else None)
if gp5:
    import io
    import guitarpro
    song = guitarpro.parse(io.BytesIO(gp5))
    print("gp5 roundtrip OK:", song.title, "| bars:", len(song.measureHeaders),
          "| notes in first bars:", sum(len(v.beats) for m in song.tracks[0].measures[:2] for v in m.voices))
