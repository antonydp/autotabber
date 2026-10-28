"""Generate a synthetic 'plucked guitar' test WAV (Karplus-Strong synthesis).

Riff: A minor pentatonic, ~15 seconds, 44.1kHz stereo.
"""
import numpy as np
import wave

SR = 44100

def pluck(freq: float, dur: float, decay: float = 0.996) -> np.ndarray:
    n = int(SR * dur)
    period = int(SR / freq)
    buf = np.random.uniform(-1, 1, period)
    out = np.empty(n)
    for i in range(n):
        out[i] = buf[i % period]
        buf[i % period] = decay * 0.5 * (buf[i % period] + buf[(i + 1) % period])
    # fade out
    env = np.linspace(1, 0, n) ** 1.5
    return out * env

# A2=110, C3=130.81, D3=146.83, E3=164.81, G3=196, A3=220, C4=261.63, E4=329.63
riff = [
    (110.00, 0.40), (164.81, 0.20), (196.00, 0.20), (220.00, 0.40),
    (196.00, 0.20), (164.81, 0.20), (146.83, 0.40), (164.81, 0.20),
    (220.00, 0.40), (261.63, 0.20), (220.00, 0.20), (196.00, 0.40),
    (164.81, 0.20), (146.83, 0.20), (130.81, 0.40), (110.00, 0.60),
    # final chord A (A2 E3 A3 C4 E4)
    (110.00, 1.60), (164.81, 1.60), (220.00, 1.60), (261.63, 1.60), (329.63, 1.60),
]

total = sum(d for _, d in riff) + 0.5
mix = np.zeros(int(SR * total))
t_cursor = 0.0
for freq, dur in riff:
    sig = pluck(freq, dur + 0.35)
    idx = int(t_cursor * SR)
    end = min(len(mix), idx + len(sig))
    mix[idx:end] += sig[: end - idx]
    t_cursor += dur

mix = mix / max(1e-9, np.max(np.abs(mix))) * 0.85
stereo = np.stack([mix, mix], axis=1)
pcm = (stereo * 32767).astype(np.int16)

with wave.open("/tmp/at_test/synth_guitar.wav", "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
print("written /tmp/at_test/synth_guitar.wav", f"{total:.1f}s")
