"""
AutoTabber - Modal remote functions
===================================

Heavy AI stages run here (on Modal infrastructure):

  1. separate_guitar  (GPU) : Mel-Band RoFormer (2025 SOTA checkpoint for guitar,
     `becruily/mel-band-roformer-guitar`) run through ZFTurbo's
     Music-Source-Separation-Training (MSST) framework. Extracts the guitar stem.
  2. transcribe_guitar (CPU): Spotify Basic Pitch (ONNX runtime) converts the
     isolated guitar stem into a MIDI file. Also estimates tempo.

Deploy once with:
    modal deploy modal_app.py

The Docker web app calls these functions programmatically using
MODAL_TOKEN_ID / MODAL_TOKEN_SECRET environment variables.
"""

import base64
import glob
import os
import subprocess
import tempfile

import modal

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GPU_TYPE = os.environ.get("AUTOTABBER_GPU", "A10G")
MSST_REPO = "https://github.com/ZFTurbo/Music-Source-Separation-Training.git"
GUITAR_CKPT_URL = (
    "https://huggingface.co/becruily/mel-band-roformer-guitar/resolve/main/"
    "becruily_guitar.ckpt"
)
GUITAR_CONFIG_URL = (
    "https://huggingface.co/becruily/mel-band-roformer-guitar/resolve/main/config.yaml"
)

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

msst_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "torch",
        "torchaudio",
        "librosa",
        "soundfile",
        "tqdm",
        "ml_collections",
        "omegaconf",
        "einops==0.8.1",
        "rotary_embedding_torch==0.3.5",
        "matplotlib",
        "pyyaml",
        "scipy",
        "beartype",
    )
    .run_commands(
        "python -c \"import urllib.request; urllib.request.urlretrieve("
        "'https://github.com/ZFTurbo/Music-Source-Separation-Training/archive/refs/heads/main.tar.gz',"
        "'/tmp/msst.tar.gz')\" && mkdir -p /msst && "
        "tar -xzf /tmp/msst.tar.gz -C /msst --strip-components=1 && rm /tmp/msst.tar.gz",
        "mkdir -p /models && python -c \"import urllib.request; urllib.request.urlretrieve("
        f"'{GUITAR_CKPT_URL}', '/models/guitar.ckpt')\"",
        "python -c \"import urllib.request; urllib.request.urlretrieve("
        f"'{GUITAR_CONFIG_URL}', '/models/config.yaml')\"",
    )
)

transcribe_image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("ffmpeg")
    .pip_install(
        "basic-pitch[onnx]==0.4.0",
        "librosa==0.10.2.post1",
        "pretty_midi",
    )
    # Warm-up: fail fast at build time if onnxruntime is broken (weights are
    # bundled inside the wheel); force the ONNX variant (tflite-runtime is
    # ABI-incompatible with modern numpy)
    .run_commands(
        'python -c "from basic_pitch import build_icassp_2022_model_path, FilenameSuffix; '
        'from basic_pitch.inference import Model; '
        'Model(build_icassp_2022_model_path(FilenameSuffix.onnx))"'
    )
)

# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

app = modal.App("autotabber")


