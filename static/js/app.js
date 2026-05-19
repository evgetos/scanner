let allCards = [];
let favorites = JSON.parse(localStorage.getItem("favorites") || "[]");
let autoScanActive = false;
let pollInterval = null;
let hiddenCards = new Set(JSON.parse(localStorage.getItem("hiddenCards") || "[]"));

document.addEventListener("DOMContentLoaded", () => {
    loadSettings();
    loadExchanges();
    renderFavorites();
});

async function loadExchanges() {
    try {
        const resp = await fetch("/api/exchanges");
        const exchanges = await resp.json();
        const el = document.getElementById("exchanges-list");
        el.innerHTML = "";
        const saved = JSON.parse(localStorage.getItem("enabledExchanges") || "null");
        exchanges.forEach(ex => {
            const label = document.createElement("label");
            label.className = "cb";
            const cb = document.createElement("input");
            cb.type = "checkbox";
            cb.dataset.exchange = ex.id;
            cb.checked = saved ? saved.includes(ex.id) : true;
            label.appendChild(cb);
            label.appendChild(document.createTextNode(` ${ex.name}`));
            el.appendChild(label);
        });
    } catch (e) { console.error("loadExchanges:", e); }
}

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
    document.querySelectorAll("#exchanges-list input[type=checkbox]").forEach(cb => {
        if (cb.checked) enabled.push(cb.dataset.exchange);
    });
    localStorage.setItem("enabledExchanges", JSON.stringify(enabled));
}

function getSettings() {
    const mt = [];
    if (document.getElementById("market-spot").checked) mt.push("spot");
    if (document.getElementById("market-futures").checked) mt.push("futures");
    const ex = [];
    document.querySelectorAll("#exchanges-list input[type=checkbox]").forEach(cb => {
        if (cb.checked) ex.push(cb.dataset.exchange);
    });
    return {
        min_volume_spot: +document.getElementById("min-volume-spot").value,
        min_volume_futures: +document.getElementById("min-volume-futures").value,
        max_distance_pct: +document.getElementById("max-distance").value,
        min_density_usd: +document.getElementById("min-density").value,
        max_symbols_per_exchange: +document.getElementById("max-symbols").value,
        enabled_exchanges: ex,
        market_types: mt,
        favorites: favorites,
    };
}

function toggleSettings() {
    document.getElementById("settings-panel").classList.toggle("hidden");
}

/* scan */
async function startScan() {
    const btn = document.getElementById("btn-scan");
    const st = document.getElementById("status-text");
    const overlay = document.getElementById("loading-overlay");
    btn.disabled = true;
    st.textContent = "Сканирование...";
    st.className = "status-scanning";
    overlay.classList.remove("hidden");
    saveSettings();
    try {
        const resp = await fetch("/api/scan", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(getSettings()),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        allCards = await resp.json();
        st.textContent = "Готово";
        st.className = "status-done";
        document.getElementById("last-update").textContent = new Date().toLocaleTimeString("ru-RU");
        renderCards();
    } catch (e) {
        console.error("scan:", e);
        st.textContent = "Ошибка";
        st.className = "status-error";
    } finally {
        btn.disabled = false;
        overlay.classList.add("hidden");
    }
}

async function toggleAutoScan() {
    const btn = document.getElementById("btn-auto");
    if (autoScanActive) {
        autoScanActive = false;
        btn.classList.remove("btn-active");
        btn.textContent = "Авто";
        if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
        await fetch("/api/auto-scan/stop", {method: "POST"});
    } else {
        saveSettings();
        autoScanActive = true;
        btn.classList.add("btn-active");
        btn.textContent = "Стоп";
        await fetch("/api/auto-scan/start", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(getSettings()),
        });
        pollInterval = setInterval(pollResults, 3000);
    }
}

async function pollResults() {
    try {
        const resp = await fetch("/api/results");
        const data = await resp.json();
        if (data && data.length > 0) {
            allCards = data;
            document.getElementById("last-update").textContent = new Date().toLocaleTimeString("ru-RU");
            document.getElementById("status-text").textContent = "Авто-скан";
            document.getElementById("status-text").className = "status-done";
            renderCards();
        }
    } catch (e) { console.error("poll:", e); }
}

