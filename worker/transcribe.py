"""Audio -> MIDI.

Percorso GPU (opzionale): UNA chiamata Modal `separate_and_transcribe`
esegue Demucs (6 stems) + Basic Pitch per ogni stem melodico su T4 e
restituisce MIDI + stems compattati; attiva con MODAL_ENABLED=1 + chiavi
env. Il fallback su eventuali errori e' totale (tutto in locale).

Percorso locale: Demucs CPU (subprocess) + Basic Pitch (Spotify) per stem.
"""

import logging
import os
from pathlib import Path

# basic-pitch 0.4 salva il modello in Keras 2: con TensorFlow >= 2.16
# serve tf-keras in modalita' legacy (con TF 2.15 la variabile e' ignorata)
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")

log = logging.getLogger("autotabber.transcribe")


def modal_enabled() -> bool:
    return (
        os.getenv("MODAL_ENABLED", "0") == "1"
        and bool(os.getenv("MODAL_TOKEN_ID"))
        and bool(os.getenv("MODAL_TOKEN_SECRET"))
    )


def modal_full_separate(audio_path: Path, d: Path, model: str):
    """Tutto il lavoro pesante su Modal (GPU): Demucs 6 stems + Basic Pitch.

    Scrive in d: stems/*.wav (mono 22k) e {stem}.mid per gli stem melodici.
    Ritorna {stem: Path} in caso di successo, None se Modal non e'
    attivo o se la chiamata fallisce (il chiamante fara' fallback locale).
    """
    if not modal_enabled():
        return None
    try:
        import modal

        app_name = os.getenv("MODAL_APP", "autotabber")
        fn_name = os.getenv("MODAL_FN", "separate_and_transcribe")
        log.info("Modal: separazione + trascrizione remote (%s/%s)",
                 app_name, fn_name)
        fn = modal.Function.from_name(app_name, fn_name)
        res = fn.remote(Path(audio_path).read_bytes(), model)
    except Exception as exc:  # noqa: BLE001
        log.warning("Modal fallito (%s); fallback locale completo", exc)
        return None

    try:
        stems_dir = d / "stems"
        stems_dir.mkdir(exist_ok=True)
        stems: dict[str, Path] = {}
        for name, blob in res["stems"].items():
            p = stems_dir / f"{name}.wav"
            p.write_bytes(blob)
            stems[name] = p
        for name, blob in res["midi"].items():
            (d / f"{name}.mid").write_bytes(blob)
        log.info("Modal OK: %d stems, %d midi",
                 len(stems), len(res.get("midi", {})))
        return stems
    except Exception as exc:  # noqa: BLE001
        log.warning("Risultato Modal invalido (%s); fallback locale", exc)
        return None


def transcribe_file(audio_path: Path, midi_out: Path) -> None:
    """Basic Pitch locale (CPU) — fallback o singolo stem."""
    from basic_pitch.inference import predict

    log.info("Basic Pitch: %s", audio_path)
    _, midi_data, _ = predict(str(audio_path))
    midi_data.write(str(midi_out))
