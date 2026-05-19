/* global state */
let allResults = [];
let filteredResults = [];
let favorites = JSON.parse(localStorage.getItem("favorites") || "[]");
let sortColumn = "volume_usd";
let sortDirection = "desc";

/* ---------- init ---------- */
document.addEventListener("DOMContentLoaded", () => {
    loadSettings();
    loadExchanges();
    renderFavorites();
    setupSortHandlers();
});

/* ---------- exchanges ---------- */
async function loadExchanges() {
    try {
        const resp = await fetch("/api/exchanges");
        const exchanges = await resp.json();
        const container = document.getElementById("exchanges-list");
        container.innerHTML = "";

        const saved = JSON.parse(localStorage.getItem("enabledExchanges") || "null");

        exchanges.forEach((ex) => {
            const label = document.createElement("label");
            label.className = "checkbox-label";

            const cb = document.createElement("input");
            cb.type = "checkbox";
            cb.dataset.exchange = ex.id;
            cb.checked = saved ? saved.includes(ex.id) : true;

            const features = [];
            if (ex.spot) features.push("спот");
            if (ex.futures) features.push("фьюч");

            label.appendChild(cb);
            label.appendChild(
                document.createTextNode(` ${ex.name} (${features.join(", ")})`)
            );
            container.appendChild(label);
        });
    } catch (e) {
        console.error("Failed to load exchanges:", e);
    }
}

/* ---------- settings ---------- */
function loadSettings() {
    const s = JSON.parse(localStorage.getItem("scanSettings") || "{}");
    if (s.min_volume_spot != null) document.getElementById("min-volume-spot").value = s.min_volume_spot;
    if (s.min_volume_futures != null) document.getElementById("min-volume-futures").value = s.min_volume_futures;
    if (s.max_distance_pct != null) document.getElementById("max-distance").value = s.max_distance_pct;
    if (s.min_density_usd != null) document.getElementById("min-density").value = s.min_density_usd;
    if (s.max_symbols != null) document.getElementById("max-symbols").value = s.max_symbols;
    if (s.market_spot != null) document.getElementById("market-spot").checked = s.market_spot;
    if (s.market_futures != null) document.getElementById("market-futures").checked = s.market_futures;
}

function saveSettings() {
    const s = {
        min_volume_spot: +document.getElementById("min-volume-spot").value,
        min_volume_futures: +document.getElementById("min-volume-futures").value,
        max_distance_pct: +document.getElementById("max-distance").value,
        min_density_usd: +document.getElementById("min-density").value,
        max_symbols: +document.getElementById("max-symbols").value,
        market_spot: document.getElementById("market-spot").checked,
        market_futures: document.getElementById("market-futures").checked,
    };
    localStorage.setItem("scanSettings", JSON.stringify(s));

    const enabled = [];
    document.querySelectorAll("#exchanges-list input[type=checkbox]").forEach((cb) => {
        if (cb.checked) enabled.push(cb.dataset.exchange);
    });
    localStorage.setItem("enabledExchanges", JSON.stringify(enabled));
}

function getSettings() {
    const marketTypes = [];
    if (document.getElementById("market-spot").checked) marketTypes.push("spot");
    if (document.getElementById("market-futures").checked) marketTypes.push("futures");

    const enabledExchanges = [];
    document.querySelectorAll("#exchanges-list input[type=checkbox]").forEach((cb) => {
        if (cb.checked) enabledExchanges.push(cb.dataset.exchange);
    });

    return {
        min_volume_spot: +document.getElementById("min-volume-spot").value,
        min_volume_futures: +document.getElementById("min-volume-futures").value,
        max_distance_pct: +document.getElementById("max-distance").value,
        min_density_usd: +document.getElementById("min-density").value,
        max_symbols_per_exchange: +document.getElementById("max-symbols").value,
        enabled_exchanges: enabledExchanges,
        market_types: marketTypes,
        favorites: favorites,
    };
}

