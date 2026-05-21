const EXCHANGE_BOX = document.getElementById("exchange-checkboxes");
const SETTINGS_FORM = document.getElementById("settings-form");
const SETTINGS_STATUS = document.getElementById("settings-status");
const STATUS_BOX = document.getElementById("exchange-status");
const RESULTS_BODY = document.getElementById("results-body");
const RESULT_COUNT = document.getElementById("result-count");
const LAST_SCAN_EL = document.getElementById("last-scan");
const RUN_NOW_BTN = document.getElementById("run-now");
const TEST_SOUND_BTN = document.getElementById("test-sound");

const HISTORY_FORM = document.getElementById("history-form");
const HISTORY_BODY = document.getElementById("history-body");
const HISTORY_COUNT = document.getElementById("history-count");
const HISTORY_PAGE = document.getElementById("history-page");
const HISTORY_PREV = document.getElementById("history-prev");
const HISTORY_NEXT = document.getElementById("history-next");
const HISTORY_RESET = document.getElementById("history-reset");
const HISTORY_STATUS = document.getElementById("history-status");

const POLL_INTERVAL_MS = 1000;
const HISTORY_REFRESH_MS = 5000;

let exchanges = [];
let exchangeLabels = {};
let refreshTimer = null;
let historyTimer = null;
let historyOffset = 0;
let historyTotal = 0;
let historyLastQuery = null;

// --- Tabs -------------------------------------------------------------------

const VIEWS = { scanner: document.getElementById("scanner-view"), history: document.getElementById("history-view") };
let activeTab = "scanner";

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

function switchTab(name) {
  if (!VIEWS[name]) return;
  activeTab = name;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  Object.entries(VIEWS).forEach(([n, el]) => el.classList.toggle("active", n === name));
  if (name === "history") {
    historyOffset = 0;
    loadHistory();
    if (historyTimer) clearInterval(historyTimer);
    historyTimer = setInterval(() => { if (activeTab === "history") loadHistory(); }, HISTORY_REFRESH_MS);
  } else {
    if (historyTimer) { clearInterval(historyTimer); historyTimer = null; }
  }
}

// --- Anomaly alert state ----------------------------------------------------

let prevAlertKeys = null;
let prevThreshold = null;

const AudioCtor = window.AudioContext || window.webkitAudioContext;
const audioCtx = AudioCtor ? new AudioCtor() : null;
let audioUnlocked = false;

function unlockAudio() {
  if (audioUnlocked || !audioCtx) return;
  if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
  audioUnlocked = true;
}
document.addEventListener("pointerdown", unlockAudio, { once: true });
document.addEventListener("keydown", unlockAudio, { once: true });

function playAlertSound() {
  if (!audioCtx || audioCtx.state === "suspended") return;
  const now = audioCtx.currentTime;
  const beep = (freq, start, dur) => {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0, now + start);
    gain.gain.linearRampToValueAtTime(0.2, now + start + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.001, now + start + dur);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(now + start);
    osc.stop(now + start + dur + 0.02);
  };
  beep(880, 0, 0.18);
  beep(1320, 0.2, 0.22);
}

// --- API helpers ------------------------------------------------------------

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "content-type": "application/json" }, ...options });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json();
}

function fmtNumber(value, opts = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const { maxFractionDigits = 6 } = opts;
  const abs = Math.abs(value);
  let digits;
  if (abs >= 1000) digits = 2;
  else if (abs >= 1) digits = 4;
  else if (abs >= 0.01) digits = 5;
  else digits = maxFractionDigits;
  return value.toLocaleString("ru-RU", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

function fmtMoney(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value >= 1e9) return (value / 1e9).toFixed(2) + " B";
  if (value >= 1e6) return (value / 1e6).toFixed(2) + " M";
  if (value >= 1e3) return (value / 1e3).toFixed(2) + " K";
  return value.toFixed(2);
}

function fmtTimeAgo(ts) {
  if (!ts) return "—";
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 5) return "только что";
  if (diff < 60) return `${diff} сек назад`;
  const m = Math.floor(diff / 60);
  if (m < 60) return `${m} мин назад`;
  const h = Math.floor(m / 60);
  return `${h} ч назад`;
}

function fmtDateTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}

function fmtDuration(sec) {
  if (sec === null || sec === undefined) return "—";
  if (sec < 60) return `${sec.toFixed(1)} с`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec - m * 60);
  if (m < 60) return `${m}м ${s}с`;
  const h = Math.floor(m / 60);
  return `${h}ч ${m - h * 60}м`;
}

// --- Scanner tab init / settings --------------------------------------------