/* render */
function renderCards() {
    const container = document.getElementById("cards-container");
    const empty = document.getElementById("empty-state");
    const countEl = document.getElementById("density-count");

    const visible = allCards.filter(c => !hiddenCards.has(c.symbol + "|" + c.market_type));
    const totalDensities = visible.reduce((s, c) => s + c.densities.length, 0);
    countEl.textContent = `${totalDensities} плотностей`;

    if (visible.length === 0 && allCards.length === 0) {
        container.innerHTML = "";
        container.appendChild(empty);
        return;
    }

    const fragment = document.createDocumentFragment();

    visible.forEach(card => {
        const el = document.createElement("div");
        el.className = "density-card";
        if (card.is_favorite) el.classList.add("favorite");

        const typeLabel = card.market_type === "spot" ? "S" : "F";
        const typeClass = card.market_type;
        const isFav = isFavoriteSymbol(card.symbol);
        const cardKey = card.symbol + "|" + card.market_type;
        const isHidden = hiddenCards.has(cardKey);

        let headerHtml = `
            <div class="card-header">
                <div class="card-title">
                    <span class="card-symbol">${card.symbol}</span>
                    <span class="card-type ${typeClass}">${typeLabel}</span>
                </div>
                <div class="card-actions">
                    <button title="Скрыть" onclick="toggleHideCard('${cardKey}')">👁</button>
                    <button class="${isFav ? 'active' : ''}" title="Избранное" onclick="toggleCardFavorite('${card.symbol}')">☆</button>
                </div>
            </div>`;

        let rowsHtml = "";
        card.densities.forEach(d => {
            const rowClass = d.side === "bid" ? "bid" : "ask";
            const arrow = d.side === "bid" ? "▲" : "▼";
            const ageStr = formatAge(d.age_seconds);
            rowsHtml += `
                <div class="density-row ${rowClass}">
                    <span class="vol-cell">${formatUsd(d.volume_usd)}</span>
                    <span class="side-icon">${arrow}</span>
                    <span class="age-cell">
                        <span class="exchange-tag">${d.exchange_id}</span>
                        ${ageStr}
                    </span>
                    <span class="price-cell">${formatPrice(d.price)}</span>
                    <span class="dist-cell">${d.distance_pct.toFixed(1)}%</span>
                </div>`;
        });

        el.innerHTML = headerHtml + rowsHtml;
        fragment.appendChild(el);
    });

    container.innerHTML = "";
    container.appendChild(fragment);
}

/* favorites */
function addFavorite() {
    const input = document.getElementById("fav-input");
    const vals = input.value.split(",").map(v => v.trim().toUpperCase()).filter(v => v && !favorites.includes(v));
    favorites.push(...vals);
    localStorage.setItem("favorites", JSON.stringify(favorites));
    input.value = "";
    renderFavorites();
}

function removeFavorite(sym) {
    favorites = favorites.filter(f => f !== sym);
    localStorage.setItem("favorites", JSON.stringify(favorites));
    renderFavorites();
}

function isFavoriteSymbol(symbol) {
    const base = symbol.replace(/USDT$/i, "").replace(/USD$/i, "");
    return favorites.some(f => f === base.toUpperCase() || f === symbol.toUpperCase());
}

function toggleCardFavorite(symbol) {
    const base = symbol.replace(/USDT$/i, "").replace(/USD$/i, "").toUpperCase();
    if (favorites.includes(base)) {
        favorites = favorites.filter(f => f !== base);
    } else {
        favorites.push(base);
    }
    localStorage.setItem("favorites", JSON.stringify(favorites));
    renderFavorites();
    renderCards();
}

function renderFavorites() {
    const el = document.getElementById("favorites-list");
    el.innerHTML = "";
    favorites.forEach(sym => {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.innerHTML = `${sym} <button onclick="removeFavorite('${sym}')">&times;</button>`;
        el.appendChild(tag);
    });
}

/* hide cards */
function toggleHideCard(key) {
    if (hiddenCards.has(key)) hiddenCards.delete(key);
    else hiddenCards.add(key);
    localStorage.setItem("hiddenCards", JSON.stringify([...hiddenCards]));
    renderCards();
}

/* formatters */
function formatUsd(v) {
    if (v >= 1e9) return (v / 1e9).toFixed(2) + "B$";
    if (v >= 1e6) return (v / 1e6).toFixed(2) + "M$";
    if (v >= 1e3) return (v / 1e3).toFixed(1) + "K$";
    return v.toFixed(0) + "$";
}

function formatPrice(p) {
    if (p >= 1000) return p.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
    if (p >= 1) return p.toFixed(4);
    if (p >= 0.001) return p.toFixed(6);
    return p.toFixed(8);
}

function formatAge(seconds) {
    if (seconds < 60) return seconds + "с";
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    if (m < 60) return m + "м " + s + "с";
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return h + "ч " + rm + "м";
}