/* ---------- scanning ---------- */
async function startScan() {
    const btn = document.getElementById("btn-scan");
    const statusText = document.getElementById("status-text");
    const overlay = document.getElementById("loading-overlay");

    btn.disabled = true;
    statusText.textContent = "Сканирование...";
    statusText.className = "status-scanning";
    overlay.classList.remove("hidden");

    saveSettings();
    const settings = getSettings();

    try {
        const resp = await fetch("/api/scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(settings),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        allResults = await resp.json();

        const now = new Date().toLocaleTimeString("ru-RU");
        document.getElementById("last-update").textContent = `Обновлено: ${now}`;
        statusText.textContent = "Готово";
        statusText.className = "status-done";

        filterResults();
    } catch (e) {
        console.error("Scan failed:", e);
        statusText.textContent = "Ошибка";
        statusText.className = "status-error";
    } finally {
        btn.disabled = false;
        overlay.classList.add("hidden");
    }
}

/* ---------- filtering ---------- */
function filterResults() {
    const search = document.getElementById("search-input").value.toUpperCase().trim();
    const favOnly = document.getElementById("show-favorites-only").checked;

    filteredResults = allResults.filter((r) => {
        if (search && !r.symbol.toUpperCase().includes(search)) return false;
        if (favOnly && !r.is_favorite) return false;
        return true;
    });

    sortResults();
    renderResults();
}

/* ---------- sorting ---------- */
function setupSortHandlers() {
    document.querySelectorAll("th.sortable").forEach((th) => {
        th.addEventListener("click", () => {
            const col = th.dataset.sort;
            if (sortColumn === col) {
                sortDirection = sortDirection === "asc" ? "desc" : "asc";
            } else {
                sortColumn = col;
                sortDirection = "desc";
            }

            document.querySelectorAll("th.sortable").forEach((t) => {
                t.classList.remove("active-sort", "asc", "desc");
            });
            th.classList.add("active-sort", sortDirection);

            sortResults();
            renderResults();
        });
    });
}

function sortResults() {
    filteredResults.sort((a, b) => {
        let va = a[sortColumn];
        let vb = b[sortColumn];

        if (typeof va === "string") va = va.toLowerCase();
        if (typeof vb === "string") vb = vb.toLowerCase();
        if (typeof va === "boolean") { va = va ? 1 : 0; vb = vb ? 1 : 0; }

        if (va < vb) return sortDirection === "asc" ? -1 : 1;
        if (va > vb) return sortDirection === "asc" ? 1 : -1;
        return 0;
    });
}

/* ---------- rendering ---------- */
function renderResults() {
    const tbody = document.getElementById("results-body");
    const countEl = document.getElementById("results-count");
    const infoEl = document.getElementById("scan-info");

    countEl.textContent = `${filteredResults.length} плотностей найдено`;

    const totalAll = allResults.length;
    if (totalAll !== filteredResults.length) {
        infoEl.textContent = `(из ${totalAll} всего)`;
    } else {
        infoEl.textContent = "";
    }

    if (filteredResults.length === 0) {
        tbody.innerHTML = `<tr class="empty-row"><td colspan="11">${
            allResults.length === 0
                ? 'Нажмите "Сканировать" для начала'
                : "Ничего не найдено по фильтрам"
        }</td></tr>`;
        return;
    }

    const maxVolume = Math.max(...filteredResults.map((r) => r.volume_usd));

    const fragment = document.createDocumentFragment();
    filteredResults.forEach((r) => {
        const tr = document.createElement("tr");
        tr.className = `${r.side === "bid" ? "bid-row" : "ask-row"} ${r.is_favorite ? "favorite-row" : ""}`;

        const barWidth = Math.round((r.volume_usd / maxVolume) * 60);

        tr.innerHTML = `
            <td><span class="fav-star ${r.is_favorite ? "active" : ""}"
                       onclick="toggleFavorite('${r.symbol}', this)">★</span></td>
            <td>${r.exchange}</td>
            <td><strong>${r.symbol}</strong></td>
            <td><span class="market-badge ${r.market_type}">${r.market_type === "spot" ? "Спот" : "Фьюч"}</span></td>
            <td>${r.side === "bid" ? "BID" : "ASK"}</td>
            <td>${formatPrice(r.price)}</td>
            <td>${formatUsd(r.volume_usd)}<span class="volume-bar" style="width:${barWidth}px"></span></td>
            <td>${formatNumber(r.amount)}</td>
            <td>${r.distance_pct.toFixed(2)}%</td>
            <td>${r.volume_ratio}x</td>
            <td>${formatUsd(r.volume_24h_usd)}</td>
        `;
        fragment.appendChild(tr);
    });

    tbody.innerHTML = "";
    tbody.appendChild(fragment);
}

/* ---------- favorites ---------- */
function addFavorite() {
    const input = document.getElementById("fav-input");
    const values = input.value
        .split(",")
        .map((v) => v.trim().toUpperCase())
        .filter((v) => v && !favorites.includes(v));

    favorites.push(...values);
    localStorage.setItem("favorites", JSON.stringify(favorites));
    input.value = "";
    renderFavorites();
}

function removeFavorite(symbol) {
    favorites = favorites.filter((f) => f !== symbol);
    localStorage.setItem("favorites", JSON.stringify(favorites));
    renderFavorites();
}

function toggleFavorite(symbol, starEl) {
    const base = symbol.split("/")[0];
    if (favorites.includes(base)) {
        favorites = favorites.filter((f) => f !== base);
    } else {
        favorites.push(base);
    }
    localStorage.setItem("favorites", JSON.stringify(favorites));
    renderFavorites();

    allResults.forEach((r) => {
        const b = r.symbol.split("/")[0];
        r.is_favorite = favorites.includes(b.toUpperCase());
    });
    filterResults();
}

function renderFavorites() {
    const container = document.getElementById("favorites-list");
    container.innerHTML = "";
    favorites.forEach((sym) => {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.innerHTML = `${sym} <button onclick="removeFavorite('${sym}')">&times;</button>`;
        container.appendChild(tag);
    });
}

/* ---------- formatters ---------- */
function formatUsd(value) {
    if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
    if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
    if (value >= 1e3) return `$${(value / 1e3).toFixed(1)}K`;
    return `$${value.toFixed(2)}`;
}

function formatPrice(price) {
    if (price >= 1000) return price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (price >= 1) return price.toFixed(4);
    if (price >= 0.001) return price.toFixed(6);
    return price.toFixed(8);
}

function formatNumber(num) {
    if (num >= 1e6) return `${(num / 1e6).toFixed(2)}M`;
    if (num >= 1e3) return `${(num / 1e3).toFixed(1)}K`;
    return num.toFixed(2);
}
