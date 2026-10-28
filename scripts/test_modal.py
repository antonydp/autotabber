"""Test Modal functions end-to-end with the synthetic guitar wav."""
import base64
import sys
import time

import modal

sys.path.insert(0, "/home/z/my-project/autotabber")

wav_path = "/tmp/at_test/synth_guitar.wav"
with open(wav_path, "rb") as f:
    wav = f.read()

print(f"input wav: {len(wav)/1024/1024:.1f} MB")

sep = modal.Function.from_name("autotabber", "separate_guitar")
tr = modal.Function.from_name("autotabber", "transcribe_guitar")

t0 = time.time()
print("separating (GPU)...")
stem = sep.remote(wav)
print(f"stem: {len(stem)/1024/1024:.1f} MB in {time.time()-t0:.1f}s")
with open("/tmp/at_test/stem.wav", "wb") as f:
    f.write(stem)

t0 = time.time()
print("transcribing (CPU)...")
result = tr.remote(stem)
print(f"tempo={result['tempo']} duration={result['duration']:.1f}s in {time.time()-t0:.1f}s")
midi = base64.b64decode(result["midi"])
with open("/tmp/at_test/out.mid", "wb") as f:
    f.write(midi)
print(f"midi: {len(midi)} bytes -> /tmp/at_test/out.mid")

# quick note stats
import pretty_midi
import io
pm = pretty_midi.PrettyMIDI(io.BytesIO(midi))
notes = [n for i in pm.instruments for n in i.notes]
print(f"notes: {len(notes)}")
pitches = sorted({n.pitch for n in notes})
print("pitch range:", min(pitches) if pitches else "-", "-", max(pitches) if pitches else "-")
print("first notes:", [(round(n.start, 2), n.pitch) for n in notes[:10]])
