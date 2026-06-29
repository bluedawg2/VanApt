"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const SOURCE_LABEL = {
  craigslist: "Craigslist", kijiji: "Kijiji", zumper: "Zumper/PadMapper",
  rentals_ca: "Rentals.ca", manual: "Manual", facebook: "Facebook",
};
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

let pollTimer = null;
let lastItems = [];      // current filtered result set
let map = null, markerLayer = null, mapReady = false;

// ---- filter state -> query string ----------------------------------------
function buildQuery() {
  const p = new URLSearchParams();
  p.set("max_price", $("#price").value);
  const beds = $$(".beds:checked").map((c) => parseFloat(c.value));
  if (beds.length) {
    p.set("min_bedrooms", Math.min(...beds));
    p.set("max_bedrooms", Math.max(...beds));
  } else {
    p.set("max_bedrooms", "0");
  }
  const areas = $$(".area:checked").map((c) => c.value);
  p.set("areas", areas.length ? areas.join(",") : "none");

  const srcs = $$(".src-filter:checked").map((c) => c.value);
  if (srcs.length) p.set("sources", srcs.join(","));
  else p.set("sources", "none");

  const avail = $("#available-by").value;
  if (avail) p.set("available_by", avail);

  p.set("include_rooms", $("#include-rooms").checked);
  p.set("sort", $("#sort").value);

  const sf = $("#status-filter").value;
  if (sf === "favorite") p.set("status", "favorite");
  else if (sf === "new") p.set("status", "new");
  else if (sf === "discarded") p.set("status", "discarded");
  return { qs: p.toString(), statusFilter: sf };
}

async function load() {
  const { qs, statusFilter } = buildQuery();
  const r = await fetch("/api/listings?" + qs);
  const data = await r.json();
  let items = data.listings;
  if (statusFilter === "active") items = items.filter((x) => x.status !== "discarded");
  lastItems = items;
  renderCounts(items, statusFilter);
  if ($("#map").classList.contains("hidden")) renderCards(items, statusFilter);
  else renderMap(items);
  loadStatus(true); // refresh status line only (not the filtered counts)
}

// ---- dynamic counts (reflect current filters) -----------------------------
function renderCounts(items, statusFilter) {
  const ev = items.filter((x) => x.area === "east_van").length;
  const bby = items.filter((x) => x.area === "burnaby").length;
  const fav = items.filter((x) => x.status === "favorite").length;
  const bySrc = {};
  items.forEach((x) => (bySrc[x.source] = (bySrc[x.source] || 0) + 1));
  const srcLine = Object.entries(bySrc)
    .sort((a, b) => b[1] - a[1])
    .map(([s, n]) => `${SOURCE_LABEL[s] || s}&nbsp;<b>${n}</b>`).join(" · ");
  $("#counts").innerHTML = `
    <div><b>${items.length}</b> matching now</div>
    <div>East Van <b>${ev}</b> &nbsp;·&nbsp; Burnaby <b>${bby}</b> &nbsp;·&nbsp; ★ <b>${fav}</b></div>
    ${srcLine ? `<div class="src-counts">${srcLine}</div>` : ""}`;
  const label = statusFilter === "favorite" ? " favorited" : "";
  $("#result-head").innerHTML =
    `<b>${items.length}</b>${label} place${items.length === 1 ? "" : "s"} shown`;
}

