"""Audio -> MIDI.

Percorso GPU (opzionale): funzione Modal remota `transcribe`
(Demucs + Basic Pitch su T4) attiva con MODAL_ENABLED=1 + chiavi env.

Percorso locale: Basic Pitch (Spotify) su CPU — leggero e affidabile.
"""

import logging
import os
from pathlib import Path

log = logging.getLogger("autotabber.transcribe")


def modal_enabled() -> bool:
    return (
        os.getenv("MODAL_ENABLED", "0") == "1"
        and bool(os.getenv("MODAL_TOKEN_ID"))
        and bool(os.getenv("MODAL_TOKEN_SECRET"))
    )


def transcribe_file(audio_path: Path, midi_out: Path) -> None:
    if modal_enabled():
        try:
            midi_out.write_bytes(transcribe_modal(audio_path))
            log.info("trascrizione eseguita su Modal (GPU)")
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("Modal fallito (%s); fallback Basic Pitch locale", exc)
    transcribe_basic_pitch(audio_path, midi_out)


def transcribe_modal(audio_path: Path) -> bytes:
    import modal

    app_name = os.getenv("MODAL_APP", "autotabber")
    fn_name = os.getenv("MODAL_FN", "transcribe")
    fn = modal.Function.from_name(app_name, fn_name)
    return fn.remote(Path(audio_path).read_bytes())


def transcribe_basic_pitch(audio_path: Path, midi_out: Path) -> None:
    from basic_pitch.inference import predict

    log.info("Basic Pitch: %s", audio_path)
    _, midi_data, _ = predict(str(audio_path))
    midi_data.write(str(midi_out))