async function loadExchanges() {
  const data = await api("/api/exchanges");
  exchanges = data.map((e) => e.id);
  exchangeLabels = Object.fromEntries(data.map((e) => [e.id, e.label]));
  EXCHANGE_BOX.innerHTML = "";
  for (const ex of data) {
    const label = document.createElement("label");
    label.innerHTML = `<input type="checkbox" name="exchange:${ex.id}" /> <span>${ex.label}</span>`;
    EXCHANGE_BOX.appendChild(label);
  }
  // populate history exchange dropdown
  const sel = HISTORY_FORM.elements["exchange"];
  while (sel.options.length > 1) sel.remove(1);
  for (const ex of data) {
    const opt = document.createElement("option");
    opt.value = ex.id; opt.textContent = ex.label;
    sel.appendChild(opt);
  }
}

async function loadSettings() {
  const s = await api("/api/settings");
  SETTINGS_FORM.elements["min_volume_usdt"].value = s.min_volume_usdt;
  SETTINGS_FORM.elements["min_spread_pct"].value = s.min_spread_pct;
  SETTINGS_FORM.elements["close_threshold_pct"].value = s.close_threshold_pct;
  SETTINGS_FORM.elements["scan_interval_sec"].value = s.scan_interval_sec;
  SETTINGS_FORM.elements["proxy_url"].value = s.proxy_url || "";
  SETTINGS_FORM.elements["sound_enabled"].checked = !!s.sound_enabled;
  SETTINGS_FORM.elements["sound_threshold_pct"].value = s.sound_threshold_pct;
  for (const id of exchanges) {
    const cb = SETTINGS_FORM.elements[`exchange:${id}`];
    if (cb) cb.checked = !!(s.exchanges[id] && s.exchanges[id].enabled);
  }
}

SETTINGS_FORM.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    exchanges: Object.fromEntries(
      exchanges.map((id) => [id, { enabled: !!SETTINGS_FORM.elements[`exchange:${id}`].checked }])
    ),
    min_volume_usdt: parseFloat(SETTINGS_FORM.elements["min_volume_usdt"].value || "0"),
    min_spread_pct: parseFloat(SETTINGS_FORM.elements["min_spread_pct"].value || "0"),
    close_threshold_pct: parseFloat(SETTINGS_FORM.elements["close_threshold_pct"].value || "0"),
    scan_interval_sec: parseInt(SETTINGS_FORM.elements["scan_interval_sec"].value || "1", 10),
    proxy_url: SETTINGS_FORM.elements["proxy_url"].value.trim() || null,
    sound_enabled: !!SETTINGS_FORM.elements["sound_enabled"].checked,
    sound_threshold_pct: parseFloat(SETTINGS_FORM.elements["sound_threshold_pct"].value || "1"),
  };
  SETTINGS_STATUS.textContent = "Сохранение…";
  SETTINGS_STATUS.className = "status-text";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    SETTINGS_STATUS.textContent = "✓ Сохранено, сканирование перезапущено";
    SETTINGS_STATUS.className = "status-text ok";
    scheduleRefresh(500);
    setTimeout(() => { SETTINGS_STATUS.textContent = ""; SETTINGS_STATUS.className = "status-text"; }, 3000);
  } catch (err) {
    SETTINGS_STATUS.textContent = `Ошибка: ${err.message}`;
    SETTINGS_STATUS.className = "status-text err";
  }
});

RUN_NOW_BTN.addEventListener("click", async () => {
  RUN_NOW_BTN.disabled = true;
  RUN_NOW_BTN.textContent = "Сканирование…";
  try {
    const r = await api("/api/scan/run", { method: "POST" });
    renderScan(r);
  } catch (err) {
    SETTINGS_STATUS.textContent = `Ошибка сканирования: ${err.message}`;
    SETTINGS_STATUS.className = "status-text err";
  } finally {
    RUN_NOW_BTN.disabled = false;
    RUN_NOW_BTN.textContent = "Сканировать сейчас";
  }
});

TEST_SOUND_BTN.addEventListener("click", () => { unlockAudio(); playAlertSound(); });

// --- Scanner alert + render -------------------------------------------------

function checkAlertTransitions(result) {
  const enabled = !!SETTINGS_FORM.elements["sound_enabled"].checked;
  if (!enabled) { prevAlertKeys = null; return new Set(); }
  const threshold = parseFloat(SETTINGS_FORM.elements["sound_threshold_pct"].value || "1");
  const current = new Set();
  for (const a of result.anomalies) {
    if (Math.abs(a.spread_pct) >= threshold) current.add(`${a.exchange}:${a.symbol}`);
  }
  if (threshold !== prevThreshold) prevAlertKeys = null;
  prevThreshold = threshold;

  const newlyTriggered = new Set();
  if (prevAlertKeys !== null) {
    for (const k of current) if (!prevAlertKeys.has(k)) newlyTriggered.add(k);
  }
  prevAlertKeys = current;
  if (newlyTriggered.size > 0) playAlertSound();
  return newlyTriggered;
}

