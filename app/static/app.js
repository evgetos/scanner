(() => {
  const POLL_INTERVAL_MS = 2000;
  const STATS_POLL_INTERVAL_MS = 5000;
  const LS_PREFIX = "scanner.";

  // Exchange URL builders. Each function takes the coin symbol (e.g. "BTC")
  // and returns a full spot-trading URL for that pair against USDT.
  // The arb table only outputs USDT pairs, so we hardcode the quote.
  const EXCHANGE_URL_BUILDERS = {
    Binance:     (c) => `https://www.binance.com/en/trade/${c}_USDT?type=spot`,
    Bybit:       (c) => `https://www.bybit.com/en-US/trade/spot/${c}/USDT`,
    OKX:         (c) => `https://www.okx.com/trade-spot/${c.toLowerCase()}-usdt`,
    Bitget:      (c) => `https://www.bitget.com/spot/${c}USDT`,
    BingX:       (c) => `https://bingx.com/en-us/spot/${c}USDT/`,
    MEXC:        (c) => `https://www.mexc.com/exchange/${c}_USDT`,
    KuCoin:      (c) => `https://www.kucoin.com/trade/${c}-USDT`,
    Huobi:       (c) => `https://www.htx.com/trade/${c.toLowerCase()}_usdt`,
    Gateio:      (c) => `https://www.gate.io/trade/${c}_USDT`,
    Blofin:      (c) => `https://blofin.com/spot/${c}-USDT`,
    Hyperliquid: (c) => `https://app.hyperliquid.xyz/trade/${c}`,
    XT:          (c) => `https://www.xt.com/en/trade/${c.toLowerCase()}_usdt`,
    Asterdex:    (c) => `https://www.asterdex.com/en/spot/${c}USDT`,
  };

  function exchangeUrl(exchange, coin) {
    if (!exchange || !coin) return null;
    const fn = EXCHANGE_URL_BUILDERS[exchange];
    if (!fn) return null;
    try {
      return fn(coin);
    } catch (err) {
      console.error("URL builder failed", exchange, coin, err);
      return null;
    }
  }

  function exchangeLink(exchange, coin) {
    const url = exchangeUrl(exchange, coin);
    if (!url) {
      return '<span class="ex-link--no-url">' + escapeHtml(exchange) + "</span>";
    }
    return (
      '<a class="ex-link" target="_blank" rel="noopener noreferrer" href="' +
      escapeHtml(url) +
      '">' +
      escapeHtml(exchange) +
      "</a>"
    );
  }

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  const elements = {
    stateIndicator: document.getElementById("state-indicator"),
    stateLabel: document.getElementById("state-label"),
    lastUpdate: document.getElementById("last-update"),
    refreshInterval: document.getElementById("refresh-interval"),
    btnApplyParams: document.getElementById("btn-apply-params"),
    btnPause: document.getElementById("btn-pause"),
    btnScanNow: document.getElementById("btn-scan-now"),
    formHint: document.getElementById("form-hint"),
    inputProxy: document.getElementById("input-proxy"),
    inputMinVolume: document.getElementById("input-min-volume"),
    inputMinSpread: document.getElementById("input-min-spread"),
    inputMaxSpread: document.getElementById("input-max-spread"),
    metricTotal: document.getElementById("metric-total"),
    metricMulti: document.getElementById("metric-multi"),
    metricRows: document.getElementById("metric-rows"),
    metricTransfer: document.getElementById("metric-transfer"),
    metricDuration: document.getElementById("metric-duration"),
    exchangesGrid: document.getElementById("exchanges-grid"),
    arbCount: document.getElementById("arb-count"),
    arbBody: document.getElementById("arb-body"),
    filterTransfer: document.getElementById("filter-transfer"),
    filterPair: document.getElementById("filter-pair"),
    form: document.getElementById("controls"),
    // Tabs
    tabs: document.querySelectorAll(".tab"),
    views: {
      arbitrage: document.getElementById("view-arbitrage"),
      stats: document.getElementById("view-stats"),
    },
    // Stats
    statsMinSpread: document.getElementById("stats-min-spread"),
    statsMinProfit: document.getElementById("stats-min-profit"),
    statsPair: document.getElementById("stats-pair"),
    statsTransfer: document.getElementById("stats-transfer"),
    statsLimit: document.getElementById("stats-limit"),
    statsRefresh: document.getElementById("btn-stats-refresh"),
    statsClear: document.getElementById("btn-stats-clear"),
    statsHint: document.getElementById("stats-hint"),
    statsHistorySize: document.getElementById("stats-history-size"),
    statsCount: document.getElementById("stats-count"),
    statsBody: document.getElementById("stats-body"),
    statsBufferInfo: document.getElementById("stats-buffer-info"),
  };

  const state = {
    sortKey: "spread",
    sortDir: "desc",
    rows: [],
    paused: false,
    currentTab: "arbitrage",
    statsSortKey: "timestamp",
    statsSortDir: "desc",
    statsRows: [],
  };

  function fmtPrice(value) {
    if (value == null || !isFinite(value)) return "—";
    if (value === 0) return "0";
    if (Math.abs(value) < 0.00001) return value.toExponential(4);
    if (Math.abs(value) < 1) return value.toFixed(6);
    if (Math.abs(value) < 100) return value.toFixed(4);
    return value.toFixed(2);
  }

  function fmtUsd(value) {
    if (value == null || !isFinite(value)) return "—";
    if (Math.abs(value) >= 1) return "$" + value.toLocaleString("en-US", { maximumFractionDigits: 0 });
    return "$" + value.toFixed(2);
  }

  function fmtPct(value) {
    if (value == null || !isFinite(value)) return "—";
    return value.toFixed(2) + "%";
  }

  function fmtTime(ts) {
    if (!ts) return "—";
    const date = new Date(ts * 1000);
    return date.toLocaleTimeString();
  }

  function fmtDateTime(ts) {
    if (!ts) return "—";
    const date = new Date(ts * 1000);
    return date.toLocaleString();
  }

  function dwClass(symbol) {
    if (symbol === "D+" || symbol === "W+") return "dw-ok";
    if (symbol === "D-" || symbol === "W-") return "dw-bad";
    return "dw-unknown";
  }

  function renderDw(dep, wd) {
    return (
      '<span class="dw-cell">' +
      '<span class="' + dwClass(dep) + '">' + escapeHtml(dep) + "</span> " +
      '<span class="' + dwClass(wd) + '">' + escapeHtml(wd) + "</span>" +
      "</span>"
    );
  }

  function shortContract(addr) {
    if (!addr) return "";
    if (addr.length <= 10) return addr;
    return addr.slice(0, 6) + ".." + addr.slice(-4);
  }

  function renderChains(chains) {
    if (!chains || chains.length === 0) {
      return '<span class="dw-unknown">нет данных</span>';
    }
    return chains
      .map((ch) => {
        const transferClass = ch.transfer_ok ? "transfer-ok" : "transfer-bad";
        const status = ch.transfer_ok ? "OK" : "X";
        const wd = ch.buy_withdraw ? "W+" : "W-";
        const dep = ch.sell_deposit ? "D+" : "D-";
        const contract = ch.contract ? " (" + shortContract(ch.contract) + ")" : "";
        return (
          '<span class="chain-item ' + transferClass + '">' +
          escapeHtml(ch.chain + contract) + " " + wd + "/" + dep + "=" + status +
          "</span>"
        );
      })
      .join(" ");
  }

  function sortRows(rows, key, dir) {
    const mul = dir === "asc" ? 1 : -1;
    const getter = (row) => {
      switch (key) {
        case "pair": return row.pair;
        case "buy_exchange": return row.buy_exchange;
        case "sell_exchange": return row.sell_exchange;
        case "buy_price": return row.buy_price;
        case "sell_price": return row.sell_price;
        case "spread": return row.spread;
        case "buy_volume": return row.buy_volume;
        case "sell_volume": return row.sell_volume;
        case "ob_volume": return row.orderbook ? row.orderbook.volume_usdt : -1;
        case "ob_profit": return row.orderbook ? row.orderbook.profit_usdt : -1;
        case "timestamp": return row.timestamp;
        default: return 0;
      }
    };
    return [...rows].sort((a, b) => {
      const va = getter(a);
      const vb = getter(b);
      if (typeof va === "string") return va.localeCompare(vb) * mul;
      const na = va == null ? -Infinity : va;
      const nb = vb == null ? -Infinity : vb;
      return (na - nb) * mul;
    });
  }

  function applyFilters(rows) {
    const onlyTransfer = elements.filterTransfer.checked;
    const pairQ = (elements.filterPair.value || "").trim().toUpperCase();
    return rows.filter((row) => {
      if (onlyTransfer && !row.has_transfer) return false;
      if (pairQ && row.pair.indexOf(pairQ) === -1 && row.coin.indexOf(pairQ) === -1) return false;
      return true;
    });
  }

  function renderRows() {
    let rows = applyFilters(state.rows);
    rows = sortRows(rows, state.sortKey, state.sortDir);

    elements.arbCount.textContent = String(rows.length);

    if (rows.length === 0) {
      elements.arbBody.innerHTML =
        '<tr><td colspan="13" class="empty">Нет данных или все строки отфильтрованы.</td></tr>';
      return;
    }

    const html = rows
      .map((row) => {
        const ob = row.orderbook;
        const obVol = ob ? fmtUsd(ob.volume_usdt) : "—";
        const obProfit = ob && ob.volume_usdt > 0 ? fmtUsd(ob.profit_usdt) : "—";
        return (
          "<tr>" +
          '<td class="pair">' + escapeHtml(row.pair) + "</td>" +
          "<td>" + exchangeLink(row.buy_exchange, row.coin) + "</td>" +
          '<td class="num">' + fmtPrice(row.buy_price) + "</td>" +
          "<td>" + exchangeLink(row.sell_exchange, row.coin) + "</td>" +
          '<td class="num">' + fmtPrice(row.sell_price) + "</td>" +
          '<td class="num spread-positive">' + fmtPct(row.spread) + "</td>" +
          "<td>" + renderDw(row.buy_deposit, row.buy_withdraw) + "</td>" +
          "<td>" + renderDw(row.sell_deposit, row.sell_withdraw) + "</td>" +
          '<td class="num">' + fmtUsd(row.buy_volume) + "</td>" +
          '<td class="num">' + fmtUsd(row.sell_volume) + "</td>" +
          '<td class="num">' + obVol + "</td>" +
          '<td class="num">' + obProfit + "</td>" +
          '<td class="chains-cell">' + renderChains(row.common_chains) + "</td>" +
          "</tr>"
        );
      })
      .join("");
    elements.arbBody.innerHTML = html;

    document.querySelectorAll("table.arb th[data-sort]").forEach((th) => {
      th.classList.remove("sort-asc", "sort-desc");
    });
    const activeView = state.currentTab === "arbitrage"
      ? elements.views.arbitrage
      : elements.views.stats;
    activeView.querySelectorAll("table.arb th[data-sort]").forEach((th) => {
      if (th.dataset.sort === state.sortKey) {
        th.classList.add(state.sortDir === "asc" ? "sort-asc" : "sort-desc");
      }
    });
  }

  function renderExchanges(exchanges) {
    if (!exchanges) {
      elements.exchangesGrid.innerHTML = "";
      return;
    }
    elements.exchangesGrid.innerHTML = exchanges
      .map(
        (ex) =>
          '<div class="exchange-cell">' +
          '<span class="name">' + escapeHtml(ex.name) + "</span>" +
          '<span class="count">' + ex.pairs_count + " пар · " + ex.dw_count + " D/W</span>" +
          "</div>"
      )
      .join("");
  }

  function updatePauseButton() {
    if (state.paused) {
      elements.btnPause.classList.add("paused");
      elements.btnPause.textContent = "▶ Возобновить";
      elements.btnPause.title = "Возобновить фоновый цикл сканирования";
    } else {
      elements.btnPause.classList.remove("paused");
      elements.btnPause.textContent = "⏸ Пауза";
      elements.btnPause.title = "Поставить фоновый цикл на паузу";
    }
  }

  function renderState(payload) {
    elements.refreshInterval.textContent = payload.refresh_interval
      ? payload.refresh_interval.toFixed(0)
      : "—";

    state.paused = !!payload.paused;
    updatePauseButton();

    if (payload.scanning) {
      elements.stateIndicator.className = "indicator indicator--scanning";
      elements.stateLabel.textContent = "Сканирование…";
      elements.btnApplyParams.disabled = true;
      elements.btnScanNow.disabled = true;
      elements.formHint.textContent = "Сканирование уже идёт.";
    } else if (state.paused) {
      elements.stateIndicator.className = "indicator indicator--paused";
      elements.stateLabel.textContent = "На паузе";
      elements.btnApplyParams.disabled = false;
      elements.btnScanNow.disabled = false;
      elements.formHint.textContent = "Фоновый цикл остановлен. Ручной скан доступен.";
    } else if (payload.last_error) {
      elements.stateIndicator.className = "indicator indicator--error";
      elements.stateLabel.textContent = "Ошибка";
      elements.btnApplyParams.disabled = false;
      elements.btnScanNow.disabled = false;
      elements.formHint.textContent = payload.last_error;
    } else if (payload.result) {
      elements.stateIndicator.className = "indicator indicator--ok";
      elements.stateLabel.textContent = "Готово";
      elements.btnApplyParams.disabled = false;
      elements.btnScanNow.disabled = false;
      elements.formHint.textContent = "";
    } else {
      elements.stateIndicator.className = "indicator indicator--idle";
      elements.stateLabel.textContent = "Ожидание первого скана…";
      elements.btnApplyParams.disabled = false;
      elements.btnScanNow.disabled = false;
    }

    elements.lastUpdate.textContent = fmtTime(payload.last_finished_at);

    // History size shown both on the badge and in the buffer-info span.
    if (typeof payload.history_size === "number") {
      const limit = payload.history_limit || 1000;
      elements.statsHistorySize.textContent = payload.history_size + " / " + limit;
      elements.statsBufferInfo.textContent = String(limit);
    }

    const result = payload.result;
    if (result) {
      elements.metricTotal.textContent = result.total_pairs.toLocaleString();
      elements.metricMulti.textContent = result.multi_exchange_pairs.toLocaleString();
      elements.metricRows.textContent = result.arbitrage.length.toLocaleString();
      const transferCount = result.arbitrage.filter((r) => r.has_transfer).length;
      elements.metricTransfer.textContent = transferCount.toLocaleString();
      elements.metricDuration.textContent = result.duration_seconds.toFixed(1) + " с";

      renderExchanges(result.exchanges);
      state.rows = result.arbitrage;
      renderRows();
    } else {
      state.rows = [];
      renderRows();
    }
  }

  // ---- Stats view ----

  function sortStatsRows(rows, key, dir) {
    const mul = dir === "asc" ? 1 : -1;
    const getter = (row) => {
      switch (key) {
        case "timestamp": return row.timestamp;
        case "pair": return row.pair;
        case "buy_exchange": return row.buy_exchange;
        case "sell_exchange": return row.sell_exchange;
        case "buy_price": return row.buy_price;
        case "sell_price": return row.sell_price;
        case "spread": return row.spread;
        case "ob_volume": return row.ob_volume_usdt;
        case "ob_profit": return row.ob_profit_usdt;
        default: return 0;
      }
    };
    return [...rows].sort((a, b) => {
      const va = getter(a);
      const vb = getter(b);
      if (typeof va === "string") return va.localeCompare(vb) * mul;
      const na = va == null ? -Infinity : va;
      const nb = vb == null ? -Infinity : vb;
      return (na - nb) * mul;
    });
  }

  function renderStatsRows() {
    const rows = sortStatsRows(state.statsRows, state.statsSortKey, state.statsSortDir);
    elements.statsCount.textContent = String(rows.length);

    if (rows.length === 0) {
      elements.statsBody.innerHTML =
        '<tr><td colspan="10" class="empty">Нет событий, удовлетворяющих фильтру.</td></tr>';
    } else {
      elements.statsBody.innerHTML = rows
        .map((row) => {
          const transfer = row.has_transfer
            ? '<span class="transfer-tag yes">D+/W+</span>'
            : '<span class="transfer-tag no">—</span>';
          const obVol = row.ob_volume_usdt != null ? fmtUsd(row.ob_volume_usdt) : "—";
          const obProfit = row.ob_profit_usdt != null ? fmtUsd(row.ob_profit_usdt) : "—";
          return (
            "<tr>" +
            '<td class="timestamp">' + fmtDateTime(row.timestamp) + "</td>" +
            '<td class="pair">' + escapeHtml(row.pair) + "</td>" +
            "<td>" + exchangeLink(row.buy_exchange, row.coin) + "</td>" +
            '<td class="num">' + fmtPrice(row.buy_price) + "</td>" +
            "<td>" + exchangeLink(row.sell_exchange, row.coin) + "</td>" +
            '<td class="num">' + fmtPrice(row.sell_price) + "</td>" +
            '<td class="num spread-positive">' + fmtPct(row.spread) + "</td>" +
            '<td class="num">' + obVol + "</td>" +
            '<td class="num">' + obProfit + "</td>" +
            "<td>" + transfer + "</td>" +
            "</tr>"
          );
        })
        .join("");
    }

    elements.views.stats.querySelectorAll("table.arb th[data-sort]").forEach((th) => {
      th.classList.remove("sort-asc", "sort-desc");
      if (th.dataset.sort === state.statsSortKey) {
        th.classList.add(state.statsSortDir === "asc" ? "sort-asc" : "sort-desc");
      }
    });
  }

  async function fetchStats() {
    const minSpread = parseFloat(elements.statsMinSpread.value) || 0;
    const minProfit = parseFloat(elements.statsMinProfit.value) || 0;
    const limit = Math.max(10, parseInt(elements.statsLimit.value, 10) || 200);
    const pair = (elements.statsPair.value || "").trim();
    const requireTransfer = elements.statsTransfer.checked;

    // Persist threshold settings.
    setLs("stats.minSpread", String(minSpread));
    setLs("stats.minProfit", String(minProfit));
    setLs("stats.limit", String(limit));
    setLs("stats.pair", pair);
    setLs("stats.transfer", requireTransfer ? "1" : "0");

    const params = new URLSearchParams({
      min_spread: String(minSpread),
      min_profit: String(minProfit),
      limit: String(limit),
      require_transfer: requireTransfer ? "true" : "false",
    });
    if (pair) params.set("pair", pair);

    try {
      const response = await fetch("/api/stats?" + params.toString());
      if (!response.ok) throw new Error("HTTP " + response.status);
      const payload = await response.json();
      state.statsRows = payload.events || [];
      renderStatsRows();
      elements.statsHistorySize.textContent =
        payload.total_in_history + " / " + payload.history_limit;
      elements.statsBufferInfo.textContent = String(payload.history_limit);
      elements.statsHint.textContent = "Обновлено " + new Date().toLocaleTimeString();
    } catch (error) {
      console.error("fetchStats failed", error);
      elements.statsHint.textContent = "Ошибка: " + error.message;
    }
  }

  async function clearStats() {
    if (!confirm("Удалить всю историю арбитражных событий?")) return;
    try {
      const response = await fetch("/api/stats", { method: "DELETE" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      state.statsRows = [];
      renderStatsRows();
      elements.statsHint.textContent = "История очищена.";
    } catch (error) {
      console.error("clearStats failed", error);
      elements.statsHint.textContent = "Ошибка: " + error.message;
    }
  }

  // ---- Polling ----

  async function poll() {
    try {
      const response = await fetch("/api/state");
      if (!response.ok) throw new Error("HTTP " + response.status);
      const payload = await response.json();
      renderState(payload);
    } catch (error) {
      elements.stateIndicator.className = "indicator indicator--error";
      elements.stateLabel.textContent = "Нет связи с сервером";
      console.error(error);
    }
  }

  async function pollStatsIfActive() {
    if (state.currentTab === "stats") {
      await fetchStats();
    }
  }

  // ---- Scan actions ----

  function readScanOverrides() {
    const body = {};
    if (elements.inputProxy.value !== "") body.proxy = elements.inputProxy.value;
    if (elements.inputMinVolume.value !== "") body.min_volume = Number(elements.inputMinVolume.value);
    if (elements.inputMinSpread.value !== "") body.min_spread = Number(elements.inputMinSpread.value);
    if (elements.inputMaxSpread.value !== "") body.max_spread = Number(elements.inputMaxSpread.value);
    return body;
  }

  async function triggerScan(event) {
    if (event) event.preventDefault();
    const body = readScanOverrides();

    elements.btnApplyParams.disabled = true;
    elements.btnScanNow.disabled = true;
    elements.formHint.textContent = "Запуск…";

    try {
      const response = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error("HTTP " + response.status + ": " + text);
      }
      elements.formHint.textContent = "Скан запущен.";
    } catch (error) {
      elements.formHint.textContent = "Ошибка: " + error.message;
      console.error(error);
    } finally {
      // poll() will re-enable buttons once scanning=false.
      poll();
    }
  }

  async function togglePause() {
    const endpoint = state.paused ? "/api/resume" : "/api/pause";
    elements.btnPause.disabled = true;
    try {
      const response = await fetch(endpoint, { method: "POST" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const payload = await response.json();
      state.paused = !!payload.paused;
      updatePauseButton();
    } catch (error) {
      console.error("Pause toggle failed", error);
    } finally {
      elements.btnPause.disabled = false;
      poll();
    }
  }

  // ---- Tabs ----

  function switchTab(tab) {
    state.currentTab = tab;
    elements.tabs.forEach((t) => {
      t.classList.toggle("tab--active", t.dataset.tab === tab);
    });
    elements.views.arbitrage.classList.toggle("view--active", tab === "arbitrage");
    elements.views.stats.classList.toggle("view--active", tab === "stats");
    setLs("ui.tab", tab);
    if (tab === "stats") fetchStats();
  }

  // ---- Collapsible blocks ----

  function setupCollapse(card) {
    const key = card.dataset.collapseKey;
    const toggle = card.querySelector(".collapse-toggle");
    const initial = getLs("collapse." + key, "0") === "1";
    if (initial) card.classList.add("collapsed");
    toggle.setAttribute("aria-expanded", String(!initial));
    toggle.addEventListener("click", () => {
      const collapsed = card.classList.toggle("collapsed");
      toggle.setAttribute("aria-expanded", String(!collapsed));
      setLs("collapse." + key, collapsed ? "1" : "0");
    });
  }

  // ---- localStorage helpers ----

  function setLs(key, value) {
    try {
      localStorage.setItem(LS_PREFIX + key, value);
    } catch (err) { /* ignore */ }
  }

  function getLs(key, fallback) {
    try {
      const v = localStorage.getItem(LS_PREFIX + key);
      return v == null ? fallback : v;
    } catch (err) {
      return fallback;
    }
  }

  // ---- Init ----

  function setupSortHandlers() {
    document.querySelectorAll("#view-arbitrage table.arb th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.sortKey === key) {
          state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        } else {
          state.sortKey = key;
          state.sortDir = "desc";
        }
        renderRows();
      });
    });
    document.querySelectorAll("#view-stats table.arb th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.statsSortKey === key) {
          state.statsSortDir = state.statsSortDir === "asc" ? "desc" : "asc";
        } else {
          state.statsSortKey = key;
          state.statsSortDir = "desc";
        }
        renderStatsRows();
      });
    });
  }

  function restoreStatsForm() {
    const minSpread = getLs("stats.minSpread", "2");
    const minProfit = getLs("stats.minProfit", "20");
    const limit = getLs("stats.limit", "200");
    const pair = getLs("stats.pair", "");
    const transfer = getLs("stats.transfer", "0") === "1";
    elements.statsMinSpread.value = minSpread;
    elements.statsMinProfit.value = minProfit;
    elements.statsLimit.value = limit;
    elements.statsPair.value = pair;
    elements.statsTransfer.checked = transfer;
  }

  function init() {
    elements.form.addEventListener("submit", triggerScan);
    elements.filterTransfer.addEventListener("change", renderRows);
    elements.filterPair.addEventListener("input", renderRows);
    elements.btnPause.addEventListener("click", togglePause);
    elements.btnScanNow.addEventListener("click", () => triggerScan(null));

    // Tabs
    elements.tabs.forEach((t) => {
      t.addEventListener("click", () => switchTab(t.dataset.tab));
    });

    // Collapsibles
    document.querySelectorAll(".collapsible").forEach(setupCollapse);

    // Sort handlers (both tables)
    setupSortHandlers();

    // Stats controls
    restoreStatsForm();
    elements.statsMinSpread.addEventListener("change", fetchStats);
    elements.statsMinProfit.addEventListener("change", fetchStats);
    elements.statsLimit.addEventListener("change", fetchStats);
    elements.statsPair.addEventListener("input", debounce(fetchStats, 400));
    elements.statsTransfer.addEventListener("change", fetchStats);
    elements.statsRefresh.addEventListener("click", fetchStats);
    elements.statsClear.addEventListener("click", clearStats);

    // Restore tab.
    const savedTab = getLs("ui.tab", "arbitrage");
    switchTab(savedTab === "stats" ? "stats" : "arbitrage");

    // Polling loops.
    poll();
    setInterval(poll, POLL_INTERVAL_MS);
    setInterval(pollStatsIfActive, STATS_POLL_INTERVAL_MS);
  }

  function debounce(fn, ms) {
    let timer = null;
    return function () {
      const args = arguments;
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(null, args), ms);
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
