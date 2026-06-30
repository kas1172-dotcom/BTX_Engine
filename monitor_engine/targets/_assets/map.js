'use strict';

/* Account Map — reads map_targets.json (MapData) and renders an interactive
   Leaflet map + a filterable account list. Static: all data is precomputed at
   build time. See getLiveAccountData() for the live/authenticated-API seam. */

const state = {
  data: null,
  map: null,
  markers: {},                 // account id -> Leaflet marker
  filters: { q: '', segment: '', minFit: 0 },
};

const TIER_COLOR = { hot: '#FF5A4D', warm: '#F0B441', cool: '#6B7C93' };
const US_CENTER = { lat: 39.5, lon: -98.35 };

document.addEventListener('DOMContentLoaded', () => {
  const url = window.__DATA_URL || 'map_targets.json';
  fetch(url)
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(data => { state.data = data; boot(); })
    .catch(err => {
      document.getElementById('loading-screen').innerHTML =
        '<div class="loading-inner"><p>Could not load account data.</p><p style="color:#999">'
        + esc(err.message) + '</p></div>';
    });
});

function boot() {
  const cfg = state.data.config || {};
  applyAccent(cfg.accent_color);
  document.getElementById('map-title').textContent = cfg.title || 'Account Map';
  const placed = state.data.targets.filter(t => t.geo).length;
  document.getElementById('map-sub').textContent =
    (cfg.region ? cfg.region + ' · ' : '') + state.data.targets.length + ' accounts · '
    + placed + ' mapped';
  buildLegend();
  buildSegmentFilter(cfg);
  wireControls();
  initMap(cfg);
  render();
  document.getElementById('loading-screen').hidden = true;
  document.getElementById('app').hidden = false;
}

function applyAccent(hex) {
  if (!hex) return;
  document.documentElement.style.setProperty('--accent', hex);
  const meta = document.getElementById('meta-theme-color');
  if (meta) meta.setAttribute('content', hex);
}

function buildLegend() {
  const el = document.getElementById('legend');
  el.innerHTML =
    '<span><span class="dot hot"></span>Strong</span>' +
    '<span><span class="dot warm"></span>Possible</span>' +
    '<span><span class="dot cool"></span>Low</span>';
}

function buildSegmentFilter(cfg) {
  const sel = document.getElementById('segment');
  const segs = (cfg.segments && cfg.segments.length)
    ? cfg.segments
    : [...new Set(state.data.targets.map(t => t.segment).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">All segments</option>'
    + segs.map(s => '<option value="' + esc(s) + '">' + esc(s) + '</option>').join('');
}

function wireControls() {
  const search = document.getElementById('search');
  search.addEventListener('input', () => { state.filters.q = search.value.trim().toLowerCase(); render(); });
  document.getElementById('segment').addEventListener('change', e => {
    state.filters.segment = e.target.value; render();
  });
  const minfit = document.getElementById('minfit');
  minfit.addEventListener('input', () => {
    state.filters.minFit = Number(minfit.value);
    document.getElementById('minfit-val').textContent = minfit.value;
    render();
  });
}

function initMap(cfg) {
  if (typeof L === 'undefined') return;                 // Leaflet unavailable (e.g. offline test)
  const c = cfg.center || US_CENTER;
  state.map = L.map('map', { zoomControl: true }).setView([c.lat, c.lon], cfg.zoom || 5);
  // Dark "command center" basemap (CARTO dark) — falls back gracefully if offline.
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 19, subdomains: 'abcd',
    attribution: '© OpenStreetMap contributors © CARTO',
  }).addTo(state.map);
}

function passesFilters(t) {
  const f = state.filters;
  if (t.fit_score < f.minFit) return false;
  if (f.segment && t.segment !== f.segment) return false;
  if (f.q) {
    const hay = [t.name, t.segment, t.city, t.state, t.fit_rationale,
      ...(t.serve_with || []), ...((t.facts || []).map(x => x.label + ' ' + x.value))]
      .filter(Boolean).join(' ').toLowerCase();
    if (!hay.includes(f.q)) return false;
  }
  return true;
}

function render() {
  const visible = state.data.targets.filter(passesFilters);
  renderMarkers(visible);
  renderList(visible);
  const placed = visible.filter(t => t.geo).length;
  document.getElementById('count-bar').textContent =
    visible.length + ' shown · ' + placed + ' on map';
}

