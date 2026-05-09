"use strict";

const POLL_FALLBACK_MS = 1000;

const els = {
  connectionIndicator: document.getElementById("connection-indicator"),
  connectionLabel: document.getElementById("connection-label"),
  lastUpdate: document.getElementById("last-update"),
  pollInterval: document.getElementById("poll-interval"),
  situations: document.getElementById("situations"),
  situationsCount: document.getElementById("situations-count"),
  tickersBody: document.getElementById("tickers-body"),
  tickersCount: document.getElementById("tickers-count"),
};

const previousPrices = new Map();

function setConnection(state, message) {
  els.connectionIndicator.classList.remove(
    "indicator--idle",
    "indicator--ok",
    "indicator--error",
  );
  if (state === "ok") {
    els.connectionIndicator.classList.add("indicator--ok");
  } else if (state === "error") {
    els.connectionIndicator.classList.add("indicator--error");
  } else {
    els.connectionIndicator.classList.add("indicator--idle");
  }
  els.connectionLabel.textContent = message;
}

function formatPrice(value) {
  if (!Number.isFinite(value)) return "—";
  if (value >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (value >= 1) return value.toFixed(4);
  if (value >= 0.01) return value.toFixed(5);
  return value.toFixed(8);
}

function formatVolume(value) {
  if (!Number.isFinite(value)) return "—";
  if (value >= 1e9) return (value / 1e9).toFixed(2) + "B";
  if (value >= 1e6) return (value / 1e6).toFixed(2) + "M";
  if (value >= 1e3) return (value / 1e3).toFixed(2) + "K";
  return value.toFixed(0);
}

function formatPct(value) {
  if (!Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatTime(epochSeconds) {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return "—";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString();
}

function rangeMeta(price, low, high) {
  const wrap = document.createElement("div");
  wrap.className = "range-meta";
  if (!(Number.isFinite(price) && Number.isFinite(low) && Number.isFinite(high) && high > low)) {
    wrap.textContent = "—";
    return wrap;
  }
  const pct = Math.min(1, Math.max(0, (price - low) / (high - low)));
  const lowEl = document.createElement("span");
  lowEl.textContent = formatPrice(low);
  const bar = document.createElement("span");
  bar.className = "range-bar";
  const marker = document.createElement("span");
  marker.className = "marker";
  marker.style.left = `calc(${pct * 100}% - 1px)`;
  bar.appendChild(marker);
  const highEl = document.createElement("span");
  highEl.textContent = formatPrice(high);
  wrap.appendChild(lowEl);
  wrap.appendChild(bar);
  wrap.appendChild(highEl);
  return wrap;
}

function renderTickers(tickers) {
  els.tickersCount.textContent = tickers.length.toString();
  if (!tickers.length) {
    els.tickersBody.innerHTML = '<tr><td colspan="5" class="empty">No tickers yet…</td></tr>';
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const t of tickers) {
    const tr = document.createElement("tr");
    tr.dataset.symbol = t.symbol;

    const symbolCell = document.createElement("td");
    symbolCell.textContent = t.symbol;
    tr.appendChild(symbolCell);

    const priceCell = document.createElement("td");
    priceCell.className = "num";
    priceCell.textContent = formatPrice(t.price);
    const prev = previousPrices.get(t.symbol);
    if (Number.isFinite(prev) && Number.isFinite(t.price) && prev !== t.price) {
      priceCell.classList.add(t.price > prev ? "flash-up" : "flash-down");
      priceCell.addEventListener(
        "animationend",
        () => priceCell.classList.remove("flash-up", "flash-down"),
        { once: true },
      );
    }
    previousPrices.set(t.symbol, t.price);
    tr.appendChild(priceCell);

    const changeCell = document.createElement("td");
    changeCell.className = `num ${t.change_pct_24h >= 0 ? "pos" : "neg"}`;
    changeCell.textContent = formatPct(t.change_pct_24h);
    tr.appendChild(changeCell);

    const volumeCell = document.createElement("td");
    volumeCell.className = "num";
    volumeCell.textContent = formatVolume(t.quote_volume_24h);
    tr.appendChild(volumeCell);

    const rangeCell = document.createElement("td");
    rangeCell.className = "num";
    rangeCell.appendChild(rangeMeta(t.price, t.low_24h, t.high_24h));
    tr.appendChild(rangeCell);

    fragment.appendChild(tr);
  }
  els.tickersBody.replaceChildren(fragment);
}

function renderSituations(situations) {
  els.situationsCount.textContent = situations.length.toString();
  if (!situations.length) {
    els.situations.innerHTML = '<p class="empty">No situations detected. Watching the market…</p>';
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const s of situations) {
    const item = document.createElement("div");
    item.className = `situation situation--${s.severity}`;
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = s.kind.replace(/_/g, " ");
    const body = document.createElement("div");
    const symbol = document.createElement("div");
    symbol.className = "symbol";
    symbol.textContent = s.symbol;
    const message = document.createElement("div");
    message.className = "message";
    message.textContent = s.message;
    body.appendChild(symbol);
    body.appendChild(message);
    const value = document.createElement("span");
    value.className = "value";
    if (s.kind.startsWith("price_spike")) {
      value.textContent = formatPct(s.value);
    } else if (s.kind === "volume_surge") {
      value.textContent = `${s.value.toFixed(1)}x`;
    } else {
      value.textContent = formatPrice(s.value);
    }
    item.appendChild(kind);
    item.appendChild(body);
    item.appendChild(value);
    fragment.appendChild(item);
  }
  els.situations.replaceChildren(fragment);
}

async function tick() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.error) {
      setConnection("error", `Scanner error: ${data.error}`);
    } else {
      setConnection("ok", "Live");
    }
    if (Number.isFinite(data.poll_interval_seconds)) {
      els.pollInterval.textContent = data.poll_interval_seconds.toFixed(1);
    }
    els.lastUpdate.textContent = formatTime(data.last_updated);
    renderTickers(Array.isArray(data.tickers) ? data.tickers : []);
    renderSituations(Array.isArray(data.situations) ? data.situations : []);
  } catch (err) {
    setConnection("error", `Disconnected: ${err.message ?? err}`);
  }
}

function getPollIntervalMs() {
  const text = els.pollInterval.textContent;
  const seconds = Number.parseFloat(text);
  if (Number.isFinite(seconds) && seconds > 0) {
    return seconds * 1000;
  }
  return POLL_FALLBACK_MS;
}

async function loop() {
  while (true) {
    const start = performance.now();
    await tick();
    const elapsed = performance.now() - start;
    const wait = Math.max(0, getPollIntervalMs() - elapsed);
    await new Promise((resolve) => setTimeout(resolve, wait));
  }
}

loop();
