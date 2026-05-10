(() => {
  const POLL_INTERVAL_MS = 2000;

  const elements = {
    stateIndicator: document.getElementById("state-indicator"),
    stateLabel: document.getElementById("state-label"),
    lastUpdate: document.getElementById("last-update"),
    refreshInterval: document.getElementById("refresh-interval"),
    btnScan: document.getElementById("btn-scan"),
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
  };

  const state = {
    sortKey: "spread",
    sortDir: "desc",
    rows: [],
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

  function dwClass(symbol) {
    if (symbol === "D+" || symbol === "W+") return "dw-ok";
    if (symbol === "D-" || symbol === "W-") return "dw-bad";
    return "dw-unknown";
  }

  function renderDw(dep, wd) {
    return (
      '<span class="dw-cell">' +
      '<span class="' + dwClass(dep) + '">' + dep + "</span> " +
      '<span class="' + dwClass(wd) + '">' + wd + "</span>" +
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
          ch.chain + contract + " " + wd + "/" + dep + "=" + status +
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
        default: return 0;
      }
    };
    return [...rows].sort((a, b) => {
      const va = getter(a);
      const vb = getter(b);
      if (typeof va === "string") return va.localeCompare(vb) * mul;
      return (va - vb) * mul;
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
          '<td class="pair">' + row.pair + "</td>" +
          "<td>" + row.buy_exchange + "</td>" +
          '<td class="num">' + fmtPrice(row.buy_price) + "</td>" +
          "<td>" + row.sell_exchange + "</td>" +
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
          '<span class="name">' + ex.name + "</span>" +
          '<span class="count">' + ex.pairs_count + " пар · " + ex.dw_count + " D/W</span>" +
          "</div>"
      )
      .join("");
  }

  function renderState(payload) {
    elements.refreshInterval.textContent = payload.refresh_interval
      ? payload.refresh_interval.toFixed(0)
      : "—";

    if (payload.scanning) {
      elements.stateIndicator.className = "indicator indicator--scanning";
      elements.stateLabel.textContent = "Сканирование…";
      elements.btnScan.disabled = true;
      elements.formHint.textContent = "Сканирование уже идёт.";
    } else if (payload.last_error) {
      elements.stateIndicator.className = "indicator indicator--error";
      elements.stateLabel.textContent = "Ошибка";
      elements.btnScan.disabled = false;
      elements.formHint.textContent = payload.last_error;
    } else if (payload.result) {
      elements.stateIndicator.className = "indicator indicator--ok";
      elements.stateLabel.textContent = "Готово";
      elements.btnScan.disabled = false;
      elements.formHint.textContent = "";
    } else {
      elements.stateIndicator.className = "indicator indicator--idle";
      elements.stateLabel.textContent = "Ожидание первого скана…";
      elements.btnScan.disabled = false;
    }

    elements.lastUpdate.textContent = fmtTime(payload.last_finished_at);

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

  async function triggerScan(event) {
    event.preventDefault();
    const body = {};
    if (elements.inputProxy.value !== "") body.proxy = elements.inputProxy.value;
    if (elements.inputMinVolume.value !== "") body.min_volume = Number(elements.inputMinVolume.value);
    if (elements.inputMinSpread.value !== "") body.min_spread = Number(elements.inputMinSpread.value);
    if (elements.inputMaxSpread.value !== "") body.max_spread = Number(elements.inputMaxSpread.value);

    elements.btnScan.disabled = true;
    elements.formHint.textContent = "Запускаю…";

    try {
      const response = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (response.status === 409) {
        elements.formHint.textContent = "Уже идёт сканирование.";
      } else if (!response.ok) {
        const text = await response.text();
        elements.formHint.textContent = "Ошибка: " + text;
      } else {
        elements.formHint.textContent = "Запущено.";
      }
    } catch (error) {
      elements.formHint.textContent = "Ошибка: " + error.message;
    } finally {
      poll();
    }
  }

  function setupSorting() {
    document.querySelectorAll("table.arb th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.sortKey === key) {
          state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        } else {
          state.sortKey = key;
          state.sortDir = key === "pair" || key.endsWith("exchange") ? "asc" : "desc";
        }
        renderRows();
      });
    });
  }

  function setupFilters() {
    elements.filterTransfer.addEventListener("change", renderRows);
    elements.filterPair.addEventListener("input", renderRows);
  }

  function init() {
    setupSorting();
    setupFilters();
    elements.form.addEventListener("submit", triggerScan);
    poll();
    setInterval(poll, POLL_INTERVAL_MS);
  }

  init();
})();
