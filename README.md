# 🎸 Autotabber

**Self-hosted, pronto per Portainer.** Incolla il link di un brano e ottieni
una tablatura chitarra con diteggiatura, visualizzata e **suonabile nel
browser** (grazie ad alphaTab), senza dipendenze da servizi a pagamento.

Il download gira con **Cobalt self-hosted nello stack**: il link viene
risolto dalla tua istanza privata (`cobalt`), che parla con YouTube usando
una sessione (PO token + visitor_data) generata da
[yt-session-generator](https://github.com/imputnet/yt-session-generator)
e rinnovata in automatico. Nessuna istanza pubblica, nessun rate limit
condiviso. Sono accettati anche file audio diretti (.mp3/.wav/…).

> Nota onesta: YouTube combatte il download da IP datacenter. La sessione
> generata copre la maggior parte dei video; se qualche video resta
> bloccato (age-restricted o contenuti protetti), si aggiungono i propri
> cookie (vedi sotto) — è la configurazione raccomandata dalla community.

```
link ──cobalt (self-hosted + sessione)──► audio.wav ──Demucs (htdemucs_6s)──► stem chitarra
     ──Basic Pitch──► MIDI ──tex.py (BPM + diteggiatura)──► alphaTex
     ──alphaTab nel browser──► tab suonabile ▶
```

### Cookie (opzionale, per i video tosti)

1. Dal tuo browser, copia gli header cookie di `youtube.com` (estensione
   tipo *Cookie-Editor* → Export → *Header string*)
2. Crea `cookies.json`: `{"youtube": ["VISITOR_INFO1_LIVE=…; SID=…; …"]}`
3. Copialo nel volume: `docker cp cookies.json <volume-app-data>:/cobalt-cookies.json`
   (per trovare il volume: `docker volume ls | grep app-data`)
4. Nello stack decommenta `COOKIE_PATH` e `volumes` nel servizio `cobalt`
   e fai **Update stack**

> I cookie sono sensibili: vivono solo nel volume Docker, mai nel repo.

## Servizi dello stack

| Servizio | Immagine | Ruolo |
|----------|----------|-------|
| `web` | nginx + alphaTab (vendored) | UI + player tab, proxy `/api` |
| `api` | FastAPI | crea/consulta i job |
| `worker` | Python + torch CPU + Demucs + Basic Pitch | pipeline pesante |
| `cobalt` | Cobalt 11 (self-hosted) | risolve i link: parla con YouTube per noi |
| `yt-session-generator` | imput (chromium) | genera PO token/visitor_data per cobalt |
| `redis` | Redis 7 | coda job (RQ) + metadati |

Le immagini possono essere pubblicate automaticamente su
`ghcr.io/antonydp/autotabber-{api,worker,web}:latest` abilitando GitHub
Actions (vedi sotto) — ma lo stack **funziona anche senza**, costruendo
le immagini in loco.

## Requisiti

- Docker / Portainer (CE va benissimo)
- ~2 GB liberi su disco per l'immagine worker
- **RAM: 6–8 GB liberi per il worker** (Demucs `htdemucs_6s` su CPU è affamato)
- Connessione internet (YouTube + download modelli al primo avvio)

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

1. Incolla `docker-compose.yml` in **Stacks → Add stack → Web editor**
2. Aggiungi le environment variables → **Deploy**
3. Le immagini vengono scaricate da GHCR (vedi "Abilitare la build
   automatica" qui sotto)

### CLI (senza Portainer)

```bash
git clone https://github.com/antonydp/autotabber && cd autotabber
docker compose up -d --build
```

### Abilitare la build automatica (GHCR, opzionale)

Il token usato per il push non ha scope `workflow`, quindi il workflow non è
attivo. Per attivarlo:

1. Crea un PAT GitHub con scope **repo + workflow**
2. Sposta il file: `docs/workflow-docker.yml.disabled` →
   `.github/workflows/docker.yml` e fai push con il nuovo token
3. Ad ogni push su `main` Actions pubblica le tre immagini su GHCR
4. Rendi pubblici i pacchetti su `https://github.com/antonydp?tab=packages`
   (*Package settings → Change visibility → Public*, una tantum): da quel
   momento il Metodo 2 (Web editor) funziona senza build

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
| `MAX_DURATION_SEC` | `900` | rifiuta brani più lunghi di 15 min |
| `KEEP_AUDIO` | `0` | conserva i wav intermedi per debug |
| `COBALT_API_URLS` | `http://cobalt:9000` | istanze Cobalt usate dal worker; il default è la tua istanza interna, puoi aggiungerne esterne come riserva |
| `DOWNLOAD_MAX_MB` | `300` | tetto massimo del file audio scaricato |
| `COBALT_MAX_TRIES` | `6` | quante istanze provare in cascata |

Vedi anche [`.env.example`](.env.example).

## API

```
POST   /api/jobs            {"url": "https://youtu.be/..."} -> {id}
GET    /api/jobs            lista job
GET    /api/jobs/{id}       stato: queued|downloading|separating|transcribing|rendering|done|error
GET    /api/jobs/{id}/tex   alphaTex generato
GET    /api/jobs/{id}/midi  MIDI scaricabile
GET    /api/jobs/{id}/gp5   Guitar Pro 5 scaricabile (se generato)
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
| Tutte le istanze Cobalt falliscono | verifica i log di `cobalt` e `yt-session-generator` (`docker logs`); per video age-restricted aggiungi i cookie (vedi sopra) |
| Worker OOM durante Demucs | riduci a `DEMUCS_ENABLED=0`, oppure usa Modal (GPU), oppure più RAM |
| Nessun suono nel player | soundfont servita da `/soundfont/sonivox.sf2`; verifica col bundle nginx |
| Brani più lunghi di 15 min | aumenta `MAX_DURATION_SEC` (i tempi si allungano molto) |
| File audio molto grandi | aumenta `DOWNLOAD_MAX_MB` |

## Licenze dei bundle

- [alphaTab](https://github.com/coderline/alphaTab) — MPL-2.0 (vendored `alphaTab.min.js` + `sonivox.sf2`)
- Demucs (MIT) · Basic Pitch (Apache-2.0) · [Cobalt](https://github.com/imputnet/cobalt) (AGPL-3.0, usato via API pubblica delle istanze)
