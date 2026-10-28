"""Environment configuration. NO secrets are ever hard-coded."""

import os
from pathlib import Path

# Modal credentials come exclusively from the environment (Docker .env file)
MODAL_TOKEN_ID = os.environ.get("MODAL_TOKEN_ID", "")
MODAL_TOKEN_SECRET = os.environ.get("MODAL_TOKEN_SECRET", "")

DATA_DIR = Path(os.environ.get("AUTOTABBER_DATA_DIR", "/data"))
MAX_DURATION = int(os.environ.get("AUTOTABBER_MAX_DURATION", "600"))  # seconds
HOST_PORT = int(os.environ.get("AUTOTABBER_PORT", "8000"))

# Auto-deploy the Modal app if it is not deployed yet (first run)
AUTO_DEPLOY_MODAL = os.environ.get("AUTOTABBER_AUTO_DEPLOY", "1") != "0"

# Instrument presets: alphaTex tuning (HIGH->LOW), GM program, fingering tuning
INSTRUMENTS: dict[str, dict] = {
    "electric": {
        "label": "Chitarra elettrica",
        "tuning": [40, 45, 50, 55, 59, 64],
        "tuning_names": ["E4", "B3", "G3", "D3", "A2", "E2"],
        "program": 30,   # GM distortion guitar
        "max_fret": 20,
    },
    "acoustic": {
        "label": "Chitarra acustica",
        "tuning": [40, 45, 50, 55, 59, 64],
        "tuning_names": ["E4", "B3", "G3", "D3", "A2", "E2"],
        "program": 25,   # GM steel acoustic
        "max_fret": 19,
    },
    "bass": {
        "label": "Basso",
        "tuning": [28, 33, 38, 43],
        "tuning_names": ["G2", "D2", "A1", "E1"],
        "program": 33,   # GM finger bass
        "max_fret": 20,
    },
}
