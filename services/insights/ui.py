"""Web-behavior insights dashboard — same dark app-shell design language as
capman2's desktop UI (capman/api/chat_ui.py), reused here for a Railway-hosted
read-only view over the shared capman-pg tenant tables.
"""

INSIGHTS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>capman2 — Web Insights</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root { color-scheme: dark; }
  html, body { height: 100%; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    background: #0B0D10; color: #E6E9EE;
    display: block; min-height: 0; overflow: hidden;
  }
  button, input, textarea { font: inherit; }
  button:focus-visible, a:focus-visible { outline: 2px solid #2962FF; outline-offset: 2px; }

  .app-shell { height: 100vh; height: 100dvh; min-height: 0; display: grid; grid-template-columns: 244px minmax(0, 1fr); overflow: hidden; }
  .sidebar { background: #15181D; border-right: 1px solid #282E37; padding: 18px 12px; display: flex; flex-direction: column; gap: 20px; overflow-y: auto; }
  .brand { display: flex; align-items: center; gap: 9px; min-height: 32px; padding: 0 8px; font-size: 16px; font-weight: 700; letter-spacing: -.02em; color: #fff; }
  .brand-mark { display: grid; place-items: center; width: 22px; height: 22px; border-radius: 6px; background: #2962FF; color: #fff; font-size: 12px; font-weight: 800; }
  .scope-card { padding: 10px; border: 1px solid #303844; background: #1E232A; border-radius: 8px; }
  .scope-label, .nav-label, .section-kicker { color: #9AA4B2; font-size: 10px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
  .scope-current { display: flex; justify-content: space-between; align-items: center; margin-top: 5px; color: #fff; font-size: 13px; font-weight: 650; }
  .scope-private { display: block; margin-top: 3px; color: #9AA4B2; font-size: 11px; }
  .sidebar-nav { display: flex; flex-direction: column; gap: 3px; }
  .nav-label { padding: 0 8px; margin: 4px 0 5px; }
  .nav-item { width: 100%; min-height: 40px; padding: 8px; border: 1px solid transparent; border-radius: 6px; display: flex; align-items: center; justify-content: space-between; gap: 8px; background: transparent; color: #BAC2CD; cursor: pointer; text-align: left; font-size: 13px; transition: background .14s ease, color .14s ease, border-color .14s ease; }
  .nav-item:hover { color: #fff; background: #20252D; }
  .nav-item.active { color: #fff; background: #223357; border-color: #2D519D; }
  .nav-count { min-width: 22px; padding: 1px 6px; text-align: center; border-radius: 10px; background: #282E37; color: #9AA4B2; font-size: 11px; font-variant-numeric: tabular-nums; }
  .nav-item.active .nav-count { background: #2962FF; color: #fff; }
  .sidebar-footer { margin-top: auto; border-top: 1px solid #282E37; padding-top: 12px; }
  .sidebar-footer p { color: #677181; font-size: 11px; line-height: 1.5; }

  .workspace { min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow: hidden; background: #0B0D10; }
  .workspace-header { min-height: 68px; padding: 13px clamp(20px, 4vw, 48px); display: flex; flex-shrink: 0; align-items: center; justify-content: space-between; border-bottom: 1px solid #282E37; background: #0B0D10; }
  .workspace-header h1 { margin-top: 2px; color: #fff; font-size: 20px; line-height: 1.2; letter-spacing: -.025em; }
  .connection { display: inline-flex; align-items: center; gap: 7px; color: #9AA4B2; font-size: 12px; }
  .connection::before { content: ''; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  .connection.is-online { color: #00B894; }

  .view { min-height: 0; flex: 1; overflow: hidden; display: flex; flex-direction: column; }
  .view.hidden { display: none; }

  .home-content { flex: 1; min-height: 0; overflow-y: auto; padding: clamp(20px, 4vw, 48px); max-width: 1200px; width: 100%; margin: 0 auto; }
  .home-intro h2 { max-width: 620px; margin-top: 7px; color: #fff; font-size: clamp(22px, 3vw, 30px); line-height: 1.13; letter-spacing: -.04em; }
  .home-intro p { max-width: 570px; margin-top: 11px; color: #9AA4B2; font-size: 14px; line-height: 1.55; }
  .stat-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-top: 24px; }
  .stat-tile { padding: 14px; border: 1px solid #282E37; border-radius: 8px; background: #12151A; }
  .stat-tile .stat-value { color: #fff; font-size: 24px; font-weight: 700; letter-spacing: -.02em; }
  .stat-tile .stat-label { margin-top: 3px; color: #9AA4B2; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }
  .home-grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(270px, .65fr); gap: 38px; padding-top: 30px; }
  .home-section { min-width: 0; }
  .home-section + .home-section { margin-top: 30px; }
  .section-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 10px; }
  .section-heading h3 { color: #fff; font-size: 14px; letter-spacing: -.01em; }
  .text-button { appearance: none; border: 0; background: transparent; color: #87A8FF; padding: 6px 0; cursor: pointer; font-size: 12px; }
  .text-button:hover { color: #B7CAFF; text-decoration: underline; }
  .evidence-list { border-top: 1px solid #303844; }
  .evidence-row { width: 100%; min-height: 60px; padding: 12px 0; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 14px; border: 0; border-bottom: 1px solid #303844; background: transparent; color: inherit; cursor: pointer; text-align: left; }
  .evidence-row:hover .evidence-title { color: #AFC5FF; }
  .evidence-title { display: block; overflow: hidden; color: #E6E9EE; font-size: 13px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
  .evidence-meta { display: block; overflow: hidden; margin-top: 4px; color: #9AA4B2; font-size: 11px; line-height: 1.3; text-overflow: ellipsis; white-space: nowrap; }
  .evidence-trailing { color: #9AA4B2; font-size: 11px; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .attention-panel { padding: 16px; border: 1px solid #3E3523; border-radius: 8px; background: #211C14; }
  .attention-panel h3 { color: #F7C36B; font-size: 13px; }
  .attention-panel p { margin-top: 7px; color: #C6B89B; font-size: 12px; line-height: 1.5; }
  .attention-panel ul { margin-top: 10px; padding-left: 16px; }
  .attention-panel li { color: #C6B89B; font-size: 12px; line-height: 1.7; }
  .home-empty { padding: 20px 0; color: #9AA4B2; font-size: 13px; line-height: 1.55; }
  .home-error { color: #F28B8B; }

  .toolbar { padding: 12px clamp(20px, 4vw, 48px) 0; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; border-bottom: 1px solid #282E37; }
  .toolbar .filter-btn { background: #1a1a1a; border: 1px solid #303844; color: #9AA4B2; border-radius: 12px; padding: 3px 10px; cursor: pointer; font-size: 12px; margin-bottom: 10px; }
  .toolbar .filter-btn.active { background: #2962FF; border-color: #3D73FF; color: #fff; }

  .card-list { flex: 1; overflow-y: auto; padding: clamp(20px, 4vw, 48px); display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 14px; align-content: start; }
  .card { background: #12151A; border: 1px solid #282E37; border-radius: 10px; padding: 16px; transition: all 0.15s; cursor: pointer; }
  .card:hover { border-color: #2962FF; transform: translateY(-1px); }
  .card-title { font-size: 14px; font-weight: 600; color: #fff; margin-bottom: 6px; }
  .card-meta { font-size: 11px; color: #9AA4B2; margin-bottom: 8px; display: flex; gap: 8px; flex-wrap: wrap; }
  .card-meta span { background: #1a1e25; padding: 2px 8px; border-radius: 10px; }
  .card-meta .tag-domain { color: #7dd3fc; }
  .card-meta .tag-score { color: #22c55e; }
  .card-meta .tag-succeeded { color: #22c55e; }
  .card-meta .tag-abandoned, .card-meta .tag-errored { color: #F28B8B; }
  .card-meta .tag-bounced { color: #F7C36B; }
  .card-body { font-size: 13px; color: #C6CBD3; line-height: 1.5; }
  .card-body ul { padding-left: 18px; margin: 4px 0; }
  .empty { grid-column: 1/-1; text-align: center; color: #555; padding: 60px 20px; font-style: italic; }

  .modal-overlay { position: fixed; inset: 0; background: rgba(4, 6, 8, .78); display: none; align-items: center; justify-content: center; z-index: 100; padding: 30px; }
  .modal-overlay.show { display: flex; }
  .modal { background: #1E232A; border: 1px solid #303844; border-radius: 12px; max-width: 800px; width: 100%; max-height: 90vh; overflow-y: auto; padding: 28px; position: relative; }
  .modal h2 { color: #fff; margin-bottom: 12px; font-size: 18px; }
  .modal h3 { color: #7dd3fc; font-size: 14px; margin: 18px 0 8px; }
  .modal p, .modal li { color: #ccc; line-height: 1.6; font-size: 13px; }
  .modal ol, .modal ul { padding-left: 24px; }
  .modal-close { position: absolute; top: 12px; right: 16px; background: none; border: none; color: #888; font-size: 22px; cursor: pointer; }
  .modal-close:hover { color: #fff; }
  .step-card { background: #12151A; border: 1px solid #282E37; border-radius: 8px; padding: 12px; margin: 8px 0; }
  .step-card .step-action { font-weight: 600; color: #fff; margin-bottom: 4px; }
  .step-card .step-tool { color: #a5f3fc; font-family: monospace; font-size: 12px; }
  .step-card .step-meta { color: #888; font-size: 12px; margin-top: 4px; }

  @keyframes fadein { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; } }

  @media (max-width: 760px) {
    body { overflow: auto; }
    .app-shell { display: block; height: auto; min-height: 100vh; min-height: 100dvh; overflow: visible; }
    .sidebar { position: sticky; top: 0; z-index: 10; min-height: auto; padding: 10px 12px; border-right: 0; border-bottom: 1px solid #282E37; }
    .brand, .scope-card, .nav-label, .sidebar-footer { display: none; }
    .sidebar-nav { flex-direction: row; overflow-x: auto; gap: 6px; }
    .nav-item { flex: 0 0 auto; width: auto; min-height: 40px; padding: 8px 11px; }
    .workspace { min-height: calc(100vh - 62px); }
    .workspace-header { min-height: 62px; padding: 12px 20px; }
    .home-grid { grid-template-columns: 1fr; gap: 24px; padding-top: 24px; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; scroll-behavior: auto !important; transition-duration: .01ms !important; }
  }
</style>
</head>
<body>
<div class="app-shell">
  <aside class="sidebar" aria-label="Primary navigation">
    <div class="brand"><span class="brand-mark">C</span> capman insights</div>
    <div class="scope-card">
      <div class="scope-label">Tenant</div>
      <div class="scope-current"><span id="tenant-name">__TENANT__</span></div>
      <span class="scope-private">Anonymous web-behavior analysis. No PII.</span>
    </div>
    <nav class="sidebar-nav" aria-label="Workspace">
      <div class="nav-label">Overview</div>
      <button type="button" class="nav-item active" data-view="home" aria-current="page"><span>Home</span></button>
      <div class="nav-label">Behavior</div>
      <button type="button" class="nav-item" data-view="episodes"><span>Episodes</span><span class="nav-count" id="count-episodes">0</span></button>
      <div class="nav-label">Knowledge</div>
      <button type="button" class="nav-item" data-view="playbooks"><span>Playbooks</span><span class="nav-count" id="count-playbooks">0</span></button>
      <button type="button" class="nav-item" data-view="gaps"><span>Knowledge gaps</span><span class="nav-count" id="count-gaps">0</span></button>
    </nav>
    <div class="sidebar-footer">
      <p>Built from anonymized session behavior — no visitor identity is ever stored.</p>
    </div>
  </aside>
  <main class="workspace">
    <header class="workspace-header">
      <div><div class="section-kicker">Web behavior analysis</div><h1 id="page-title">Home</h1></div>
      <div class="connection" id="status" aria-live="polite">Connecting</div>
    </header>

<!-- Home view -->
<div class="view" id="view-home">
  <div class="home-content">
    <div class="home-intro">
      <div class="section-kicker">Read-only</div>
      <h2>What visitors are actually doing.</h2>
      <p>Sessions are clustered into episodes and analyzed by the capman2 analyst worker every 15 minutes. This view reads directly from that pipeline.</p>
    </div>
    <div class="stat-row" id="stat-row">
      <div class="stat-tile"><div class="stat-value" id="stat-episodes">—</div><div class="stat-label">Episodes</div></div>
      <div class="stat-tile"><div class="stat-value" id="stat-analyzed">—</div><div class="stat-label">Analyzed</div></div>
      <div class="stat-tile"><div class="stat-value" id="stat-unassigned">—</div><div class="stat-label">Unassigned events</div></div>
      <div class="stat-tile"><div class="stat-value" id="stat-playbooks">—</div><div class="stat-label">Playbooks</div></div>
      <div class="stat-tile"><div class="stat-value" id="stat-gaps">—</div><div class="stat-label">Knowledge gaps</div></div>
    </div>
    <div class="home-grid">
      <div>
        <section class="home-section">
          <div class="section-heading"><h3>Recent episodes</h3><button class="text-button" type="button" data-view-link="episodes">View all</button></div>
          <div class="evidence-list" id="home-episodes"><div class="home-empty">Loading recent episodes…</div></div>
        </section>
      </div>
      <aside class="attention-panel">
        <h3>Outcome breakdown</h3>
        <ul id="home-outcomes"><li>Loading…</li></ul>
      </aside>
    </div>
  </div>
</div>

<!-- Episodes view -->
<div class="view hidden" id="view-episodes">
  <div class="toolbar">
    <button type="button" class="filter-btn active" data-filter="all" onclick="setEpisodesFilter('all')">All</button>
    <button type="button" class="filter-btn" data-filter="succeeded" onclick="setEpisodesFilter('succeeded')">Succeeded</button>
    <button type="button" class="filter-btn" data-filter="abandoned" onclick="setEpisodesFilter('abandoned')">Abandoned</button>
    <button type="button" class="filter-btn" data-filter="bounced" onclick="setEpisodesFilter('bounced')">Bounced</button>
    <button type="button" class="filter-btn" data-filter="errored" onclick="setEpisodesFilter('errored')">Errored</button>
  </div>
  <div class="card-list" id="episodes-list"><div class="empty">Loading episodes…</div></div>
</div>

<!-- Playbooks view -->
<div class="view hidden" id="view-playbooks">
  <div class="card-list" id="playbooks-list"><div class="empty">Loading playbooks…</div></div>
</div>

<!-- Gaps view -->
<div class="view hidden" id="view-gaps">
  <div class="card-list" id="gaps-list"><div class="empty">Loading knowledge gaps…</div></div>
</div>

  </main>
</div>

<!-- Detail modal -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <button class="modal-close" id="modal-close">×</button>
    <div id="modal-content"></div>
  </div>
</div>

<script>
const tabs = document.querySelectorAll('.nav-item[data-view]');
const views = document.querySelectorAll('.view');
const pageTitle = document.getElementById('page-title');
const pageTitles = { home: 'Home', episodes: 'Episodes', playbooks: 'Playbooks', gaps: 'Knowledge gaps' };
const loaders = { home: loadHome, episodes: loadEpisodes, playbooks: loadPlaybooks, gaps: loadGaps };
const loaded = {};

function openView(view) {
  const target = document.getElementById('view-' + view);
  if (!target) return;
  tabs.forEach(t => {
    const selected = t.dataset.view === view;
    t.classList.toggle('active', selected);
    if (selected) t.setAttribute('aria-current', 'page'); else t.removeAttribute('aria-current');
  });
  views.forEach(v => v.classList.add('hidden'));
  target.classList.remove('hidden');
  pageTitle.textContent = pageTitles[view] || 'capman insights';
  if (loaders[view] && !loaded[view]) { loaders[view](); loaded[view] = true; }
}
tabs.forEach(tab => tab.addEventListener('click', () => openView(tab.dataset.view)));
document.querySelectorAll('[data-view-link]').forEach(b => b.addEventListener('click', () => openView(b.dataset.viewLink)));

const statusEl = document.getElementById('status');
fetch('/health').then(r => r.json())
  .then(() => { statusEl.textContent = 'Connected'; statusEl.classList.add('is-online'); })
  .catch(() => { statusEl.textContent = 'Offline'; statusEl.classList.remove('is-online'); });

function refreshCounts() {
  fetch('/api/summary').then(r => r.json()).then(d => {
    document.getElementById('count-episodes').textContent = d.episodes || 0;
    document.getElementById('count-playbooks').textContent = d.playbooks || 0;
    document.getElementById('count-gaps').textContent = d.gaps || 0;
  }).catch(() => {});
}
refreshCounts();
setInterval(refreshCounts, 30000);

function escapeHtml(s) {
  return (s || '').toString()
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function relativeTime(unixSeconds) {
  if (!unixSeconds) return 'Unknown';
  const ms = unixSeconds * 1000;
  const minutes = Math.max(0, Math.floor((Date.now() - ms) / 60000));
  if (minutes < 2) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
function evidenceRow(title, meta, trailing, onClick) {
  const row = document.createElement('button');
  row.type = 'button'; row.className = 'evidence-row';
  row.innerHTML = `<span><span class="evidence-title"></span><span class="evidence-meta"></span></span><span class="evidence-trailing"></span>`;
  row.querySelector('.evidence-title').textContent = title;
  row.querySelector('.evidence-meta').textContent = meta;
  row.querySelector('.evidence-trailing').textContent = trailing;
  row.addEventListener('click', onClick);
  return row;
}
function outcomeTagClass(outcome) {
  if (!outcome) return '';
  if (outcome === 'succeeded') return 'tag-succeeded';
  if (outcome.startsWith('abandoned')) return 'tag-abandoned';
  if (outcome === 'errored') return 'tag-errored';
  if (outcome === 'bounced') return 'tag-bounced';
  return '';
}

// ====================================================================
// Modal
// ====================================================================
const modal = document.getElementById('modal');
const modalContent = document.getElementById('modal-content');
document.getElementById('modal-close').onclick = () => modal.classList.remove('show');
modal.onclick = e => { if (e.target === modal) modal.classList.remove('show'); };
function openModal(html) { modalContent.innerHTML = html; modal.classList.add('show'); }

// ====================================================================
// Home
// ====================================================================
let _allEpisodesCache = [];
async function loadHome() {
  const results = await Promise.allSettled([
    fetch('/api/episodes?limit=8').then(r => r.ok ? r.json() : Promise.reject()),
    fetch('/api/summary').then(r => r.ok ? r.json() : Promise.reject()),
  ]);
  const [episodesResult, summaryResult] = results;

  const episodesEl = document.getElementById('home-episodes');
  episodesEl.innerHTML = '';
  if (episodesResult.status === 'fulfilled' && episodesResult.value.length) {
    episodesResult.value.forEach(ep => {
      const title = ep.problem_statement || (ep.outcome ? `Episode — ${ep.outcome}` : 'Unanalyzed episode');
      const meta = `${ep.outcome || 'pending'} · reached ${ep.step_reached || '—'} · ${ep.n_events} events`;
      episodesEl.appendChild(evidenceRow(title, meta, relativeTime(ep.started_at), () => openEpisode(ep.id)));
    });
  } else {
    episodesEl.innerHTML = '<div class="home-empty">No episodes yet.</div>';
  }

  if (summaryResult.status === 'fulfilled') {
    const d = summaryResult.value;
    document.getElementById('stat-episodes').textContent = d.episodes;
    document.getElementById('stat-analyzed').textContent = d.analyzed;
    document.getElementById('stat-unassigned').textContent = d.unassigned_events;
    document.getElementById('stat-playbooks').textContent = d.playbooks;
    document.getElementById('stat-gaps').textContent = d.gaps;
    const outcomesEl = document.getElementById('home-outcomes');
    if (d.by_outcome && d.by_outcome.length) {
      outcomesEl.innerHTML = d.by_outcome.map(o => `<li><b>${o.count}</b> ${escapeHtml(o.outcome)}</li>`).join('');
    } else {
      outcomesEl.innerHTML = '<li>No episodes analyzed yet.</li>';
    }
  }
}

// ====================================================================
// Episodes
// ====================================================================
let _episodesFilter = 'all';
function setEpisodesFilter(f) {
  _episodesFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.toggle('active', b.dataset.filter === f));
  renderEpisodes();
}
function loadEpisodes() {
  fetch('/api/episodes?limit=200').then(r => r.json()).then(data => {
    _allEpisodesCache = data;
    renderEpisodes();
  });
}
function renderEpisodes() {
  const list = document.getElementById('episodes-list');
  let items = _allEpisodesCache;
  if (_episodesFilter !== 'all') {
    items = items.filter(ep => (ep.outcome || '').startsWith(_episodesFilter));
  }
  if (!items.length) {
    list.innerHTML = '<div class="empty">No episodes match this filter.</div>';
    return;
  }
  list.innerHTML = items.map(ep => `
    <div class="card" onclick="openEpisode('${ep.id}')">
      <div class="card-title">${escapeHtml(ep.problem_statement || 'Unanalyzed episode')}</div>
      <div class="card-meta">
        <span class="${outcomeTagClass(ep.outcome)}">${escapeHtml(ep.outcome || 'pending')}</span>
        <span>reached ${escapeHtml(ep.step_reached || '—')}</span>
        <span>${ep.n_events} events</span>
        ${ep.confidence ? '<span class="tag-score">conf ' + ep.confidence.toFixed(2) + '</span>' : ''}
        ${(ep.friction_flags || []).map(f => '<span>⚠ ' + escapeHtml(f) + '</span>').join('')}
      </div>
      <div class="card-body">${escapeHtml((ep.approach_description || '').slice(0, 220))}</div>
    </div>
  `).join('');
}
function openEpisode(id) {
  const ep = _allEpisodesCache.find(e => e.id === id);
  if (!ep) return;
  let html = `<h2>${escapeHtml(ep.problem_statement || 'Unanalyzed episode')}</h2>`;
  html += `<div class="card-meta" style="margin-bottom:12px">
    <span class="${outcomeTagClass(ep.outcome)}">${escapeHtml(ep.outcome || 'pending')}</span>
    <span>reached ${escapeHtml(ep.step_reached || '—')}</span>
    <span>${ep.n_events} events</span>
    ${ep.confidence ? '<span class="tag-score">confidence ' + ep.confidence.toFixed(2) + '</span>' : ''}
  </div>`;
  if (ep.approach_description) html += `<h3>What happened</h3><p>${escapeHtml(ep.approach_description)}</p>`;
  if (ep.friction_flags && ep.friction_flags.length) html += `<h3>Friction flags</h3><ul>${ep.friction_flags.map(f => '<li>' + escapeHtml(f) + '</li>').join('')}</ul>`;
  if (ep.methodology_tags && ep.methodology_tags.length) html += `<h3>Tags</h3><ul>${ep.methodology_tags.map(t => '<li>' + escapeHtml(t) + '</li>').join('')}</ul>`;
  html += `<h3>Timing</h3><p>Started ${relativeTime(ep.started_at)}${ep.analyzed_at ? ' · analyzed ' + relativeTime(ep.analyzed_at) : ' · not yet analyzed'}</p>`;
  openModal(html);
}

// ====================================================================
// Playbooks
// ====================================================================
function loadPlaybooks() {
  const list = document.getElementById('playbooks-list');
  fetch('/api/playbooks?limit=100').then(r => r.json()).then(data => {
    const pbs = data.playbooks || [];
    document.getElementById('count-playbooks').textContent = data.total || 0;
    if (!pbs.length) {
      list.innerHTML = '<div class="empty">No playbooks extracted yet. They appear once a debugging/friction-shaped cluster of episodes is analyzed.</div>';
      return;
    }
    list.innerHTML = pbs.map(p => `
      <div class="card" onclick="openPlaybook('${p.id}')">
        <div class="card-title">${escapeHtml(p.title)}</div>
        <div class="card-meta">
          <span class="tag-domain">${escapeHtml(p.domain || 'general')}</span>
          <span>${p.diagnostic_step_count} steps</span>
          <span class="tag-score">⭐ ${(p.reusability_score || 0).toFixed(2)}</span>
        </div>
        <div class="card-body">${p.root_cause ? '<strong>Root cause:</strong> ' + escapeHtml(p.root_cause).slice(0,200) : ''}</div>
      </div>
    `).join('');
    window._allPlaybooks = pbs;
  });
}
function openPlaybook(id) {
  const p = (window._allPlaybooks || []).find(x => x.id === id);
  if (!p) return;
  let html = `<h2>${escapeHtml(p.title)}</h2>`;
  html += `<div class="card-meta"><span class="tag-domain">${escapeHtml(p.domain || 'general')}</span> <span class="tag-score">Reusability ${(p.reusability_score || 0).toFixed(2)}</span></div>`;
  if (p.symptoms && p.symptoms.length) html += `<h3>Apply when (Symptoms)</h3><ul>${p.symptoms.map(s => '<li>' + escapeHtml(s) + '</li>').join('')}</ul>`;
  if (p.context_signals && p.context_signals.length) html += `<h3>Context</h3><ul>${p.context_signals.map(s => '<li>' + escapeHtml(s) + '</li>').join('')}</ul>`;
  if (p.diagnostic_steps && p.diagnostic_steps.length) {
    html += `<h3>Diagnostic Steps</h3>`;
    p.diagnostic_steps.forEach(s => {
      html += `<div class="step-card">
        <div class="step-action">${s.sequence || ''}. ${escapeHtml(s.action || '')}</div>
        ${s.tool ? '<div class="step-tool">' + escapeHtml(s.tool) + '</div>' : ''}
        ${s.rationale ? '<div class="step-meta"><strong>Why:</strong> ' + escapeHtml(s.rationale) + '</div>' : ''}
        ${s.expected_signal ? '<div class="step-meta"><strong>Expected:</strong> ' + escapeHtml(s.expected_signal) + '</div>' : ''}
      </div>`;
    });
  }
  if (p.root_cause) html += `<h3>Root Cause</h3><p>${escapeHtml(p.root_cause)}</p>`;
  if (p.fix && p.fix.length) html += `<h3>Fix</h3><ol>${p.fix.map(f => '<li>' + escapeHtml(f) + '</li>').join('')}</ol>`;
  if (p.verification && p.verification.length) html += `<h3>Verification</h3><ul>${p.verification.map(v => '<li>☐ ' + escapeHtml(v) + '</li>').join('')}</ul>`;
  if (p.references && p.references.length) html += `<h3>References</h3><ul>${p.references.map(r => '<li><a href="' + escapeHtml(r) + '" target="_blank" style="color:#7dd3fc">' + escapeHtml(r) + '</a></li>').join('')}</ul>`;
  openModal(html);
}

// ====================================================================
// Knowledge Gaps
// ====================================================================
let _allGaps = [];
function loadGaps() {
  fetch('/api/gaps?limit=100').then(r => r.json()).then(data => {
    _allGaps = data.gaps || [];
    document.getElementById('count-gaps').textContent = data.total || 0;
    renderGaps();
  });
}
function renderGaps() {
  const list = document.getElementById('gaps-list');
  if (!_allGaps.length) {
    list.innerHTML = '<div class="empty">No knowledge gaps tracked yet. Gaps are detected when the same concept comes up repeatedly across sessions.</div>';
    return;
  }
  list.innerHTML = _allGaps.map(g => `
    <div class="card" onclick='openGap(${JSON.stringify(JSON.stringify(g))})'>
      <div class="card-title">${escapeHtml(g.concept)}</div>
      <div class="card-meta">
        <span class="tag-domain">${escapeHtml(g.domain || 'unspecified')}</span>
        <span>Looked up ${g.lookup_count}× in ${g.session_count} session${g.session_count !== 1 ? 's' : ''}</span>
        ${g.last_seen ? '<span>Last: ' + new Date(g.last_seen * 1000).toLocaleDateString() + '</span>' : ''}
      </div>
      ${g.examples && g.examples.length ? `<div class="card-body">${escapeHtml(g.examples[0].slice(0, 140))}</div>` : ''}
    </div>
  `).join('');
}
function openGap(gJson) {
  const g = JSON.parse(gJson);
  let html = `<h2>${escapeHtml(g.concept)}</h2>`;
  html += `<div class="card-meta" style="margin-bottom:12px">
    <span class="tag-domain">${escapeHtml(g.domain || 'unspecified')}</span>
    <span>Looked up <b>${g.lookup_count}</b>× across <b>${g.session_count}</b> sessions</span>
  </div>`;
  if (g.examples && g.examples.length) html += `<h3>Examples</h3><ul>${g.examples.map(e => '<li>' + escapeHtml(e) + '</li>').join('')}</ul>`;
  openModal(html);
}

openView('home');
</script>
</body>
</html>
"""


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>capman2 — Web Insights</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center; background:#0B0D10; font-family: system-ui, -apple-system, sans-serif; }
  .box { width: 100%; max-width: 340px; padding: 28px; border: 1px solid #282E37; border-radius: 12px; background: #12151A; }
  .box h1 { color:#fff; font-size:16px; margin-bottom: 6px; }
  .box p { color:#9AA4B2; font-size: 13px; line-height:1.5; margin-bottom: 18px; }
  input { width:100%; background:#1a1e25; border:1px solid #303844; color:#E6E9EE; border-radius:8px; padding:10px 12px; font-size:14px; outline:none; }
  input:focus { border-color:#2962FF; }
  button { margin-top: 12px; width:100%; background:#2962FF; border:none; color:#fff; border-radius:8px; padding:10px; font-size:14px; font-weight:600; cursor:pointer; }
  button:hover { background:#3D73FF; }
  .err { color:#F28B8B; font-size:12px; margin-top:10px; }
</style>
</head>
<body>
  <form class="box" method="get" action="/">
    <h1>capman2 — Web Insights</h1>
    <p>Enter the access key to view web-behavior analysis.</p>
    <input type="password" name="key" placeholder="Access key" autofocus>
    <button type="submit">Continue</button>
    __ERROR__
  </form>
</body>
</html>
"""
