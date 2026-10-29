"""Download dell'audio SENZA parlare con YouTube.

Il server non contatta mai Google per scaricare: la risoluzione del link
avviene tramite istanze **Cobalt** comunitarie (https://cobalt.tools,
open source). L'istanza scarica il video con i propri IP (residenziali o
proxy dedicati) e ci restituisce un URL tunnel/redirect da cui prendere
il file audio già pronto.

Ordine di ricerca delle istanze:
  1. env COBALT_API_URLS  (lista esplicita, separata da virgole — es.
     la tua istanza self-hosted)
  2. liste pubbliche dinamiche (instances.cobalt.best / instances.hyper.lol),
     filtrate: supporto YouTube attivo, senza autenticazione, ordinate
     per punteggio di affidabilità
  3. piccola lista statica di riserva

Sono accettati anche URL di file audio diretti (.mp3/.wav/.m4a/…): in
quel caso si scarica direttamente senza Cobalt.

Metadati: titolo/artista via oEmbed (endpoint pubblico, non soggetto a
bot-check), durata via ffprobe sul file scaricato.
"""

import logging
import os
import re
import subprocess
import time
from pathlib import Path

import requests

log = logging.getLogger("autotabber.fetcher")

UA = {"User-Agent": "autotabber/1.0 (+https://github.com/antonydp/autotabber)"}

# --- configurazione (env) -------------------------------------------------
COBALT_API_URLS = [
    u.strip().rstrip("/")
    for u in os.getenv("COBALT_API_URLS", "").split(",")
    if u.strip()
]
COBALT_MAX_TRIES = int(os.getenv("COBALT_MAX_TRIES", "6"))
COBALT_LIST_TTL = int(os.getenv("COBALT_LIST_TTL_SEC", "3600"))
DOWNLOAD_MAX_MB = int(os.getenv("DOWNLOAD_MAX_MB", "300"))

LIST_URLS = [
    "https://instances.cobalt.best/api/instances.json",
    "https://instances.hyper.lol/instances.json",
]

# riserva: piccola lista statica (verificata manualmente, può cambiare)
STATIC_INSTANCES = [
    "https://co.otomir23.me",
    "https://cobalt-api.kwiatekmiki.com",
    "https://capi.oak.li",
]

# URL che punta direttamente a un file media -> niente Cobalt
_DIRECT_RE = re.compile(
    r"\.(mp3|wav|m4a|m4b|flac|ogg|oga|opus|aac|wma|webm|mp4)([?#].*)?$", re.I
)

# oEmbed pubblico: funziona anche da IP datacenter (non è bot-gated)
_OEMBED_URLS = [
    "https://www.youtube.com/oembed",
    "https://noembed.com/embed",
]

# cache in-process della lista istanze
_cache: dict = {"at": 0.0, "apis": []}


# --------------------------------------------------------------------------
# lista istanze Cobalt
# --------------------------------------------------------------------------

def _parse_instance_list(data) -> list:
    """Estrae gli URL delle istanze YouTube-capable e senza auth.

    Parser difensivo: i campi delle liste pubbliche cambiano spesso.
    """
    out = []
    if isinstance(data, dict):
        items = data.get("instances") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    for it in items:
        if not isinstance(it, dict):
            continue
        api = it.get("api") or it.get("url") or ""
        api = str(api).strip().rstrip("/")
        if not api.startswith("http"):
            continue

        # youtube supportato?
        svcs = it.get("services")
        yt = None
        if isinstance(svcs, dict):
            yt = svcs.get("youtube")
        elif isinstance(svcs, list):
            yt = "youtube" in svcs
        if yt is False:
            continue
        if yt is None and isinstance(svcs, dict) and svcs:
            continue  # dichiara altri servizi ma non youtube
        if isinstance(yt, dict):
            if yt.get("has_auth") or yt.get("api_online") is False:
                continue

        # scarta le istanze dietro login/turnstile/api-key
        if any(
            it.get(k)
            for k in ("has_auth", "turnstile", "api_key", "auth_required")
        ):
            continue

        try:
            score = float(it.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        out.append((score, api))

    out.sort(key=lambda t: t[0], reverse=True)
    return [api for _, api in out]


def _instance_candidates() -> list:
    """COBALT_API_URLS + lista dinamica (cache TTL) + statici di riserva."""
    candidates: list = list(COBALT_API_URLS)
    seen = set(candidates)

    now = time.time()
    if now - _cache["at"] > COBALT_LIST_TTL:
        apis: list = []
        for lu in LIST_URLS:
            try:
                r = requests.get(lu, timeout=12, headers=UA)
                r.raise_for_status()
                apis.extend(_parse_instance_list(r.json()))
            except Exception as exc:  # noqa: BLE001
                log.warning("lista istanze %s non disponibile: %s", lu, exc)
        if apis:
            _cache["apis"] = apis
            _cache["at"] = now
        # se la fetch fallisce riusiamo l'ultima cache valida
    candidates.extend(a for a in _cache["apis"] if a not in seen)

    for api in STATIC_INSTANCES:
        if api not in seen:
            candidates.append(api)
    return candidates[: max(COBALT_MAX_TRIES, len(COBALT_API_URLS))]


# --------------------------------------------------------------------------
# Cobalt: risoluzione URL -> media URL
# --------------------------------------------------------------------------

def _cobalt_resolve(api: str, url: str) -> dict:
    """Interroga un'istanza Cobalt (API v10) e restituisce la risposta.

    Solleva RuntimeError con un messaggio breve se l'istanza rifiuta.
    """
    body = {
        "url": url,
        "downloadMode": "audio",
        # "best" = formato originale senza transcodifica: più veloce e
        # meno carico per l'istanza; convertiamo noi in wav con ffmpeg
        "audioFormat": "best",
        "filenameStyle": "basic",
    }
    # l'API v10 ESIGE Accept: application/json (sennò: error.api.header.accept)
    headers = {**UA, "Accept": "application/json"}
    r = requests.post(api + "/", json=body, headers=headers, timeout=30)
    j = r.json()
    status = j.get("status")

    if status in ("tunnel", "redirect") and j.get("url"):
        return j
    if status == "picker" and j.get("picker"):
        first = j["picker"][0]
        if first.get("url"):
            return {"url": first["url"], "filename": first.get("text", "")}

    code = ""
    if isinstance(j.get("error"), dict):
        code = j["error"].get("code", "")
    elif isinstance(j.get("error"), str):
        code = j["error"]
    raise RuntimeError(f"{status or r.status_code} {code}".strip())


def _download_stream(url: str, dest: Path) -> None:
    """Scarica in streaming con tetto massimo di dimensione."""
    max_bytes = DOWNLOAD_MAX_MB * 1024 * 1024
    got = 0
    with requests.get(url, stream=True, headers=UA, timeout=(15, 120)) as r:
        ctype = r.headers.get("content-type", "?")
        clen = r.headers.get("content-length", "?")
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} (type={ctype})")
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                got += len(chunk)
                if got > max_bytes:
                    raise RuntimeError(
                        f"file più grande di {DOWNLOAD_MAX_MB} MB "
                        "(aumenta DOWNLOAD_MAX_MB se serve)"
                    )
                fh.write(chunk)
    if got == 0:
        # es. tunnel 200 con corpo vuoto: accade a volte, transitorio
        raise RuntimeError(f"risposta vuota (type={ctype}, length={clen})")


