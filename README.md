# 🎸 Autotabber

**Self-hosted, pronto per Portainer.** Carica un file audio (o un video da
cui estrarre l'audio) e ottieni un tab **multi-traccia in stile Songsterr**:
lo strumento capisce da solo quali strumenti ci sono nel brano — voce,
chitarra, basso, piano, synth e batteria — e genera una tabulatura per
ognuno, **voce inclusa** (mappata sulla tastiera), visualizzata e
**suonabile nel browser** (alphaTab), senza dipendenze da servizi a pagamento.

Niente YouTube, niente proxy, niente blocchi anti-bot: il file lo carichi
tu dal browser (drag & drop) e viene processato interamente nel tuo server.
Formati accettati: `.mp3 .wav .m4a .flac .ogg .opus .aac .wma .webm .mp4`.

```
file caricato ──ffmpeg──► audio.wav ──Demucs (htdemucs_6s, 6 stems)──► stems
     │  energia p90 per stem ──► strumenti presenti (voce/chitarra/basso/
     │  piano/synth/batteria)
     ├─ stem melodici ──Basic Pitch──► MIDI ──quantizzazione sul beat grid
     │  ──diteggiatura cost-based──► tracce con corda/tasto
     ├─ stem batteria ──onsets + bande di frequenza──► notazione percussione
     └─ tex.py ──► alphaTex multi-traccia ──alphaTab──► tab suonabile ▶
         (+ export Guitar Pro 5 con tutte le tracce)
```

### Come funziona il rilevamento degli strumenti

Ogni stem Demucs viene misurato con il **90° percentile dell'energia**
sui frame attivi del mix: uno strumento e' "presente" se il suo stem
tocca un'energia vicina a quella del mix almeno in una parte rumorosa
del brano (cosi' vengono visti anche gli strumenti che suonano solo in
alcune sezioni). Soglie regolabili con `DETECT_REL_DB` e
`DETECT_DRUMS_REL_DB` nello stack (default -25/-28 dB).

## Servizi dello stack

| Servizio | Immagine | Ruolo |
|----------|----------|-------|
| `web` | nginx + alphaTab (vendored) | UI upload + player tab, proxy `/api` |
| `api` | FastAPI | upload file + crea/consulta i job |
| `worker` | Python + torch CPU + Demucs + Basic Pitch | pipeline pesante |
| `redis` | Redis 7 | coda job (RQ) + metadati |

Le immagini possono essere pubblicate automaticamente su
`ghcr.io/antonydp/autotabber-{api,worker,web}:latest` abilitando GitHub
Actions (vedi sotto) — ma lo stack **funziona anche senza**, costruendo
le immagini in loco.

## Requisiti

- Docker / Portainer (CE va benissimo)
- ~2 GB liberi su disco per l'immagine worker
- **RAM: 6–8 GB liberi per il worker** (Demucs `htdemucs_6s` su CPU è affamato)
- Connessione internet solo per scaricare i modelli al primo avvio

## Deploy su Portainer

### Metodo 1 — Da repository Git (consigliato, funziona subito)

1. **Portainer → Stacks → + Add stack → Repository**
2. Repository URL: `https://github.com/antonydp/autotabber`,
   Compose path: `docker-compose.yml`, branch `main`
3. Nella sezione **Environment variables** aggiungi (opzionale):
   `MODAL_ENABLED=1`, `MODAL_TOKEN_ID=…`, `MODAL_TOKEN_SECRET=…`
   (e se serve `WEB_PORT=8088`)
4. **Deploy the stack** — Portainer clona il repo e costruisce le immagini
   (la prima build del worker scarica torch/TF: ~5–10 min)

### Metodo 2 — Web editor (richiede immagini GHCR già pubblicate)

1. Incolla `portainer-stack.yml` in **Stacks → autotabber → Editor → Update**
2. Le immagini vengono scaricate da GHCR (vedi "Abilitare la build
   automatica" qui sotto)

### CLI (senza Portainer)

```bash
git clone https://github.com/antonydp/autotabber && cd autotabber
docker compose up -d --build
```

### Abilitare la build automatica (GHCR, opzionale)

Il workflow GitHub Actions (`.github/workflows/docker.yml`) pubblica le tre
immagini su GHCR ad ogni push su `main`. Rendi pubblici i pacchetti su
`https://github.com/antonydp?tab=packages` (*Package settings → Change
visibility → Public*, una tantum): da quel momento il Metodo 2 (Web editor)
funziona senza build.

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
| `UPLOAD_MAX_MB` | `300` | tetto massimo del file caricato dal browser |
| `MODAL_ENABLED` | `0` | `1` = usa GPU Modal se disponibile |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | — | credenziali Modal (mai committare) |
| `MODAL_APP` / `MODAL_FN` | `autotabber` / `transcribe` | funzione remota |
| `DEMUCS_ENABLED` | `1` | separazione stems (0 = trascrivi l'audio intero) |
| `DEMUCS_MODEL` | `htdemucs_6s` | modello Demucs (6 stems: include la chitarra) |
| `MAX_DURATION_SEC` | `900` | rifiuta brani più lunghi di 15 min |
| `KEEP_AUDIO` | `0` | conserva i wav intermedi per debug |

Vedi anche [`.env.example`](.env.example).

## API

```
POST   /api/upload          multipart (campo "file") -> {id}
GET    /api/jobs            lista job
GET    /api/jobs/{id}       stato: queued|preparing|separating|transcribing|rendering|done|error
GET    /api/jobs/{id}/tex   alphaTex generato
GET    /api/jobs/{id}/midi  MIDI scaricabile
GET    /api/jobs/{id}/gp5   Guitar Pro 5 scaricabile (se generato)
DELETE /api/jobs/{id}       elimina job e file
```

## Sicurezza

- **Nessuna chiave è presente nel repository**: i token Modal vivono solo
  nelle variabili d'ambiente di Portainer (o in un `.env` locale, che è in
  `.gitignore`). Il token GitHub usato per il push non è salvato nel repo.
- I file caricati vivono solo nel volume Docker locale.

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
| Upload rifiutato (413) | alza `UPLOAD_MAX_MB` (api) e `client_max_body_size` in `web/nginx.conf` |
| Worker OOM durante Demucs | riduci a `DEMUCS_ENABLED=0`, oppure usa Modal (GPU), oppure più RAM |
| Nessun suono nel player | soundfont servita da `/soundfont/sonivox.sf2`; verifica col bundle nginx |
| Brani più lunghi di 15 min | aumenta `MAX_DURATION_SEC` (i tempi si allungano molto) |
| File molto grandi | aumenta `UPLOAD_MAX_MB` |

## Licenze dei bundle

- [alphaTab](https://github.com/coderline/alphaTab) — MPL-2.0 (vendored `alphaTab.min.js` + `sonivox.sf2`)
- Demucs (MIT) · Basic Pitch (Apache-2.0)
