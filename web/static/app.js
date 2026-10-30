/* Autotabber — logica frontend (vanilla JS) */
"use strict";

const $ = (sel) => document.querySelector(sel);

const STAGES_IT = {
  queued: "In coda",
  preparing: "Preparazione audio",
  separating: "Separazione stems",
  transcribing: "Trascrizione",
  rendering: "Rendering tab",
  done: "Pronto",
  error: "Errore",
};

const state = {
  jobs: [],
  currentJob: null,
  atApi: null,
  renderedFor: null,
  pollTimer: null,
};

/* ---------- utility ---------- */

function parseTracks(job) {
  if (!job || !job.tracks) return [];
  try {
    const arr = JSON.parse(job.tracks);
    return Array.isArray(arr) ? arr : [];
  } catch (_) {
    return [];
  }
}

function trackChips(tracks) {
  if (!tracks || !tracks.length) return "";
  return tracks
    .map((t) => `<span class="chip done">${escapeHtml(t.name || "?")}${t.notes ? ` · ${t.notes}` : ""}</span>`)
    .join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtDur(sec) {
  sec = parseInt(sec || 0, 10);
  if (!sec) return "";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function isBusy(job) {
  return ["queued", "preparing", "separating", "transcribing", "rendering"].includes(job.status);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch (_) { /* noop */ }
    throw new Error(detail);
  }
  return res.json();
}

/* ---------- lista job ---------- */

function jobCard(job) {
  const el = document.createElement("div");
  el.className = "job";
  const busy = isBusy(job);
  const label = STAGES_IT[job.status] || job.status;

  el.innerHTML = `
    <div class="row">
      <div>
        <div class="title"></div>
        <div class="artist"></div>
        <div class="chips"></div>
      </div>
      <div class="grow"></div>
      <span class="chip ${job.status === "done" ? "done" : job.status === "error" ? "error" : busy ? "busy" : ""}">${label}</span>
      ${job.status === "done" ? '<button class="primary btn-open">Apri player</button>' : ""}
      <button class="ghost btn-del" title="Elimina">✕</button>
    </div>
    ${busy ? '<div class="bar"><div></div></div>' : ""}
    ${job.status === "error" ? '<div class="error-text"></div>' : ""}
  `;

  el.querySelector(".title").textContent = job.title || job.filename || job.id;
  el.querySelector(".artist").textContent =
    [job.artist, fmtDur(job.duration)].filter(Boolean).join(" · ");
  const chipsEl = el.querySelector(".chips");
  const chips = trackChips(parseTracks(job));
  if (chips) chipsEl.innerHTML = chips; else chipsEl.remove();
  const chip = el.querySelector(".chip");
  if (busy) chip.textContent = `${label} · ${job.progress || 0}%`;
  if (job.status === "error") el.querySelector(".error-text").textContent = job.error || "Errore sconosciuto";
  if (busy) el.querySelector(".bar > div").style.width = `${job.progress || 0}%`;

  if (job.status === "done") {
    el.querySelector(".btn-open").addEventListener("click", () => openPlayer(job.id));
  }
  el.querySelector(".btn-del").addEventListener("click", async () => {
    try {
      await api(`/api/jobs/${job.id}`, { method: "DELETE" });
      if (state.currentJob === job.id) closePlayer();
      refreshJobs();
    } catch (e) { showMsg(e.message, "error"); }
  });
  return el;
}

async function refreshJobs() {
  try {
    state.jobs = await api("/api/jobs");
    const list = $("#job-list");
    list.replaceChildren(...state.jobs.map(jobCard));
    $("#empty").hidden = state.jobs.length > 0;
  } catch (_) { /* noop */ }
}

/* ---------- upload file ---------- */

const ACCEPTED = [".mp3", ".wav", ".m4a", ".m4b", ".flac", ".ogg", ".oga",
  ".opus", ".aac", ".wma", ".webm", ".mp4"];

let pickedFile = null;

function showMsg(text, kind) {
  const el = $("#form-msg");
  el.hidden = false;
  el.className = `msg ${kind || "info"}`;
  el.textContent = text;
}

function setPicked(file) {
  if (!file) return;
  const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  if (!ACCEPTED.includes(ext)) {
    showMsg(`Formato ${ext || "sconosciuto"} non supportato. Usa: ${ACCEPTED.join(", ")}`, "error");
    return;
  }
  pickedFile = file;
  $("#drop-label").textContent = `🎵 ${file.name} (${(file.size / 1048576).toFixed(1)} MB)`;
  $("#drop").classList.add("picked");
  $("#btn-go").disabled = false;
  showMsg("", "info");
  $("#form-msg").hidden = true;
}

$("#file").addEventListener("change", (ev) => setPicked(ev.target.files[0]));

const dropEl = $("#drop");
["dragenter", "dragover"].forEach((ev) =>
  dropEl.addEventListener(ev, (e) => { e.preventDefault(); dropEl.classList.add("over"); })
);
["dragleave", "drop"].forEach((ev) =>
  dropEl.addEventListener(ev, (e) => { e.preventDefault(); dropEl.classList.remove("over"); })
);
dropEl.addEventListener("drop", (e) => setPicked(e.dataTransfer.files[0]));

$("#form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!pickedFile) {
    showMsg("Scegli prima un file audio/video.", "error");
    return;
  }
  const btn = $("#btn-go");
  btn.disabled = true;

  const fd = new FormData();
  fd.append("file", pickedFile, pickedFile.name);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      showMsg(`Caricamento… ${pct}%`, "info");
    }
  };
  xhr.onload = () => {
    btn.disabled = false;
    if (xhr.status >= 200 && xhr.status < 300) {
      const j = JSON.parse(xhr.responseText);
      showMsg(`Tab #${j.id} in coda: lo vedi comparire qui sotto.`, "info");
      pickedFile = null;
      $("#file").value = "";
      $("#drop-label").textContent = "🎵 Trascina qui un file audio/video, oppure clicca per sceglierlo";
      $("#drop").classList.remove("picked");
      refreshJobs();
    } else {
      let detail = xhr.statusText;
      try { detail = JSON.parse(xhr.responseText).detail || detail; } catch (_) { /* noop */ }
      showMsg(detail, "error");
    }
  };
  xhr.onerror = () => {
    btn.disabled = false;
    showMsg("Errore di rete durante il caricamento.", "error");
  };
  showMsg("Caricamento… 0%", "info");
  xhr.send(fd);
});

