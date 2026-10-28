# 🎸 Autotabber

**Self-hosted, pronto per Portainer.** Incolla un link YouTube e ottieni una
tablatura chitarra con diteggiatura, visualizzata e **suonabile nel browser**
(grazie ad alphaTab), senza dipendenze da servizi a pagamento.

```
YouTube ──yt-dlp──► audio.wav ──Demucs (htdemucs_6s)──► stem chitarra
        ──Basic Pitch──► MIDI ──tex.py (BPM + diteggiatura)──► alphaTex
        ──alphaTab nel browser──► tab suonabile ▶
```

## Servizi dello stack

| Servizio | Immagine | Ruolo |
|----------|----------|-------|
| `web` | nginx + alphaTab (vendored) | UI + player tab, proxy `/api` |
| `api` | FastAPI | crea/consulta i job |
| `worker` | Python + torch CPU + Demucs + Basic Pitch + yt-dlp | pipeline pesante |
| `redis` | Redis 7 | coda job (RQ) + metadati |

Le immagini sono pubblicate da GitHub Actions su
`ghcr.io/antonydp/autotabber-{api,worker,web}:latest`.

## Requisiti

- Docker / Portainer (CE va benissimo)
- ~2 GB liberi su disco per l'immagine worker
- **RAM: 6–8 GB liberi per il worker** (Demucs `htdemucs_6s` su CPU è affamato)
- Connessione internet (YouTube + download modelli al primo avvio)

## Deploy su Portainer

### Metodo 1 — Web editor (consigliato)

1. **Portainer → Stacks → + Add stack → Web editor**
2. Incolla il contenuto di [`docker-compose.yml`](docker-compose.yml)
3. Nella sezione **Environment variables** aggiungi (opzionale):
   `MODAL_ENABLED=1`, `MODAL_TOKEN_ID=…`, `MODAL_TOKEN_SECRET=…`
   (e se serve `WEB_PORT=8088`)
4. **Deploy the stack**

### Metodo 2 — Da repository Git

1. **Stacks → + Add stack → Repository**
2. Repository URL: `https://github.com/antonydp/autotabber`,
   Compose path: `docker-compose.yml`, branch `main`
3. Aggiungi le environment variables come sopra → **Deploy**

> Se le immagini GHCR risultassero private: su GitHub apri
> `https://github.com/antonydp?tab=packages` → pacchetto →
> *Package settings → Change visibility → Public* (una tantum), oppure
> configura le credenziali GHCR in **Portainer → Registries**.

### Build locale (senza GHCR)

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

## GPU opzionale con Modal

Il worker di default lavora su CPU: Demucs impiega ~2–5 min/brano.
Con [Modal](https://modal.com) (piano free) separazione + trascrizione girano
su T4 in ~20–40 secondi.

```bash
export MODAL_TOKEN_ID=...        # chiavi SOLO come variabili, mai nei file
export MODAL_TOKEN_SECRET=...
modal deploy modal_app.py        # crea l'app "autotabber"
```

Poi nello stack Portainer: `MODAL_ENABLED=1` + le due chiavi come env vars.
In caso di errore Modal il worker ricade automaticamente su Basic Pitch locale.

## Variabili d'ambiente

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `WEB_PORT` | `8088` | porta web esposta |
| `MODAL_ENABLED` | `0` | `1` = usa GPU Modal se disponibile |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | — | credenziali Modal (mai committare) |
| `MODAL_APP` / `MODAL_FN` | `autotabber` / `transcribe` | funzione remota |
| `DEMUCS_ENABLED` | `1` | separazione stems (0 = trascrivi l'audio intero) |
| `DEMUCS_MODEL` | `htdemucs_6s` | modello Demucs (6 stems: include la chitarra) |
| `YT_MAX_DURATION_SEC` | `900` | rifiuta video più lunghi di 15 min |
| `KEEP_AUDIO` | `0` | conserva i wav intermedi per debug |
| `YT_DLP_UPDATE` | `0` | aggiorna yt-dlp all'avvio del worker |

Vedi anche [`.env.example`](.env.example).

## API

```
POST   /api/jobs            {"url": "https://youtu.be/..."} -> {id}
GET    /api/jobs            lista job
GET    /api/jobs/{id}       stato: queued|downloading|separating|transcribing|rendering|done|error
GET    /api/jobs/{id}/tex   alphaTex generato
GET    /api/jobs/{id}/midi  MIDI scaricabile
DELETE /api/jobs/{id}       elimina job e file
```

## Sicurezza

- **Nessuna chiave è presente nel repository**: i token Modal vivono solo
  nelle variabili d'ambiente di Portainer (o in un `.env` locale, che è in
  `.gitignore`). Il token GitHub usato per il push non è salvato nel repo.
- Il GitHub token con scope `workflow` ha generato la build automatica;
  ruotalo se condiviso.

## Note sui modelli di trascrizione

- **Basic Pitch** (Spotify, Apache-2.0): scelto per il percorso CPU — leggero,
  robusto, ottimo su chitarra isolata.
- **Demucs `htdemucs_6s`**: unico Demucs con stem chitarra dedicato.
- State of the art 2024–25 (upgrade futuri):
  - **YourMT3+** (2024): trascrizione multi-strumento, ibrido MT3/PerceiverTF;
  - **MIDI-to-Tab / Fretting-Transformer** (2024–25): MIDI → tablatura con
    diteggiatura realistica via Transformer — sostituibile al posto di `tex.py`.
- Il trascrittore Modal (`modal_app.py`) è isolato: puoi aggiornarlo a
  YourMT3+ senza toccare lo stack.

## Troubleshooting

| Problema | Rimedio |
|----------|---------|
| `Sign in to confirm you're not a bot` | aggiorna yt-dlp: `YT_DLP_UPDATE=1` (o `docker exec -it <worker> pip install -U yt-dlp`) |
| Worker OOM durante Demucs | riduci a `DEMUCS_ENABLED=0`, oppure usa Modal (GPU), oppure più RAM |
| Video lenti/age-restricted | yt-dlp con cookies: personalizza `download_audio()` in `worker/pipeline.py` |
| Nessun suono nel player | soundfont servita da `/soundfont/sonivox.sf2`; verifica col bundle nginx |
| Video più lunghi di 15 min | aumenta `YT_MAX_DURATION_SEC` (i tempi si allungano molto) |

## Licenze dei bundle

- [alphaTab](https://github.com/coderline/alphaTab) — MPL-2.0 (vendored `alphaTab.min.js` + `sonivox.sf2`)
- Demucs (MIT) · Basic Pitch (Apache-2.0) · yt-dlp (Unlicense)
