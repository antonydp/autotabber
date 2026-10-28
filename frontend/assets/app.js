import * as alphaTab from '/vendor/alphatab/alphaTab.min.mjs';

const $ = (id) => document.getElementById(id);

let api = null;
let pollTimer = null;
let jobId = null;

const btnPlay = $('btn-play');
const btnStop = $('btn-stop');
const speedInput = $('speed');
const speedLabel = $('speed-label');

function showError(msg) {
  const el = $('error');
  el.textContent = msg;
  el.classList.remove('hidden');
  $('go').disabled = false;
}

function clearError() {
  $('error').classList.add('hidden');
  $('error').textContent = '';
}

function setProgress(pct, stage) {
  $('bar-fill').style.width = pct + '%';
  $('stage').textContent = stage || 'Lavorazione\u2026';
}

async function submitJob() {
  clearError();
  const url = $('url').value.trim();
  if (!url) { showError('Inserisci un link YouTube.'); return; }
  if (!/^https?:\/\//.test(url)) { showError('Inserisci un URL valido.'); return; }

  $('go').disabled = true;
  $('player-card').classList.add('hidden');
  $('progress-card').classList.remove('hidden');
  setProgress(2, 'Invio richiesta\u2026');

  try {
    const res = await fetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, instrument: $('instrument').value }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || ('Errore ' + res.status));
    }
    const { id } = await res.json();
    jobId = id;
    pollTimer = setInterval(poll, 1500);
  } catch (e) {
    showError('Impossibile avviare il job: ' + e.message);
    $('progress-card').classList.add('hidden');
  }
}

async function poll() {
  if (!jobId) return;
  try {
    const res = await fetch('/api/jobs/' + jobId);
    if (!res.ok) throw new Error('status ' + res.status);
    const job = await res.json();

    setProgress(job.progress || 0, job.stage);

    if (job.status === 'error') {
      clearInterval(pollTimer);
      pollTimer = null;
      $('progress-card').classList.add('hidden');
      $('go').disabled = false;
      showError('Job fallito:\n' + (job.error || 'errore sconosciuto'));
      return;
    }

    if (job.status === 'done') {
      clearInterval(pollTimer);
      pollTimer = null;
      $('progress-card').classList.add('hidden');
      $('go').disabled = false;
      showResult(job);
    }
  } catch (e) {
    // transient network errors: keep polling
    console.warn('poll error', e);
  }
}

function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

const FILE_LABELS = {
  'tab.gp5': 'Scarica Guitar Pro (.gp5)',
  'track.mid': 'Scarica MIDI (.mid)',
  'tab.alphatex': 'Scarica alphaTex (.txt)',
  'stem_guitar.wav': 'Ascolta traccia chitarra (.wav)',
};

function showResult(job) {
  $('result-title').textContent = job.title || 'Trascrizione';
  $('meta').textContent =
    'Tempo stimato: ' + Math.round(job.tempo) + ' BPM \u00b7 strumento: ' + job.instrument;

  // downloads
  const dl = $('downloads');
  dl.innerHTML = '';
  for (const [key, href] of Object.entries(job.files || {})) {
    const a = document.createElement('a');
    a.href = href;
    a.download = key;
    a.textContent = FILE_LABELS[key] || key;
    dl.appendChild(a);
  }

  $('player-card').classList.remove('hidden');

  // build alphaTab player
  const el = $('alphatab');
  el.innerHTML = '';
  if (api) {
    try { api.destroy(); } catch (_) { /* noop */ }
    api = null;
  }

  api = new alphaTab.AlphaTabApi(el, {
    core: {
      fontDirectory: '/vendor/alphatab/font/',
    },
    player: {
      enablePlayer: true,
      enableCursor: true,
      soundFont: '/vendor/alphatab/soundfont/sonivox.sf3',
      scrollElement: el,
      scrollMode: alphaTab.ScrollMode.Continuous,
    },
    display: {
      stretchToViewportWidth: false,
      scale: 1.0,
    },
  });

  api.tex(job.alphatex);

  api.renderFinished.on(() => {
    // player ready to use once rendered
    updatePlayButton();
  });

  api.playerStateChanged.on((args) => {
    updatePlayButton(args.state);
  });

  updatePlayButton();
}

function updatePlayButton(state) {
  let playing = false;
  try {
    if (typeof state === 'number') {
      playing = state === alphaTab.synth.PlayerState.Playing;
    } else if (api) {
      playing = api.playerState() === alphaTab.synth.PlayerState.Playing;
    }
  } catch (_) { /* player not ready yet */ }
  btnPlay.innerHTML = playing ? '&#10074;&#10074;' : '&#9654;';
}

btnPlay.addEventListener('click', () => { if (api) api.playPause(); });
btnStop.addEventListener('click', () => { if (api) api.stop(); });
speedInput.addEventListener('input', () => {
  const v = parseFloat(speedInput.value);
  speedLabel.textContent = v.toFixed(2) + '\u00d7';
  if (api) api.playbackSpeed = v;
});

document.addEventListener('keydown', (ev) => {
  if (ev.code === 'Space' && api && !$('url').matches(':focus')) {
    ev.preventDefault();
    api.playPause();
  }
});

$('go').addEventListener('click', submitJob);
$('url').addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter') submitJob();
});

// exposed for the /test-player demo page
window.showResult = showResult;
