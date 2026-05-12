/* MEXC Scanner — клиентский JS. */
(() => {
  "use strict";

  const state = {
    spot: { rows: [], loaded: false },
    futures: { rows: [], loaded: false },
    active: "spot",
    query: "",
    sort: {
      spot: { key: "volumeQuote", dir: "desc" },
      futures: { key: "amount24", dir: "desc" },
    },
    credentials: false,
  };

  const tbodies = {
    spot: document.getElementById("tbody-spot"),
    futures: document.getElementById("tbody-futures"),
  };
  const panels = {
    spot: document.getElementById("panel-spot"),
    futures: document.getElementById("panel-futures"),
  };
  const tabs = document.querySelectorAll(".tab");
  const statusEl = document.getElementById("status");
  const credEl = document.getElementById("cred-status");
  const searchEl = document.getElementById("search");
  const refreshBtn = document.getElementById("refresh");

  // ---------- форматирование ----------

  function fmtPrice(v) {
    if (v == null || Number.isNaN(v)) return "—";
    const abs = Math.abs(v);
    let digits = 2;
    if (abs > 0 && abs < 0.01) digits = 8;
    else if (abs < 1) digits = 6;
    else if (abs < 100) digits = 4;
    else if (abs < 10000) digits = 2;
    else digits = 2;
    return v.toLocaleString("en-US", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function fmtVolume(v) {
    if (v == null || Number.isNaN(v)) return "—";
    const abs = Math.abs(v);
    if (abs >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return (v / 1e6).toFixed(2) + "M";
    if (abs >= 1e3) return (v / 1e3).toFixed(2) + "K";
    return v.toFixed(2);
  }

  function fmtPct(v) {
    if (v == null || Number.isNaN(v)) return "—";
    const sign = v > 0 ? "+" : "";
    return `${sign}${(v * 100).toFixed(2)}%`;
  }

  function fmtPctDirect(v) {
    // когда значение уже в процентах (например 0.0083 -> 0.83%)
    return fmtPct(v);
  }

  function fmtFunding(v) {
    if (v == null || Number.isNaN(v)) return "—";
    const sign = v > 0 ? "+" : "";
    const pct = (v * 100).toFixed(4);
    return `${sign}${pct}%`;
  }

  function shortAddr(a) {
    if (!a) return "";
    if (a.length <= 14) return a;
    return `${a.slice(0, 8)}…${a.slice(-6)}`;
  }

  function badgeBool(v) {
    if (v === true) return `<span class="badge ok">да</span>`;
    if (v === false) return `<span class="badge no">нет</span>`;
    return `<span class="badge na">N/A</span>`;
  }

  function addrCell(addr) {
    if (!addr) return `<span class="addr empty">—</span>`;
    return (
      `<span class="addr" title="${escapeHtml(addr)}">${escapeHtml(shortAddr(addr))}</span>` +
      `<button class="copy" data-copy="${escapeHtml(addr)}" title="Скопировать">⧉</button>`
    );
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
    );
  }

  // ---------- сортировка / фильтрация ----------

  function sortRows(rows, key, dir) {
    const mul = dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = a[key];
      const vb = b[key];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * mul;
      if (typeof va === "boolean" && typeof vb === "boolean") {
        return (Number(va) - Number(vb)) * mul;
      }
      return String(va).localeCompare(String(vb)) * mul;
    });
  }

  function filterRows(rows, q) {
    if (!q) return rows;
    const needle = q.toLowerCase();
    return rows.filter((r) => {
      return (
        (r.symbol && r.symbol.toLowerCase().includes(needle)) ||
        (r.base && r.base.toLowerCase().includes(needle)) ||
        (r.fullName && r.fullName.toLowerCase().includes(needle)) ||
        (r.displayName && r.displayName.toLowerCase().includes(needle)) ||
        (r.contractAddress && r.contractAddress.toLowerCase().includes(needle))
      );
    });
  }

  // ---------- рендеринг ----------

  function renderSpot() {
    const { rows } = state.spot;
    const { key, dir } = state.sort.spot;
    const view = sortRows(filterRows(rows, state.query), key, dir);
    const tbody = tbodies.spot;
    if (view.length === 0) {
      tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:24px">Нет данных</td></tr>`;
      return;
    }
    const html = view
      .map((r) => {
        const chgCls = r.priceChangePercent == null ? "" : r.priceChangePercent >= 0 ? "pos" : "neg";
        return `<tr>
          <td class="symbol">${escapeHtml(r.symbol)}</td>
          <td>${escapeHtml(r.base)}${r.fullName ? ` <span class="addr">· ${escapeHtml(r.fullName)}</span>` : ""}</td>
          <td class="num">${fmtPrice(r.lastPrice)}</td>
          <td class="num">${fmtPrice(r.bidPrice)}</td>
          <td class="num">${fmtPrice(r.askPrice)}</td>
          <td class="num ${chgCls}">${fmtPctDirect(r.priceChangePercent)}</td>
          <td class="num">${fmtVolume(r.volumeQuote)}</td>
          <td>${badgeBool(r.depositEnable)}</td>
          <td>${badgeBool(r.withdrawEnable)}</td>
          <td>${addrCell(r.contractAddress)}</td>
        </tr>`;
      })
      .join("");
    tbody.innerHTML = html;
  }

  function renderFutures() {
    const { rows } = state.futures;
    const { key, dir } = state.sort.futures;
    const view = sortRows(filterRows(rows, state.query), key, dir);
    const tbody = tbodies.futures;
    if (view.length === 0) {
      tbody.innerHTML = `<tr><td colspan="11" style="text-align:center;color:var(--muted);padding:24px">Нет данных</td></tr>`;
      return;
    }
    const html = view
      .map((r) => {
        const chgCls = r.riseFallRate == null ? "" : r.riseFallRate >= 0 ? "pos" : "neg";
        const fundingCls = r.fundingRate == null ? "" : r.fundingRate >= 0 ? "pos" : "neg";
        return `<tr>
          <td class="symbol">${escapeHtml(r.symbol)}</td>
          <td>${escapeHtml(r.base)}</td>
          <td class="num">${fmtPrice(r.lastPrice)}</td>
          <td class="num">${fmtPrice(r.bidPrice)}</td>
          <td class="num">${fmtPrice(r.askPrice)}</td>
          <td class="num ${fundingCls}">${fmtFunding(r.fundingRate)}</td>
          <td class="num ${chgCls}">${fmtPctDirect(r.riseFallRate)}</td>
          <td class="num">${fmtVolume(r.amount24)}</td>
          <td>${badgeBool(r.depositEnable)}</td>
          <td>${badgeBool(r.withdrawEnable)}</td>
          <td>${addrCell(r.contractAddress)}</td>
        </tr>`;
      })
      .join("");
    tbody.innerHTML = html;
  }

  function render() {
    if (state.active === "spot") renderSpot();
    else renderFutures();
    updateSortHeaders();
  }

  function updateSortHeaders() {
    const panel = panels[state.active];
    const { key, dir } = state.sort[state.active];
    panel.querySelectorAll("thead th").forEach((th) => {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (th.dataset.sort === key) {
        th.classList.add(dir === "asc" ? "sorted-asc" : "sorted-desc");
      }
    });
  }

  // ---------- загрузка данных ----------

  async function fetchTab(tab, { force = false } = {}) {
    if (state[tab].loaded && !force) {
      render();
      return;
    }
    setStatus(`Загрузка данных вкладки «${tab === "spot" ? "Спот" : "Фьючерсы"}»…`);
    try {
      const r = await fetch(`/api/${tab}`, { cache: "no-store" });
      if (!r.ok) {
        const text = await r.text();
        throw new Error(`HTTP ${r.status}: ${text}`);
      }
      const payload = await r.json();
      state[tab].rows = payload.rows || [];
      state[tab].loaded = true;
      state.credentials = !!payload.credentials;
      updateCredentialStatus();
      setStatus(
        `Загружено ${payload.count} строк${
          tab === "spot" ? " (спот)" : " (фьючерсы)"
        }. Обновлено ${new Date().toLocaleTimeString("ru-RU")}.`,
      );
      render();
    } catch (e) {
      setStatus(`Ошибка загрузки: ${e.message}`, true);
    }
  }

  function setStatus(text, isError = false) {
    statusEl.textContent = text;
    statusEl.classList.toggle("error", isError);
  }

  function updateCredentialStatus() {
    if (state.credentials) {
      credEl.textContent = "MEXC API: ключи подключены (депозит/вывод и адреса доступны)";
    } else {
      credEl.textContent =
        "MEXC API: без ключей — статусы депозита/вывода = N/A. Для полной информации задайте MEXC_API_KEY/SECRET.";
    }
  }

  // ---------- обработчики ----------

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => {
        t.classList.toggle("active", t === tab);
        t.setAttribute("aria-selected", t === tab ? "true" : "false");
      });
      const name = tab.dataset.tab;
      state.active = name;
      Object.entries(panels).forEach(([k, el]) => {
        el.classList.toggle("active", k === name);
      });
      fetchTab(name);
    });
  });

  searchEl.addEventListener("input", (e) => {
    state.query = e.target.value.trim();
    render();
  });

  refreshBtn.addEventListener("click", async () => {
    setStatus("Сбрасываем кэш и обновляем…");
    try {
      await fetch("/api/refresh", { method: "POST" });
    } catch (_) {
      // ignore — всё равно перезагрузим
    }
    state.spot.loaded = false;
    state.futures.loaded = false;
    await fetchTab(state.active, { force: true });
  });

  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t && t.classList && t.classList.contains("copy")) {
      const text = t.dataset.copy || "";
      navigator.clipboard
        .writeText(text)
        .then(() => {
          const orig = t.textContent;
          t.textContent = "✓";
          setTimeout(() => (t.textContent = orig), 900);
        })
        .catch(() => {});
    }
  });

  // сортировка по клику на заголовок
  Object.values(panels).forEach((panel) => {
    panel.querySelectorAll("thead th").forEach((th) => {
      const key = th.dataset.sort;
      if (!key) return;
      th.addEventListener("click", () => {
        const tab = state.active;
        const cur = state.sort[tab];
        if (cur.key === key) {
          cur.dir = cur.dir === "asc" ? "desc" : "asc";
        } else {
          cur.key = key;
          cur.dir = th.classList.contains("num") ? "desc" : "asc";
        }
        render();
      });
    });
  });

  // первичная загрузка
  fetchTab("spot");
})();