# --------------------------------------------------------------------------
# metadati + conversione
# --------------------------------------------------------------------------

def _oembed(url: str) -> dict:
    """Titolo/artista per i link YouTube via oEmbed (con fallback)."""
    if "youtu" not in url:
        return {}
    for ep in _OEMBED_URLS:
        try:
            r = requests.get(
                ep, params={"url": url, "format": "json"},
                headers=UA, timeout=10,
            )
            if not r.ok:
                continue
            j = r.json()
            title = (j.get("title") or "").strip()
            if title:
                return {
                    "title": title,
                    "artist": (j.get("author_name") or "").strip(),
                }
        except Exception:  # noqa: BLE001
            continue
    return {}


def _ffmpeg_to_wav(src: Path, dst: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-vn", "-acodec", "pcm_s16le", str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not dst.exists():
        tail = (proc.stderr or proc.stdout or "")[-400:]
        raise RuntimeError(f"ffmpeg: {tail}")


def _ffprobe_duration(path: Path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# entry point usato dalla pipeline
# --------------------------------------------------------------------------

def download_audio(url: str, d: Path) -> dict:
    """Scarica l'audio di `url` in d/audio.wav e restituisce i metadati.

    Solleva RuntimeError con messaggio chiaro se il download non riesce.
    """
    d.mkdir(parents=True, exist_ok=True)
    src = d / "source.audio"
    filename_hint = ""

    if _DIRECT_RE.search(url):
        # file audio diretto: niente Cobalt
        log.info("download diretto: %s", url)
        _download_stream(url, src)
    else:
        candidates = _instance_candidates()
        errors: list = []
        done = False
        # DUE passate: i tunnel a volte rispondono 200 con corpo vuoto
        # (transitorio, capita anche alle istanze sane): a ogni passata
        # si ottiene un tunnel URL fresco dalla stessa istanza
        for pass_n in (1, 2):
            for api in candidates:
                try:
                    log.info("cobalt: provo %s (passata %d)", api, pass_n)
                    resp = _cobalt_resolve(api, url)
                    _download_stream(resp["url"], src)
                    filename_hint = resp.get("filename") or ""
                    log.info("cobalt: audio scaricato via %s", api)
                    done = True
                    break
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{api} ({exc})")
                    log.warning("cobalt %s fallito: %s", api, exc)
            if done:
                break
        if not done:
            raise RuntimeError(
                "nessuna istanza Cobalt ha potuto scaricare questo link "
                f"({len(errors)} tentativi). Ultimi errori: "
                + "; ".join(errors[-3:])
                + ". Puoi indicare istanze tue in COBALT_API_URLS"
            )

    _ffmpeg_to_wav(src, d / "audio.wav")

    meta = _oembed(url)
    if not meta.get("title") and filename_hint:
        meta["title"] = filename_hint.rsplit(".", 1)[0]
    meta.setdefault("title", "")
    meta.setdefault("artist", "")
    meta["duration"] = _ffprobe_duration(d / "audio.wav")
    meta["webpage_url"] = url
    return meta
