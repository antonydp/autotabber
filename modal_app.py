"""Autotabber su Modal — accelerazione GPU opzionale.

Separa lo stem chitarra con Demucs (GPU) e trascrive con Basic Pitch,
restituendo un MIDI. Il worker Docker chiama questa funzione quando
MODAL_ENABLED=1 e le chiavi Modal sono presenti come variabili d'ambiente.

Deploy (una tantum, dal PC/server):
    export MODAL_TOKEN_ID=...      # mai committare questi valori
    export MODAL_TOKEN_SECRET=...
    modal deploy modal_app.py

Test manuale:
    modal run modal_app.py --path /percorso/brano.mp3
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


@app.function(
    image=image,
    gpu="T4",
    timeout=1800,
    memory=8192,
    volumes={"/cache": cache},
    env={"TORCH_HOME": "/cache/torch", "XDG_CACHE_HOME": "/cache"},
)
def transcribe(audio: bytes) -> bytes:
    """audio (wav/mp3/...) -> MIDI bytes (stem chitarra trascritto)."""
    import pathlib
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "in.wav"
        src.write_bytes(audio)

        subprocess.run(
            [
                "python", "-m", "demucs.separate",
                "-n", "htdemucs_6s",
                "--two-stems=guitar",
                "--device", "cuda",
                "-o", str(pathlib.Path(td) / "sep"),
                str(src),
            ],
            check=True,
            capture_output=True,
        )

        hits = list(pathlib.Path(td).rglob("guitar.wav"))
        if not hits:
            raise RuntimeError("Demucs: stem chitarra non trovato")

        from basic_pitch.inference import predict

        _, midi_data, _ = predict(str(hits[0]))
        out = pathlib.Path(td) / "out.mid"
        midi_data.write(str(out))
        return out.read_bytes()


@app.local_entrypoint()
def main(path: str = ""):
    """modal run modal_app.py --path brano.mp3 -> scrive brano.mid accanto al file."""
    if not path:
        print("usa: modal run modal_app.py --path <file audio>")
        return
    out = transcribe.remote(open(path, "rb").read())
    dest = path.rsplit(".", 1)[0] + ".mid"
    with open(dest, "wb") as fh:
        fh.write(out)
    print("scritto", dest)
