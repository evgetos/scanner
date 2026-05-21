const EXCHANGE_BOX = document.getElementById("exchange-checkboxes");
const SETTINGS_FORM = document.getElementById("settings-form");
const SETTINGS_STATUS = document.getElementById("settings-status");
const STATUS_BOX = document.getElementById("exchange-status");
const RESULTS_BODY = document.getElementById("results-body");
const RESULT_COUNT = document.getElementById("result-count");
const LAST_SCAN_EL = document.getElementById("last-scan");
const RUN_NOW_BTN = document.getElementById("run-now");

let exchanges = [];
let exchangeLabels = {};
let refreshTimer = null;

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
  });
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
  return value.toLocaleString("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
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
}

async function loadSettings() {
  const s = await api("/api/settings");
  SETTINGS_FORM.elements["min_volume_usdt"].value = s.min_volume_usdt;
  SETTINGS_FORM.elements["min_spread_pct"].value = s.min_spread_pct;
  SETTINGS_FORM.elements["scan_interval_sec"].value = s.scan_interval_sec;
  SETTINGS_FORM.elements["proxy_url"].value = s.proxy_url || "";
  for (const id of exchanges) {
    const cb = SETTINGS_FORM.elements[`exchange:${id}`];
    if (cb) cb.checked = !!(s.exchanges[id] && s.exchanges[id].enabled);
  }
}

SETTINGS_FORM.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    exchanges: Object.fromEntries(
      exchanges.map((id) => [
        id,
        { enabled: !!SETTINGS_FORM.elements[`exchange:${id}`].checked },
      ])
    ),
    min_volume_usdt: parseFloat(SETTINGS_FORM.elements["min_volume_usdt"].value || "0"),
    min_spread_pct: parseFloat(SETTINGS_FORM.elements["min_spread_pct"].value || "0"),
    scan_interval_sec: parseInt(SETTINGS_FORM.elements["scan_interval_sec"].value || "30", 10),
    proxy_url: SETTINGS_FORM.elements["proxy_url"].value.trim() || null,
  };
  SETTINGS_STATUS.textContent = "Сохранение…";
  SETTINGS_STATUS.className = "status-text";
  try {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    SETTINGS_STATUS.textContent = "✓ Сохранено, сканирование перезапущено";
    SETTINGS_STATUS.className = "status-text ok";
    scheduleRefresh(1000);
    setTimeout(() => {
      SETTINGS_STATUS.textContent = "";
      SETTINGS_STATUS.className = "status-text";
    }, 3000);
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
    render(r);
  } catch (err) {
    SETTINGS_STATUS.textContent = `Ошибка сканирования: ${err.message}`;
    SETTINGS_STATUS.className = "status-text err";
  } finally {
    RUN_NOW_BTN.disabled = false;
    RUN_NOW_BTN.textContent = "Сканировать сейчас";
  }
});

function render(result) {
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
    const meta = st.enabled
      ? `${st.ticker_count} пар · ${st.anomaly_count} аном.`
      : "отключено";

    card.innerHTML = `
      <div class="name"><span class="dot ${dotCls}"></span>${label}</div>
      <div class="meta">${meta}</div>
      <div class="meta">${st.last_scan_at ? fmtTimeAgo(st.last_scan_at) : "—"}</div>
      ${st.last_error ? `<div class="error-msg" title="${escapeHtml(st.last_error)}">${escapeHtml(st.last_error)}</div>` : ""}
    `;
    STATUS_BOX.appendChild(card);
  }

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
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[c]);
}

async function refresh() {
  try {
    const r = await api("/api/scan");
    render(r);
  } catch (err) {
    console.error("refresh failed", err);
  }
}

function scheduleRefresh(delay = 0) {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    await refresh();
    scheduleRefresh(5000);
  }, delay);
}

(async () => {
  try {
    await loadExchanges();
    await loadSettings();
    await refresh();
    scheduleRefresh(5000);
  } catch (err) {
    console.error("init failed", err);
    SETTINGS_STATUS.textContent = `Не удалось загрузить: ${err.message}`;
    SETTINGS_STATUS.className = "status-text err";
  }
})();
