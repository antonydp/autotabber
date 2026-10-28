# AutoTabber

**YouTube link -> tablatura con lettore stile Songsterr, self-hosted.**

Incolli un link YouTube, scegli lo strumento (elettrica / acustica / basso) e ottieni una
tablatura riproducibile nel browser con cursore di sincronizzazione, controllo velocità e
download in Guitar Pro, MIDI e alphaTex.

## Come funziona

```
YouTube ──(yt-dlp)──► mix audio ──► ┌─────────────────────────┐
                                    │ MODAL GPU (A10G)        │
                                    │ Mel-Band RoFormer 2025  │  separazione stem chitarra
                                    │ (becruily checkpoint)   │
                                    └───────────┬─────────────┘
                                                │ stem chitarra
                                                ▼
                                    ┌─────────────────────────┐
                                    │ MODAL CPU               │
                                    │ Basic Pitch (ONNX)      │  audio → MIDI + tempo
                                    └───────────┬─────────────┘
                                                │ MIDI
                                                ▼
                                    ┌─────────────────────────┐
                                    │ APP DOCKER (FastAPI)    │
                                    │ fingering DP → alphaTex │  corde/tasti ottimali
                                    │ player alphaTab         │  lettore stile Songsterr
                                    └─────────────────────────┘
```

- **Separazione**: [Mel-Band RoFormer](https://huggingface.co/becruily/mel-band-roformer-guitar)
  (checkpoint 2025 dedicato alla chitarra) eseguito con
  [MSST](https://github.com/ZFTurbo/Music-Source-Separation-Training) su GPU Modal.
- **Trascrizione**: [Basic Pitch](https://github.com/spotify/basic-pitch) di Spotify (ONNX) su CPU Modal.
- **Tab**: algoritmo di diteggiatura (programmazione dinamica) che minimizza gli spostamenti
  della mano, con supporto accordi.
- **Lettore**: [alphaTab](https://alphatab.net) (MPL-2.0), vendorizzato — nessun CDN esterno.

## Requisiti

- Un account [Modal](https://modal.com) gratuito (per GPU/CPU) con token attivo
- Docker

## Setup

1. **Chiavi Modal** — copia l'esempio e inserisci le tue credenziali:

   ```bash
   cp .env.example .env
   # edita .env: MODAL_TOKEN_ID=...  MODAL_TOKEN_SECRET=...
   # (modal token new  per generarle)
   ```

   Le chiavi vivono **solo** nel file `.env` (gitignored) o nell'ambiente del container:
   non sono mai salvate nel codice né nella repo.

2. **Avvia**:

   ```bash
   docker compose up --build
   ```

   Alla prima richiesta il container esegue automaticamente `modal deploy modal_app.py`
   (build dell'immagine MSST ~5-10 minuti, poi cached da Modal).

3. Apri `http://localhost:8000`, incolla il link e genera.

Per il deploy manuale della parte Modal:

```bash
pip install modal && modal deploy modal_app.py
```

## Costi Modal (indicativi)

- Separazione GPU A10G: ~0.0006 $/s → tipicamente 0.05-0.40 $ a brano
- Trascrizione CPU: ~0.01-0.05 $ a brano
- Con `scaledown_window` di 60s, brani consecutivi riutilizzano i container (cold start evitati)

## Struttura

```
autotabber/
├── modal_app.py          # funzioni Modal: separazione GPU + trascrizione CPU
├── backend/
│   ├── main.py           # FastAPI: job API + static frontend
│   ├── pipeline.py       # orchestrazione yt-dlp → Modal → MIDI → tab
│   ├── fingering.py      # DP stringa/tasto
│   ├── alphatex.py       # quantizzazione ritmica + alphaTex
│   ├── tabwriter.py      # export Guitar Pro 5
│   └── settings.py       # config da env (nessuna chiave hard-coded)
├── frontend/
│   ├── index.html        # UI
│   ├── assets/           # css + js (player alphaTab)
│   └── vendor/alphatab/  # alphaTab + font Bravura + soundfont (vendorizzato)
├── Dockerfile
├── docker-compose.yml
└── scripts/deploy_modal.sh
```

## Limiti noti (v1)

- Accordatura standard (EADGBE) / basso standard; nessun capo o drop-tuning
- Ritmica quantizzata sulla semicroma: sincopazioni complesse possono semplificarsi
- Le tecniche (bend, slide, hammer-on) non sono rilevate
- Tracce con chitarra molto distorta/mista al resto danno trascrizioni più rumorose
- Il download da YouTube dipende dalla disponibilità di yt-dlp (aggiornalo se fallisce)

## Sicurezza

- Nessuna chiave nel codice o nella repo: solo variabili d'ambiente (`MODAL_TOKEN_ID`,
  `MODAL_TOKEN_SECRET`)
- `.env` è in `.gitignore`; in repo c'è solo `.env.example` con placeholder
- Se un token è stato incollato in chat o altrove, **revocalo** su
  [modal.com/settings](https://modal.com/settings) e genera un nuovo token

## Licenze

Codice progetto: MIT. Componenti di terze parti elencati in `LICENSE`
(alphaTab MPL-2.0, Basic Pitch Apache-2.0, checkpoint UVR/MSST con rispettive licenze).