function renderMarkers(visible) {
  if (!state.map) return;
  Object.values(state.markers).forEach(m => state.map.removeLayer(m));
  state.markers = {};
  const visibleIds = new Set(visible.map(t => t.id));
  visible.forEach(t => {
    if (!t.geo) return;
    const marker = L.circleMarker([t.geo.lat, t.geo.lon], {
      radius: 6 + Math.round(t.fit_score / 18),
      color: 'rgba(255,255,255,.85)', weight: 1.5,
      fillColor: TIER_COLOR[t.fit_tier] || TIER_COLOR.cool,
      fillOpacity: 0.9,
      className: 'pin-' + t.fit_tier,          // CSS adds tier-colored glow / pulse
    }).addTo(state.map);
    marker.bindPopup(popupHtml(t), { maxWidth: 300 });
    marker.on('popupopen', () => wireLiveButton(t));
    state.markers[t.id] = marker;
  });
}

function popupHtml(t) {
  const loc = [t.city, t.state].filter(Boolean).join(', ');
  const facts = (t.facts || []).map(f =>
    '<div><span class="pf-label">' + esc(f.label) + ':</span> ' + esc(f.value) + '</div>').join('');
  const serve = (t.serve_with || []).map(s => '<span class="serve-chip">' + esc(s) + '</span>').join('');
  return '' +
    '<div class="popup-name">' + esc(t.name) + '</div>' +
    '<div class="popup-meta">' + esc([t.segment, loc].filter(Boolean).join(' · ')) +
      (t.geo_approx ? ' <span class="approx-note">(approx · state-level)</span>' : '') + '</div>' +
    '<div class="popup-fit"><b>Fit ' + t.fit_score + '</b> — ' + esc(t.fit_rationale) + '</div>' +
    (serve ? '<div class="popup-serve"><b>How we can serve them:</b><br>' + serve + '</div>' : '') +
    (facts ? '<div class="popup-facts">' + facts + '</div>' : '') +
    (t.url ? '<a class="popup-link" href="' + esc(t.url) + '" target="_blank" rel="noopener">Open source ↗</a>' : '') +
    '<div><button class="live-btn" data-id="' + esc(t.id) + '">Refresh live data</button></div>';
}

function renderList(visible) {
  const list = document.getElementById('account-list');
  list.innerHTML = '';
  visible.forEach(t => {
    const li = el('li', 'account-item' + (t.geo ? '' : ' unplaced'));
    const loc = [t.city, t.state].filter(Boolean).join(', ');
    li.innerHTML =
      '<div class="ai-top"><span class="fit-badge ' + t.fit_tier + '">' + t.fit_score + '</span>' +
      '<span class="ai-name">' + esc(t.name) + '</span></div>' +
      '<div class="ai-meta">' + esc([t.segment, loc].filter(Boolean).join(' · ') ||
        (t.geo ? '' : 'no location')) + '</div>';
    if (t.geo && state.map) {
      li.addEventListener('click', () => {
        state.map.setView([t.geo.lat, t.geo.lon], Math.max(state.map.getZoom(), 8));
        const m = state.markers[t.id];
        if (m) m.openPopup();
      });
    }
    list.appendChild(li);
  });
}

/* ── Live / authenticated-API seam ──────────────────────────────────────────
   Returns a Promise of fresh, per-account data (e.g. current capacity, open
   quotes, account-owner notes) from BTX's own systems. Today it resolves null
   (static build). To wire a live CRM/ERP, point this at a small serverless
   PROXY that holds the API key server-side and returns JSON — change ONLY this
   function. Never put a secret in this file; it ships to the browser. */
function getLiveAccountData(accountId) {
  // return fetch('/api/account/' + encodeURIComponent(accountId)).then(r => r.json());
  return Promise.resolve(null);
}

function wireLiveButton(t) {
  const btn = document.querySelector('.live-btn[data-id="' + cssEscape(t.id) + '"]');
  if (!btn) return;
  btn.addEventListener('click', () => {
    btn.textContent = 'Loading…';
    getLiveAccountData(t.id).then(live => {
      btn.textContent = live ? 'Live data loaded' : 'No live source configured';
    }).catch(() => { btn.textContent = 'Live fetch failed'; });
  });
}

/* helpers */
function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function cssEscape(s) { return String(s).replace(/"/g, '\\"'); }