@app.function(
    image=msst_image,
    gpu=GPU_TYPE,
    timeout=1200,
    scaledown_window=60,
    max_containers=2,
)
def separate_guitar(wav: bytes) -> bytes:
    """Isolate the guitar stem from a full mix using Mel-Band RoFormer.

    Input:  44.1 kHz stereo WAV bytes (full song mix).
    Output: WAV bytes containing the isolated guitar stem (PCM_16).
    """
    with tempfile.TemporaryDirectory() as tmp:
        in_dir = os.path.join(tmp, "in")
        out_dir = os.path.join(tmp, "out")
        os.makedirs(in_dir)
        with open(os.path.join(in_dir, "song.wav"), "wb") as f:
            f.write(wav)

        cmd = [
            "python", "/msst/inference.py",
            "--model_type", "mel_band_roformer",
            "--config_path", "/models/config.yaml",
            "--start_check_point", "/models/guitar.ckpt",
            "--input_folder", in_dir,
            "--store_dir", out_dir,
            "--disable_detailed_pbar",
            "--pcm_type", "PCM_16",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1100)
        if proc.returncode != 0:
            raise RuntimeError(
                f"MSST inference failed (rc={proc.returncode}):\n"
                f"--- stderr ---\n{proc.stderr[-4000:]}\n"
                f"--- stdout ---\n{proc.stdout[-2000:]}"
            )

        # The output layout is {store_dir}/{file_name}/{instrument}.{ext}
        candidates = sorted(glob.glob(os.path.join(out_dir, "**", "*"), recursive=True))
        files = [c for c in candidates if os.path.isfile(c)]
        if not files:
            raise RuntimeError(
                "MSST produced no output. stdout tail:\n" + proc.stdout[-2000:]
            )
        audio_files = [f for f in files if f.lower().endswith((".wav", ".flac", ".mp3"))]
        if not audio_files:
            raise RuntimeError(
                "MSST output has no audio file. Files: "
                + ", ".join(os.path.basename(f) for f in files)
            )
        # Prefer the Guitar stem if multiple files exist
        best = None
        for c in audio_files:
            if "guitar" in os.path.basename(c).lower():
                best = c
                break
        best = best or audio_files[0]

        # Normalize to 16-bit PCM wav bytes regardless of container format
        import soundfile as sf
        import io
        data, sr = sf.read(best, dtype="float32", always_2d=True)
        buf = io.BytesIO()
        sf.write(buf, data, sr, subtype="PCM_16", format="WAV")
        return buf.getvalue()


@app.function(
    image=transcribe_image,
    cpu=4,
    memory=8192,
    timeout=1800,
    scaledown_window=60,
    max_containers=4,
)
def transcribe_guitar(wav: bytes) -> dict:
    """Transcribe an isolated guitar stem into MIDI with Basic Pitch (ONNX).

    Input:  WAV bytes of the guitar stem.
    Output: {"midi": <base64 MIDI>, "tempo": <bpm float>, "duration": <seconds>}
    """
    import librosa
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "stem.wav")
        with open(wav_path, "wb") as f:
            f.write(wav)

        # --- tempo estimation (on the stem) ---
        try:
            y, sr = librosa.load(wav_path, sr=22050, mono=True, duration=900)
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            tempo_arr = librosa.feature.rhythm.tempo(
                onset_envelope=onset_env, sr=sr, aggregate=None
            )
            tempo = float(np.median(tempo_arr)) if tempo_arr is not None else 120.0
        except Exception:
            tempo = 120.0
        if not (40.0 <= tempo <= 220.0):
            # double/halve outliers into a plausible range
            while tempo < 40.0:
                tempo *= 2.0
            while tempo > 220.0:
                tempo /= 2.0
        tempo = round(tempo, 2)

        duration = float(librosa.get_duration(path=wav_path))

        # --- transcription (ONNX variant explicitly) ---
        from basic_pitch import build_icassp_2022_model_path, FilenameSuffix
        from basic_pitch.inference import Model, predict

        model = Model(build_icassp_2022_model_path(FilenameSuffix.onnx))
        # Higher thresholds + minimum note length reduce harmonic "ghost"
        # notes (octave doublings) on isolated guitar stems
        _model_output, midi_data, _note_events = predict(
            wav_path,
            model,
            onset_threshold=0.6,
            frame_threshold=0.4,
            minimum_note_length=100,  # ms
        )

        midi_path = os.path.join(tmp, "out.mid")
        midi_data.write(midi_path)
        with open(midi_path, "rb") as f:
            midi_b = f.read()

    return {
        "midi": base64.b64encode(midi_b).decode("ascii"),
        "tempo": tempo,
        "duration": duration,
    }