function renderScan(result) {
  LAST_SCAN_EL.textContent = result.last_scan_at
    ? `Последнее сканирование: ${fmtTimeAgo(result.last_scan_at)}`
    : "Сканирование ещё не запускалось";

  STATUS_BOX.innerHTML = "";
  for (const st of result.exchange_status) {
    const card = document.createElement("div");
    let cls = "exchange-card";
    if (!st.enabled) cls += " disabled";
    else if (st.last_error) cls += " error";
    card.className = cls;
    let dotCls = "off";
    if (st.enabled) {
      if (st.last_error) dotCls = "err";
      else if (st.last_scan_at) dotCls = "ok";
      else dotCls = "warn";
    }
    const label = exchangeLabels[st.name] || st.name;
    const meta = st.enabled ? `${st.ticker_count} пар · ${st.anomaly_count} аном.` : "отключено";
    card.innerHTML = `
      <div class="name"><span class="dot ${dotCls}"></span>${label}</div>
      <div class="meta">${meta}</div>
      <div class="meta">${st.last_scan_at ? fmtTimeAgo(st.last_scan_at) : "—"}</div>
      ${st.last_error ? `<div class="error-msg" title="${escapeHtml(st.last_error)}">${escapeHtml(st.last_error)}</div>` : ""}
    `;
    STATUS_BOX.appendChild(card);
  }

  const newlyTriggered = checkAlertTransitions(result);

  RESULT_COUNT.textContent = `${result.anomalies.length} строк`;
  RESULTS_BODY.innerHTML = "";
  if (result.anomalies.length === 0) {
    const tr = document.createElement("tr");
    tr.className = "empty";
    tr.innerHTML = `<td colspan="6">Нет расхождений выше порога. Снизьте мин. спред или объём.</td>`;
    RESULTS_BODY.appendChild(tr);
    return;
  }
  for (const a of result.anomalies) {
    const tr = document.createElement("tr");
    const key = `${a.exchange}:${a.symbol}`;
    if (newlyTriggered.has(key)) tr.className = "alert-new";
    const spreadCls = a.spread_pct >= 0 ? "up" : "down";
    tr.innerHTML = `
      <td><span class="ex-tag">${escapeHtml(exchangeLabels[a.exchange] || a.exchange)}</span></td>
      <td>${escapeHtml(a.symbol)}</td>
      <td class="num">${fmtNumber(a.last_price)}</td>
      <td class="num">${fmtNumber(a.fair_price)}</td>
      <td class="num"><span class="badge ${spreadCls}">${a.spread_pct >= 0 ? "+" : ""}${a.spread_pct.toFixed(3)}%</span></td>
      <td class="num">${fmtMoney(a.volume_24h_usdt)}</td>
    `;
    RESULTS_BODY.appendChild(tr);
  }
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

async function refreshScan() {
  try { renderScan(await api("/api/scan")); }
  catch (err) { console.error("refresh failed", err); }
}

function scheduleRefresh(delay = 0) {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    if (activeTab === "scanner") await refreshScan();
    scheduleRefresh(POLL_INTERVAL_MS);
  }, delay);
}

// --- History tab ------------------------------------------------------------

function dtLocalToUnix(value) {
  if (!value) return null;
  // <input type="datetime-local"> gives local time without TZ; treat as UTC.
  const d = new Date(value + "Z");
  return Number.isNaN(d.getTime()) ? null : d.getTime() / 1000;
}

function buildHistoryQuery() {
  const ex = HISTORY_FORM.elements["exchange"].value;
  const sym = HISTORY_FORM.elements["symbol"].value.trim();
  const status = HISTORY_FORM.elements["status"].value;
  const from_ts = dtLocalToUnix(HISTORY_FORM.elements["from"].value);
  const to_ts = dtLocalToUnix(HISTORY_FORM.elements["to"].value);
  const min_abs = HISTORY_FORM.elements["min_abs_spread"].value;
  const limit = parseInt(HISTORY_FORM.elements["limit"].value || "50", 10);
  const params = new URLSearchParams();
  if (ex) params.set("exchange", ex);
  if (sym) params.set("symbol", sym);
  if (status) params.set("status", status);
  if (from_ts !== null) params.set("from_ts", from_ts);
  if (to_ts !== null) params.set("to_ts", to_ts);
  if (min_abs) params.set("min_abs_spread", parseFloat(min_abs));
  params.set("limit", limit);
  params.set("offset", historyOffset);
  return { params, limit };
}