/* ---------- player alphaTab ---------- */

async function pickSoundFont() {
  const candidates = ["/soundfont/sonivox.sf2"];
  for (const u of candidates) {
    try {
      const r = await fetch(u, { method: "HEAD" });
      if (r.ok) return u;
    } catch (_) { /* noop */ }
  }
  return null;
}

function ensureApi(soundFont) {
  if (state.atApi) return state.atApi;
  const settings = {
    core: {
      fontDirectory: "/vendor/font/",
    },
    player: {
      enablePlayer: !!soundFont,
      soundFont: soundFont || undefined,
    },
  };
  state.atApi = new alphaTab.AlphaTabApi($("#alphaTab"), settings);
  state.atApi.playerStateChanged.on((args) => {
    const playing = args && args.state === 2; // PlayerState.Playing
    $("#btn-play").textContent = playing ? "⏸ Pausa" : "▶ Play";
  });
  return state.atApi;
}

async function openPlayer(id) {
  state.currentJob = id;
  state.renderedFor = null;
  $("#player-view").classList.remove("hidden");
  $("#pv-status").textContent = "Caricamento…";
  $("#alphaTab").innerHTML = "";

  let job;
  try {
    job = await api(`/api/jobs/${id}`);
  } catch (e) {
    $("#pv-status").textContent = `Errore: ${e.message}`;
    return;
  }

  $("#pv-title").textContent = job.title || id;
  $("#pv-artist").textContent = job.artist || "";
  $("#pv-tracks").innerHTML = trackChips(parseTracks(job));
  $("#dl-midi").href = `/api/jobs/${id}/midi`;
  $("#dl-tex").href = `/api/jobs/${id}/tex`;
  const gp5 = $("#dl-gp5");
  if (job.gp5 === "1") { gp5.href = `/api/jobs/${id}/gp5`; gp5.hidden = false; }
  else { gp5.hidden = true; }

  if (job.status !== "done") {
    $("#pv-status").textContent = `Stato: ${STAGES_IT[job.status] || job.status} (${job.progress || 0}%)`;
    pollCurrent();
    return;
  }

  const sf = await pickSoundFont();
  const apiAT = ensureApi(sf);
  $("#pv-status").textContent = sf ? "" : "Soundfont non disponibile: tab visibile senza audio.";

  try {
    const res = await fetch(`/api/jobs/${id}/tex`);
    const tex = await res.text();
    const load = apiAT.tex ? (t) => apiAT.tex(t) : (t) => apiAT.loadAlphaTex(t);
    // alphaTab renderizza solo le tracce passate come secondo argomento
    // (default [0]): servono tutti gli indici delle tracce rilevate
    const nTracks = Math.max(1, parseTracks(job).length);
    const all = Array.from({ length: nTracks }, (_, i) => i);
    try {
      load(tex, all);
    } catch (_) {
      load(tex); // fallback: solo la prima traccia
    }
    state.renderedFor = id;
    setTimeout(() => {
      if (state.currentJob === id && !$("#alphaTab svg")) {
        $("#pv-status").textContent = "Rendering non riuscito: controlla il file alphaTex scaricabile qui a fianco.";
      }
    }, 1500);
  } catch (e) {
    $("#pv-status").textContent = `Errore rendering: ${e.message}`;
  }
}

function pollCurrent() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    if (!state.currentJob) return clearInterval(state.pollTimer);
    try {
      const job = await api(`/api/jobs/${state.currentJob}`);
      if (job.status !== "done") {
        $("#pv-status").textContent = `Stato: ${STAGES_IT[job.status] || job.status} (${job.progress || 0}%)`;
      } else {
        clearInterval(state.pollTimer);
        openPlayer(job.id); // ora renderizza
      }
    } catch (_) { /* noop */ }
  }, 2500);
}

function closePlayer() {
  state.currentJob = null;
  clearInterval(state.pollTimer);
  $("#player-view").classList.add("hidden");
  if (state.atApi && state.atApi.pause) state.atApi.pause();
}

/* ---------- controlli player ---------- */

$("#btn-back").addEventListener("click", closePlayer);
$("#btn-play").addEventListener("click", () => {
  if (!state.atApi) return;
  const st = state.atApi.state;
  if (st === 2) state.atApi.pause();
  else state.atApi.play();
});
$("#btn-stop").addEventListener("click", () => state.atApi && state.atApi.stop());
$("#speed").addEventListener("change", (ev) => {
  if (state.atApi) state.atApi.playbackSpeed = parseFloat(ev.target.value);
});

/* ---------- avvio ---------- */

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && state.currentJob) closePlayer();
});

refreshJobs();
setInterval(refreshJobs, 3000);
