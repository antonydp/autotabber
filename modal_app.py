"""Autotabber su Modal — accelerazione GPU opzionale.

UNA SOLA chiamata remota esegue tutto il lavoro pesante:
  1. Demucs (htdemucs_6s, 6 stems) su GPU T4 — il passo che in CPU
     locale richiede decine di minuti;
  2. Basic Pitch su ogni stem melodico -> MIDI (voce, chitarra,
     basso, piano, altro).

Al worker arrivano:
  - i MIDI (piccoli, pronti per la costruzione delle tracce);
  - gli stem ricompressi in mono/22 kHz (bastano per il rilevamento
    dell'energia e per la trascrizione della batteria, che restano
    in locale perche' sono leggere).

Deploy (una tantum, dal PC o da qualsiasi macchina):
    pip install modal
    modal setup                     # oppure export MODAL_TOKEN_ID/SECRET
    modal deploy modal_app.py

Test manuale:
    modal run modal_app.py --path /percorso/brano.wav --out-dir /tmp/out
"""

import modal

app = modal.App("autotabber")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "torch",
        "torchaudio",
        "demucs",
        "basic-pitch",
        "numpy<2",
        "mido",
        # setuptools>=84 ha rimosso pkg_resources, richiesto da resampy/basic-pitch
        "setuptools<81",
    )
)

cache = modal.Volume.from_name("autotabber-cache", create_if_missing=True)

MELODIC = ("vocals", "guitar", "bass", "piano", "other")
ALL_STEMS = MELODIC + ("drums",)


@app.function(
    image=image,
    gpu="T4",
    timeout=1800,
    memory=8192,
    volumes={"/cache": cache},
    env={"TORCH_HOME": "/cache/torch", "XDG_CACHE_HOME": "/cache"},
)
def separate_and_transcribe(audio: bytes, model: str = "htdemucs_6s") -> dict:
    """Demucs GPU + Basic Pitch: tutto il lavoro pesante in una chiamata.

    Input:  audio wav/mp3/flac... (bytes)
    Output: {"stems": {nome: wav mono 22kHz}, "midi": {nome: midi bytes}}
            (stems = tutti e 6, midi = solo quelli melodici)
    """
    import io
    import pathlib
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        src = td / "in.wav"
        src.write_bytes(audio)

        # 1) Demucs su GPU: separazione completa a 6 stems
        subprocess.run(
            [
                "python", "-m", "demucs.separate",
                "-n", model,
                "--device", "cuda",
                "-o", str(td / "sep"),
                str(src),
            ],
            check=True,
            capture_output=True,
        )
        wavs = {
            p.stem.lower(): p
            for p in (td / "sep").rglob("*.wav")
            if p.stem.lower() in ALL_STEMS
        }
        missing = [s for s in ALL_STEMS if s not in wavs]
        if missing:
            raise RuntimeError(f"Demucs: stems mancanti: {missing}")

        # 2) stems compattati (mono 22 kHz): servono solo al worker per
        #    rilevamento energia e trascrizione batteria, non per l'ascolto
        stems: dict[str, bytes] = {}
        for name, wav in wavs.items():
            small = td / f"{name}.small.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(wav), "-ac", "1", "-ar", "22050", str(small)],
                check=True, capture_output=True,
            )
            stems[name] = small.read_bytes()

        # 3) Basic Pitch per ogni stem melodico (CPU del container GPU)
        midi: dict[str, bytes] = {}
        from basic_pitch.inference import predict

        for name in MELODIC:
            _, midi_data, _ = predict(str(wavs[name]))
            buf = io.BytesIO()
            midi_data.write(buf)
            midi[name] = buf.getvalue()

        return {"stems": stems, "midi": midi}


@app.local_entrypoint()
def main(path: str = "", out_dir: str = ""):
    """modal run modal_app.py --path brano.wav [--out-dir /tmp/out]"""
    import pathlib

    audio = pathlib.Path(path).read_bytes()
    res = separate_and_transcribe.remote(audio)
    dest = pathlib.Path(out_dir) if out_dir else pathlib.Path(path).parent
    dest.mkdir(parents=True, exist_ok=True)
    for name, blob in res["stems"].items():
        (dest / f"{name}.wav").write_bytes(blob)
    for name, blob in res["midi"].items():
        (dest / f"{name}.mid").write_bytes(blob)
    print("stems+midi scritti in", dest)