async function loadHistory() {
  const { params, limit } = buildHistoryQuery();
  historyLastQuery = params.toString();
  HISTORY_STATUS.textContent = "Загрузка…";
  HISTORY_STATUS.className = "status-text";
  try {
    const page = await api(`/api/history?${params.toString()}`);
    historyTotal = page.total;
    renderHistory(page);
    HISTORY_STATUS.textContent = "";
  } catch (err) {
    HISTORY_STATUS.textContent = `Ошибка: ${err.message}`;
    HISTORY_STATUS.className = "status-text err";
  }
  HISTORY_PREV.disabled = historyOffset <= 0;
  HISTORY_NEXT.disabled = historyOffset + limit >= historyTotal;
  HISTORY_PAGE.textContent = historyTotal === 0
    ? "0 записей"
    : `${historyOffset + 1}–${Math.min(historyOffset + limit, historyTotal)} из ${historyTotal}`;
}

function renderHistory(page) {
  HISTORY_COUNT.textContent = `${page.total} ${pluralRu(page.total, ["запись", "записи", "записей"])}`;
  HISTORY_BODY.innerHTML = "";
  if (page.items.length === 0) {
    const tr = document.createElement("tr");
    tr.className = "empty";
    tr.innerHTML = `<td colspan="9">Нет записей по выбранным фильтрам.</td>`;
    HISTORY_BODY.appendChild(tr);
    return;
  }
  for (const s of page.items) {
    const tr = document.createElement("tr");
    const isOpen = s.closed_at === null;
    const openSpreadCls = s.open_spread_pct >= 0 ? "up" : "down";
    const peakCls = s.max_spread_pct >= 0 ? "up" : "down";
    const closeCell = isOpen
      ? `<span class="badge open">OPEN</span>`
      : escapeHtml(fmtDateTime(s.closed_at));
    const closeSpread = s.close_spread_pct === null
      ? "—"
      : `<span class="badge ${s.close_spread_pct >= 0 ? "up" : "down"}">${s.close_spread_pct >= 0 ? "+" : ""}${s.close_spread_pct.toFixed(3)}%</span>`;
    tr.innerHTML = `
      <td>${escapeHtml(fmtDateTime(s.opened_at))}</td>
      <td>${closeCell}</td>
      <td class="num">${fmtDuration(s.duration_sec)}</td>
      <td><span class="ex-tag">${escapeHtml(exchangeLabels[s.exchange] || s.exchange)}</span></td>
      <td>${escapeHtml(s.symbol)}</td>
      <td class="num"><span class="badge ${openSpreadCls}">${s.open_spread_pct >= 0 ? "+" : ""}${s.open_spread_pct.toFixed(3)}%</span></td>
      <td class="num"><span class="badge ${peakCls}">${s.max_spread_pct >= 0 ? "+" : ""}${s.max_spread_pct.toFixed(3)}%</span></td>
      <td class="num">${closeSpread}</td>
      <td class="num">${fmtMoney(s.open_volume_usdt)}</td>
    `;
    HISTORY_BODY.appendChild(tr);
  }
}

function pluralRu(n, [one, few, many]) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

HISTORY_FORM.addEventListener("submit", (e) => {
  e.preventDefault();
  historyOffset = 0;
  loadHistory();
});

HISTORY_RESET.addEventListener("click", () => {
  HISTORY_FORM.reset();
  historyOffset = 0;
  loadHistory();
});

HISTORY_PREV.addEventListener("click", () => {
  const limit = parseInt(HISTORY_FORM.elements["limit"].value || "50", 10);
  historyOffset = Math.max(0, historyOffset - limit);
  loadHistory();
});

HISTORY_NEXT.addEventListener("click", () => {
  const limit = parseInt(HISTORY_FORM.elements["limit"].value || "50", 10);
  if (historyOffset + limit < historyTotal) {
    historyOffset += limit;
    loadHistory();
  }
});

// --- Bootstrap --------------------------------------------------------------

(async () => {
  try {
    await loadExchanges();
    await loadSettings();
    await refreshScan();
    scheduleRefresh(POLL_INTERVAL_MS);
  } catch (err) {
    console.error("init failed", err);
    SETTINGS_STATUS.textContent = `Не удалось загрузить: ${err.message}`;
    SETTINGS_STATUS.className = "status-text err";
  }
})();