// ---- card rendering -------------------------------------------------------
function esc(s) { return (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function bedLabel(b) {
  if (b === null || b === undefined) return "?";
  return b === 0 ? "Studio" : b + " BR";
}
function availLabel(a) {
  if (!a) return "";
  if (a === "now") return "Now";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(a);
  return m ? `${MONTHS[+m[2] - 1]} ${+m[3]}` : a;
}

function card(x) {
  const photo = x.image_url
    ? `<div class="photo" style="background-image:url('${esc(x.image_url)}')">`
    : `<div class="photo"><span class="noimg">🏢</span>`;
  const price = x.price ? `$${x.price.toLocaleString()}` : "Ask";
  const room = x.listing_type === "room_share" ? `<span class="badge room">Room/Share</span>` : "";
  const also = (x.also_on || []).map(
    (a) => `<a href="${esc(a.url)}" target="_blank">${SOURCE_LABEL[a.source] || a.source}</a>`
  ).join(", ");
  const avail = availLabel(x.available_date);
  const metaBits = [`<span><span class="k">${bedLabel(x.bedrooms)}</span></span>`];
  if (x.sqft) metaBits.push(`<span><span class="k">${x.sqft}</span> sqft</span>`);
  if (x.bathrooms) metaBits.push(`<span><span class="k">${x.bathrooms}</span> bath</span>`);
  if (avail) metaBits.push(`<span class="avail">📅 ${avail}</span>`);

  return `<div class="card ${x.status === "discarded" ? "discarded" : ""}" data-uid="${x.uid}">
    ${photo}
      <span class="price">${price}</span>
      <span class="badges"><span class="badge src">${SOURCE_LABEL[x.source] || x.source}</span>${room}</span>
    </div>
    <div class="body">
      <div class="title">${esc(x.title) || "(untitled)"}</div>
      <div class="meta">${metaBits.join("")}</div>
      ${x.neighborhood || x.address ? `<div class="hood">📍 ${esc(x.neighborhood || x.address)}</div>` : ""}
      ${also ? `<div class="also">Also on: ${also}</div>` : ""}
      <div class="actions">
        <a class="btn view-link" href="${esc(x.url)}" target="_blank">View ↗</a>
        <button class="btn icon-fav ${x.status === "favorite" ? "on" : ""}" title="Favorite">★</button>
        <button class="btn icon-bad" title="Discard">✕</button>
      </div>
    </div>
  </div>`;
}

function renderCards(items) {
  $("#cards").innerHTML = items.map(card).join("");
  $("#empty").classList.toggle("hidden", items.length > 0);
  $$(".card").forEach((el) => {
    const uid = el.dataset.uid;
    el.querySelector(".icon-fav").onclick = () => {
      const on = el.querySelector(".icon-fav").classList.contains("on");
      setStatus(uid, on ? "new" : "favorite");
    };
    el.querySelector(".icon-bad").onclick = () => setStatus(uid, "discarded");
  });
}

// ---- map view -------------------------------------------------------------
function ensureMap() {
  if (mapReady) return;
  map = L.map("map").setView([49.26, -123.02], 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap",
  }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
  mapReady = true;
}

function renderMap(items) {
  ensureMap();
  markerLayer.clearLayers();
  const pts = [];
  items.forEach((x) => {
    if (x.lat == null || x.lng == null) return;
    const fav = x.status === "favorite";
    const color = fav ? "#f59e0b" : x.area === "burnaby" ? "#16a34a" : "#2563eb";
    const m = L.circleMarker([x.lat, x.lng], {
      radius: fav ? 9 : 7, color: "#fff", weight: 1.5,
      fillColor: color, fillOpacity: 0.9,
    });
    const avail = availLabel(x.available_date);
    m.bindPopup(`
      <div class="pop">
        ${x.image_url ? `<img src="${esc(x.image_url)}" />` : ""}
        <div class="pop-price">${x.price ? "$" + x.price.toLocaleString() : "Ask"} · ${bedLabel(x.bedrooms)}${avail ? " · 📅 " + avail : ""}</div>
        <div class="pop-title">${esc(x.title)}</div>
        <div class="pop-hood">${esc(x.neighborhood || x.address)} <span class="pop-src">${SOURCE_LABEL[x.source] || x.source}</span></div>
        <div class="pop-actions">
          <a href="${esc(x.url)}" target="_blank">View ↗</a>
          <a href="#" data-fav="${x.uid}">${fav ? "★ Saved" : "☆ Favorite"}</a>
          <a href="#" data-bad="${x.uid}">✕ Discard</a>
        </div>
      </div>`);
    markerLayer.addLayer(m);
    pts.push([x.lat, x.lng]);
  });
  if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 14 });
  setTimeout(() => map.invalidateSize(), 50);
  $("#empty").classList.toggle("hidden", items.length > 0);
}

// popup action delegation
document.addEventListener("click", (e) => {
  const f = e.target.closest("[data-fav]");
  const b = e.target.closest("[data-bad]");
  if (f) { e.preventDefault(); setStatus(f.dataset.fav, "favorite"); }
  if (b) { e.preventDefault(); setStatus(b.dataset.bad, "discarded"); }
});

function showMap(on) {
  $("#map").classList.toggle("hidden", !on);
  $("#cards").classList.toggle("hidden", on);
  $("#view-map").classList.toggle("active", on);
  $("#view-list").classList.toggle("active", !on);
  if (on) renderMap(lastItems); else renderCards(lastItems);
}

async function setStatus(uid, status) {
  await fetch(`/api/listings/${uid}/status`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  load();
}

// ---- status / refresh / sources -------------------------------------------
async function loadStatus(lineOnly) {
  const s = await fetch("/api/status").then((r) => r.json());
  if (!lineOnly && !$("#source-filters").children.length) buildSourceFilters(s);
  const when = s.last_refresh ? `Updated ${s.last_refresh}` : "Never refreshed — hit Refresh to collect listings";
  if (s.running) {
    $("#status-line").innerHTML = `<span class="spin">↻</span> Collecting listings…`;
    $("#refresh-btn").disabled = true;
  } else {
    $("#status-line").textContent = when;
    $("#refresh-btn").disabled = false;
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; load(); }
  }
  return s;
}

function buildSourceFilters(s) {
  const present = Object.keys((s.counts && s.counts.by_source) || {});
  const known = ["craigslist", "zumper", "kijiji", "rentals_ca", "manual", "facebook"];
  const sources = known.filter((k) => present.includes(k));
  present.forEach((p) => { if (!sources.includes(p)) sources.push(p); });
  $("#source-filters").innerHTML = sources.map((src) =>
    `<label class="check"><input type="checkbox" class="src-filter" value="${src}" checked /> ${SOURCE_LABEL[src] || src}</label>`
  ).join("") || `<span class="hint">No sources yet — hit Refresh.</span>`;
  $$(".src-filter").forEach((el) => (el.onchange = load));
}

async function refresh() {
  await fetch("/api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  $("#refresh-btn").disabled = true;
  if (!pollTimer) pollTimer = setInterval(() => loadStatus(false), 2000);
  loadStatus(false);
}

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

// ---- move-in month options ------------------------------------------------
function buildMonthOptions() {
  const now = new Date();
  const sel = $("#available-by");
  for (let i = 0; i < 5; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() + i, 1);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
    const opt = document.createElement("option");
    opt.value = iso;
    opt.textContent = `By ${MONTHS[d.getMonth()]} 1${d.getFullYear() !== now.getFullYear() ? " " + d.getFullYear() : ""}`;
    sel.appendChild(opt);
  }
}

// ---- manual import --------------------------------------------------------
function openImport(open) { $("#import-modal").classList.toggle("hidden", !open); }
async function saveImport() {
  const payload = {
    url: $("#im-url").value, title: $("#im-title").value,
    price: $("#im-price").value, bedrooms: $("#im-beds").value,
    neighborhood: $("#im-hood").value, area: $("#im-area").value || undefined,
    listing_type: $("#im-type").value, description: $("#im-desc").value,
    image_url: $("#im-img").value, source: "manual",
  };
  if (!payload.url) { toast("A link is required."); return; }
  const r = await fetch("/api/import", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => r.json());
  if (r.ok) {
    toast("Added ✓"); openImport(false);
    $$("#import-modal input, #import-modal textarea").forEach((i) => (i.value = ""));
    load();
  } else { toast(r.error || "Could not add"); }
}

// ---- wiring ---------------------------------------------------------------
function init() {
  const price = $("#price");
  const out = $("#price-out");
  const sync = () => (out.textContent = "$" + Number(price.value).toLocaleString());
  price.oninput = sync; price.onchange = load; sync();
  buildMonthOptions();

  $$(".chip").forEach((ch) => (ch.onclick = () => {
    price.value = ch.dataset.price; sync();
    $$(".chip").forEach((c) => c.classList.toggle("active", c === ch));
    load();
  }));
  $$(".beds, .area, #include-rooms").forEach((el) => (el.onchange = load));
  $("#status-filter").onchange = load;
  $("#sort").onchange = load;
  $("#available-by").onchange = load;
  $("#refresh-btn").onclick = refresh;
  $("#view-list").onclick = () => showMap(false);
  $("#view-map").onclick = () => showMap(true);
  $("#import-toggle").onclick = () => openImport(true);
  $("#im-cancel").onclick = () => openImport(false);
  $("#im-save").onclick = saveImport;
  $("#import-modal").onclick = (e) => { if (e.target.id === "import-modal") openImport(false); };

  loadStatus(false).then(load);
}
init();
