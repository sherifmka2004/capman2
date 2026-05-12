"""capman2 web UI — chat + playbook browser + knowledge gap dashboard."""

CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>capman2 — Cognitive Capture</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: system-ui, -apple-system, sans-serif;
    background: #0a0a0a;
    color: #e0e0e0;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }

  header {
    padding: 12px 20px;
    background: #111;
    border-bottom: 1px solid #222;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  header h1 { font-size: 15px; font-weight: 600; color: #fff; }
  .badge {
    background: #22c55e22;
    color: #22c55e;
    border: 1px solid #22c55e44;
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 11px;
    margin-left: auto;
  }

  /* ---------- Tabs ---------- */
  .tabs {
    display: flex;
    background: #0d0d0d;
    border-bottom: 1px solid #222;
    padding: 0 20px;
    gap: 4px;
  }
  .tab {
    padding: 10px 18px;
    cursor: pointer;
    font-size: 13px;
    color: #666;
    border-bottom: 2px solid transparent;
    transition: all 0.15s;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .tab:hover { color: #aaa; }
  .tab.active {
    color: #7dd3fc;
    border-bottom-color: #2563eb;
  }
  .tab-count {
    background: #1a1a1a;
    border-radius: 10px;
    padding: 1px 8px;
    font-size: 11px;
    color: #888;
  }
  .tab.active .tab-count { background: #2563eb22; color: #7dd3fc; }

  .view {
    flex: 1;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .view.hidden { display: none; }

  /* ---------- Chat view ---------- */
  #messages {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .msg { display: flex; flex-direction: column; max-width: 780px; animation: fadein 0.2s ease; }
  .msg.user      { align-self: flex-end; align-items: flex-end; }
  .msg.assistant { align-self: flex-start; align-items: flex-start; }
  .bubble {
    padding: 10px 14px;
    border-radius: 12px;
    font-size: 14px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .user      .bubble { background: #2563eb; color: #fff; border-bottom-right-radius: 4px; }
  .assistant .bubble { background: #1a1a1a; border: 1px solid #2a2a2a; border-bottom-left-radius: 4px; }
  .label { font-size: 11px; color: #444; margin-bottom: 4px; padding: 0 4px; }
  .thinking .bubble { color: #555; font-style: italic; }
  .bubble code { background: #0f0f0f; border: 1px solid #333; border-radius: 4px; padding: 1px 5px; font-family: monospace; font-size: 12px; color: #a5f3fc; }
  .bubble pre  { background: #0f0f0f; border: 1px solid #222; border-radius: 8px; padding: 12px; overflow-x: auto; margin: 8px 0; }
  .bubble pre code { background: none; border: none; padding: 0; }
  .bubble strong { color: #fff; }
  .bubble em     { color: #94a3b8; }
  .bubble h3     { color: #7dd3fc; margin: 8px 0 4px; font-size: 14px; }

  #suggestions { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 20px 14px; background: #111; }
  .chip {
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-radius: 20px;
    padding: 5px 12px;
    font-size: 12px;
    color: #888;
    cursor: pointer;
    user-select: none;
    transition: all 0.15s;
  }
  .chip:hover { border-color: #2563eb; color: #7dd3fc; }

  #input-area {
    padding: 16px 20px;
    background: #111;
    border-top: 1px solid #222;
    display: flex;
    gap: 10px;
    align-items: flex-end;
  }
  textarea {
    flex: 1;
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-radius: 10px;
    color: #e0e0e0;
    font-size: 14px;
    padding: 10px 14px;
    resize: none;
    outline: none;
    max-height: 120px;
    min-height: 42px;
    line-height: 1.5;
    font-family: inherit;
  }
  textarea:focus { border-color: #2563eb; }
  textarea::placeholder { color: #444; }
  button.primary {
    background: #2563eb;
    border: none;
    border-radius: 10px;
    color: white;
    padding: 0 18px;
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    height: 42px;
    white-space: nowrap;
    transition: background 0.15s;
  }
  button.primary:hover    { background: #1d4ed8; }
  button.primary:disabled { background: #1e3a6a; color: #555; cursor: not-allowed; }

  /* ---------- Cards (playbooks, gaps, sessions) ---------- */
  .card-list {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
    gap: 14px;
    align-content: start;
  }
  .card {
    background: #111;
    border: 1px solid #222;
    border-radius: 10px;
    padding: 16px;
    transition: all 0.15s;
    cursor: pointer;
  }
  .card:hover { border-color: #2563eb; transform: translateY(-1px); }
  .card-title { font-size: 14px; font-weight: 600; color: #fff; margin-bottom: 6px; }
  .card-meta  { font-size: 11px; color: #666; margin-bottom: 8px; display: flex; gap: 10px; flex-wrap: wrap; }
  .card-meta span { background: #1a1a1a; padding: 2px 8px; border-radius: 10px; }
  .card-meta .domain { color: #7dd3fc; }
  .card-meta .score  { color: #22c55e; }
  .card-body { font-size: 13px; color: #aaa; line-height: 1.5; }
  .card-body ul { padding-left: 18px; margin: 4px 0; }
  .empty {
    grid-column: 1/-1;
    text-align: center;
    color: #555;
    padding: 60px 20px;
    font-style: italic;
  }

  /* ---------- Detail modal ---------- */
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.85);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 100;
    padding: 30px;
  }
  .modal-overlay.show { display: flex; }
  .modal {
    background: #0f0f0f;
    border: 1px solid #2a2a2a;
    border-radius: 12px;
    max-width: 800px;
    width: 100%;
    max-height: 90vh;
    overflow-y: auto;
    padding: 28px;
    position: relative;
  }
  .modal h2 { color: #fff; margin-bottom: 12px; font-size: 18px; }
  .modal h3 { color: #7dd3fc; font-size: 14px; margin: 18px 0 8px; }
  .modal p, .modal li { color: #ccc; line-height: 1.6; font-size: 13px; }
  .modal ol, .modal ul { padding-left: 24px; }
  .modal-close {
    position: absolute;
    top: 12px;
    right: 16px;
    background: none;
    border: none;
    color: #888;
    font-size: 22px;
    cursor: pointer;
  }
  .modal-close:hover { color: #fff; }
  .step-card {
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-radius: 8px;
    padding: 12px;
    margin: 8px 0;
  }
  .step-card .step-action { font-weight: 600; color: #fff; margin-bottom: 4px; }
  .step-card .step-tool { color: #a5f3fc; font-family: monospace; font-size: 12px; }
  .step-card .step-meta { color: #888; font-size: 12px; margin-top: 4px; }

  /* ---------- Context Suggest view ---------- */
  .context-input {
    padding: 20px;
    background: #111;
    border-bottom: 1px solid #222;
    display: flex;
    gap: 10px;
  }
  .context-input textarea {
    min-height: 60px;
  }
  .context-results {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
  }
  .ctx-section {
    margin-bottom: 24px;
  }
  .ctx-section h3 {
    color: #7dd3fc;
    font-size: 13px;
    text-transform: uppercase;
    margin-bottom: 8px;
    letter-spacing: 0.5px;
  }
  .ctx-card {
    background: #111;
    border-left: 3px solid #2563eb;
    padding: 10px 14px;
    margin-bottom: 8px;
    border-radius: 0 6px 6px 0;
  }
  .ctx-card .ctx-title { color: #fff; font-weight: 500; font-size: 13px; }
  .ctx-card .ctx-detail { color: #aaa; font-size: 12px; margin-top: 4px; }

  #scroll-anchor { height: 1px; }
  @keyframes fadein { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; } }
</style>
</head>
<body>

<header>
  <h1>capman2 — Cognitive Capture Engine</h1>
  <div class="badge" id="status">connecting...</div>
</header>

<div class="tabs">
  <div class="tab active" data-view="chat">💬 Chat</div>
  <div class="tab" data-view="playbooks">📘 Playbooks <span class="tab-count" id="count-playbooks">0</span></div>
  <div class="tab" data-view="gaps">🎯 Knowledge Gaps <span class="tab-count" id="count-gaps">0</span></div>
  <div class="tab" data-view="sessions">📅 Sessions <span class="tab-count" id="count-sessions">0</span></div>
  <div class="tab" data-view="context">⚡ Context Suggest</div>
</div>

<!-- Chat view -->
<div class="view" id="view-chat">
  <div id="messages">
    <div class="msg assistant">
      <div class="label">capman2</div>
      <div class="bubble">Hi! I have access to everything captured from your computer activity — searches, URLs, commands, documents, page content, and the LLM-extracted chain-of-thought workflows + troubleshooting playbooks from each work session.

Switch tabs above to browse playbooks, knowledge gaps, or get context suggestions for a task. Or just ask me anything below.</div>
    </div>
    <div id="scroll-anchor"></div>
  </div>
  <div id="suggestions">
    <span class="chip">What have I been working on today?</span>
    <span class="chip">What URLs did I visit recently?</span>
    <span class="chip">What is my typical troubleshooting workflow?</span>
    <span class="chip">What knowledge gaps do I have?</span>
    <span class="chip">Show me my recent playbooks</span>
  </div>
  <div id="input-area">
    <textarea id="input" placeholder="Ask anything about your captured knowledge... (Enter to send, Shift+Enter for new line)" rows="1"></textarea>
    <button class="primary" id="send">Send</button>
  </div>
</div>

<!-- Playbooks view -->
<div class="view hidden" id="view-playbooks">
  <div class="card-list" id="playbooks-list">
    <div class="empty">Loading playbooks...</div>
  </div>
</div>

<!-- Gaps view -->
<div class="view hidden" id="view-gaps">
  <div class="card-list" id="gaps-list">
    <div class="empty">Loading knowledge gaps...</div>
  </div>
</div>

<!-- Sessions view -->
<div class="view hidden" id="view-sessions">
  <div class="card-list" id="sessions-list">
    <div class="empty">Loading sessions...</div>
  </div>
</div>

<!-- Context Suggest view -->
<div class="view hidden" id="view-context">
  <div class="context-input">
    <textarea id="ctx-input" placeholder="Describe what you're about to do — e.g. 'debug a 502 from nginx', 'fix React hydration issue', 'investigate L3VPN traffic loss'..." rows="2"></textarea>
    <button class="primary" id="ctx-go">Suggest</button>
  </div>
  <div class="context-results" id="ctx-results">
    <div class="empty">Type a task above and click Suggest to see what capman knows that could help.</div>
  </div>
</div>

<!-- Detail modal -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <button class="modal-close" id="modal-close">×</button>
    <div id="modal-content"></div>
  </div>
</div>

<script>
// ====================================================================
// Tab switching
// ====================================================================
const tabs = document.querySelectorAll('.tab');
const views = document.querySelectorAll('.view');
const loaders = {
  playbooks: loadPlaybooks,
  gaps:      loadGaps,
  sessions:  loadSessions,
};
const loaded = {};
tabs.forEach(tab => {
  tab.addEventListener('click', () => {
    const view = tab.dataset.view;
    tabs.forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    views.forEach(v => v.classList.add('hidden'));
    document.getElementById('view-' + view).classList.remove('hidden');
    if (loaders[view] && !loaded[view]) {
      loaders[view]();
      loaded[view] = true;
    }
  });
});

// ====================================================================
// Health
// ====================================================================
const statusEl = document.getElementById('status');
fetch('/health')
  .then(r => r.json())
  .then(() => { statusEl.textContent = 'connected'; statusEl.style.color = '#22c55e'; })
  .catch(() => { statusEl.textContent = 'offline'; statusEl.style.color = '#ef4444'; });

// Refresh tab counts
function refreshCounts() {
  fetch('/knowledge/playbooks?limit=1').then(r=>r.json()).then(d => {
    document.getElementById('count-playbooks').textContent = d.total || 0;
  });
  fetch('/knowledge/gaps?top=1').then(r=>r.json()).then(d => {
    document.getElementById('count-gaps').textContent = d.total || 0;
  });
  fetch('/sessions?limit=1').then(r=>r.json()).then(d => {
    document.getElementById('count-sessions').textContent = (d.sessions || []).length || '?';
  });
}
refreshCounts();
setInterval(refreshCounts, 30000);

// ====================================================================
// Modal
// ====================================================================
const modal = document.getElementById('modal');
const modalContent = document.getElementById('modal-content');
document.getElementById('modal-close').onclick = () => modal.classList.remove('show');
modal.onclick = e => { if (e.target === modal) modal.classList.remove('show'); };

function openModal(html) {
  modalContent.innerHTML = html;
  modal.classList.add('show');
}

// ====================================================================
// Playbooks tab
// ====================================================================
function loadPlaybooks() {
  const list = document.getElementById('playbooks-list');
  fetch('/knowledge/playbooks?limit=100')
    .then(r => r.json())
    .then(data => {
      const pbs = data.playbooks || [];
      document.getElementById('count-playbooks').textContent = data.total || 0;
      if (!pbs.length) {
        list.innerHTML = '<div class="empty">No playbooks extracted yet. They get created automatically after debugging/troubleshooting sessions are analyzed.</div>';
        return;
      }
      list.innerHTML = pbs.map(p => `
        <div class="card" onclick="openPlaybook('${p.id}')">
          <div class="card-title">${escapeHtml(p.title)}</div>
          <div class="card-meta">
            <span class="domain">${escapeHtml(p.domain || 'general')}</span>
            <span>${p.diagnostic_step_count} steps</span>
            <span class="score">⭐ ${(p.reusability_score || 0).toFixed(2)}</span>
          </div>
          <div class="card-body">
            ${p.root_cause ? '<strong>Root cause:</strong> ' + escapeHtml(p.root_cause).slice(0,200) : ''}
            ${p.symptoms && p.symptoms.length ? '<br><strong>Triggers:</strong> ' + p.symptoms.slice(0,2).map(s => escapeHtml(s).slice(0,80)).join('; ') : ''}
          </div>
        </div>
      `).join('');
    });
}

function openPlaybook(id) {
  fetch('/knowledge/playbooks/' + id)
    .then(r => r.json())
    .then(p => {
      let html = `<h2>${escapeHtml(p.title)}</h2>`;
      html += `<div class="card-meta"><span class="domain">${escapeHtml(p.domain || 'general')}</span> <span class="score">Reusability ${(p.reusability_score || 0).toFixed(2)}</span></div>`;
      if (p.symptoms && p.symptoms.length) {
        html += `<h3>Apply when (Symptoms)</h3><ul>${p.symptoms.map(s => '<li>' + escapeHtml(s) + '</li>').join('')}</ul>`;
      }
      if (p.context_signals && p.context_signals.length) {
        html += `<h3>Context</h3><ul>${p.context_signals.map(s => '<li>' + escapeHtml(s) + '</li>').join('')}</ul>`;
      }
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
      if (p.root_cause) {
        html += `<h3>Root Cause</h3><p>${escapeHtml(p.root_cause)}</p>`;
      }
      if (p.fix && p.fix.length) {
        html += `<h3>Fix</h3><ol>${p.fix.map(f => '<li>' + escapeHtml(f) + '</li>').join('')}</ol>`;
      }
      if (p.verification && p.verification.length) {
        html += `<h3>Verification</h3><ul>${p.verification.map(v => '<li>☐ ' + escapeHtml(v) + '</li>').join('')}</ul>`;
      }
      if (p.references && p.references.length) {
        html += `<h3>References</h3><ul>${p.references.map(r => '<li><a href="' + escapeHtml(r) + '" target="_blank" style="color:#7dd3fc">' + escapeHtml(r) + '</a></li>').join('')}</ul>`;
      }
      openModal(html);
    });
}

// ====================================================================
// Knowledge Gaps tab
// ====================================================================
function loadGaps() {
  const list = document.getElementById('gaps-list');
  fetch('/knowledge/gaps?top=50')
    .then(r => r.json())
    .then(data => {
      const gaps = data.gaps || [];
      document.getElementById('count-gaps').textContent = data.total || 0;
      if (!gaps.length) {
        list.innerHTML = '<div class="empty">No knowledge gaps tracked yet. Gaps are detected when you repeatedly look up the same concept across sessions.</div>';
        return;
      }
      list.innerHTML = gaps.map(g => `
        <div class="card">
          <div class="card-title">${escapeHtml(g.concept)}</div>
          <div class="card-meta">
            <span class="domain">${escapeHtml(g.domain || 'unspecified')}</span>
            <span>Looked up ${g.lookup_count}× in ${g.session_count} sessions</span>
          </div>
          <div class="card-body">
            <strong>Examples:</strong>
            <ul>${(g.examples || []).slice(0,3).map(e => '<li>' + escapeHtml(e) + '</li>').join('')}</ul>
          </div>
        </div>
      `).join('');
    });
}

// ====================================================================
// Sessions tab
// ====================================================================
function loadSessions() {
  const list = document.getElementById('sessions-list');
  fetch('/sessions?limit=50')
    .then(r => r.json())
    .then(data => {
      const sessions = data.sessions || [];
      document.getElementById('count-sessions').textContent = sessions.length;
      if (!sessions.length) {
        list.innerHTML = '<div class="empty">No sessions yet — use your computer for a few minutes and they will be detected automatically.</div>';
        return;
      }
      list.innerHTML = sessions.map(s => {
        const start = new Date(s.started_at * 1000).toLocaleString();
        const end = s.ended_at ? new Date(s.ended_at * 1000).toLocaleString() : 'ongoing';
        const dur = s.ended_at ? Math.round((s.ended_at - s.started_at) / 60) + 'min' : '...';
        return `
          <div class="card" onclick="openSession('${s.id}')">
            <div class="card-title">${escapeHtml(s.dominant_app || '(unknown app)')}  <span style="color:#666;font-weight:normal">${dur}</span></div>
            <div class="card-meta">
              <span>${start}</span>
              <span>${s.event_count} events</span>
              <span class="${s.analyzed === 1 ? 'score' : ''}">${s.analyzed === 1 ? '✓ analyzed' : (s.analyzed === 2 ? 'skipped' : 'pending')}</span>
            </div>
          </div>
        `;
      }).join('');
    });
}

function openSession(id) {
  fetch('/sessions/' + id)
    .then(r => r.json())
    .then(d => {
      const s = d.session || {};
      const a = d.analysis || {};
      let html = `<h2>Session ${id.slice(0,8)}</h2>`;
      html += `<p style="color:#888">${new Date(s.started_at * 1000).toLocaleString()} — ${s.event_count || 0} events</p>`;
      if (a.problem_statement) {
        html += `<h3>Problem</h3><p>${escapeHtml(a.problem_statement)}</p>`;
        html += `<h3>Approach</h3><p>${escapeHtml(a.approach_description || '')}</p>`;
        if (a.chain_of_thought) {
          try {
            const cot = JSON.parse(a.chain_of_thought);
            html += `<h3>Methodology Pattern</h3><p style="color:#7dd3fc;font-family:monospace">${escapeHtml(cot.methodology_pattern || '')}</p>`;
            if (cot.steps) {
              html += `<h3>Cognitive Steps</h3>`;
              cot.steps.forEach(st => {
                html += `<div class="step-card">
                  <div class="step-action">${st.sequence}. [${escapeHtml(st.action)}] ${escapeHtml(st.target).slice(0,120)}</div>
                  <div class="step-meta">${escapeHtml(st.reasoning)}</div>
                </div>`;
              });
            }
            if (cot.knowledge_gaps_revealed && cot.knowledge_gaps_revealed.length) {
              html += `<h3>Knowledge Gaps Revealed</h3><ul>${cot.knowledge_gaps_revealed.map(g => '<li>' + escapeHtml(g) + '</li>').join('')}</ul>`;
            }
          } catch(e) {}
        }
      } else {
        html += `<p style="color:#888"><em>Not yet analyzed</em></p>`;
      }
      openModal(html);
    });
}

// ====================================================================
// Context Suggest tab
// ====================================================================
document.getElementById('ctx-go').onclick = runContextSuggest;
document.getElementById('ctx-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); runContextSuggest(); }
});

function runContextSuggest() {
  const task = document.getElementById('ctx-input').value.trim();
  if (!task) return;
  const out = document.getElementById('ctx-results');
  out.innerHTML = '<div class="empty">Searching prior knowledge...</div>';

  fetch('/context/suggest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task, top_k: 5 })
  })
  .then(r => r.json())
  .then(d => {
    let html = '';
    if (d.playbooks && d.playbooks.length) {
      html += `<div class="ctx-section"><h3>Matching Playbooks</h3>`;
      d.playbooks.forEach(p => {
        html += `<div class="ctx-card">
          <div class="ctx-title">${escapeHtml(p.title)}</div>
          <div class="ctx-detail">Score ${(p.score || 0).toFixed(2)} · ${escapeHtml(p.domain || '')}</div>
          ${p.root_cause ? '<div class="ctx-detail"><strong>Root cause:</strong> ' + escapeHtml(p.root_cause) + '</div>' : ''}
          ${p.fix && p.fix.length ? '<div class="ctx-detail"><strong>Fix:</strong> ' + p.fix.map(f => escapeHtml(f)).join(' → ') + '</div>' : ''}
        </div>`;
      });
      html += `</div>`;
    }
    if (d.similar_sessions && d.similar_sessions.length) {
      html += `<div class="ctx-section"><h3>Similar Past Sessions</h3>`;
      d.similar_sessions.forEach(s => {
        html += `<div class="ctx-card">
          <div class="ctx-title">${escapeHtml(s.problem_statement)}</div>
          <div class="ctx-detail">Pattern: <span style="color:#7dd3fc">${escapeHtml(s.methodology_pattern || '')}</span></div>
        </div>`;
      });
      html += `</div>`;
    }
    if (d.related_concepts && d.related_concepts.length) {
      html += `<div class="ctx-section"><h3>Related Knowledge</h3>`;
      d.related_concepts.forEach(c => {
        html += `<div class="ctx-card">
          <div class="ctx-title">${escapeHtml(c.title)}</div>
          <div class="ctx-detail">${escapeHtml(c.summary)}</div>
        </div>`;
      });
      html += `</div>`;
    }
    if (d.page_excerpts && d.page_excerpts.length) {
      html += `<div class="ctx-section"><h3>Pages You've Read</h3>`;
      d.page_excerpts.forEach(p => {
        html += `<div class="ctx-card">
          <div class="ctx-title"><a href="${escapeHtml(p.url)}" target="_blank" style="color:#7dd3fc">${escapeHtml(p.title || p.url)}</a></div>
          <div class="ctx-detail">${escapeHtml(p.excerpt).slice(0, 300)}...</div>
        </div>`;
      });
      html += `</div>`;
    }
    if (d.knowledge_gaps && d.knowledge_gaps.length) {
      html += `<div class="ctx-section"><h3>⚠️ Active Knowledge Gaps</h3>`;
      d.knowledge_gaps.forEach(g => {
        html += `<div class="ctx-card">
          <div class="ctx-title">${escapeHtml(g.concept)}</div>
          <div class="ctx-detail">Looked up ${g.lookup_count}× — recurring lookup, you may want to deep-dive this</div>
        </div>`;
      });
      html += `</div>`;
    }
    if (!html) html = '<div class="empty">No prior knowledge found yet. Keep using capman — the more sessions captured, the richer this gets.</div>';
    out.innerHTML = html;
  })
  .catch(err => {
    out.innerHTML = '<div class="empty">Error: ' + escapeHtml(err.message) + '</div>';
  });
}

// ====================================================================
// Chat
// ====================================================================
const inputEl       = document.getElementById('input');
const sendBtn       = document.getElementById('send');
const messagesEl    = document.getElementById('messages');
const anchor        = document.getElementById('scroll-anchor');
const suggestionsEl = document.getElementById('suggestions');
let chatHistory     = [];
let sending         = false;

suggestionsEl.addEventListener('click', e => {
  if (e.target.classList.contains('chip')) {
    inputEl.value = e.target.textContent;
    doSend();
  }
});

inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSend(); }
});

inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});

sendBtn.addEventListener('click', doSend);

function scrollToBottom() { anchor.scrollIntoView({ behavior: 'smooth' }); }

function addMessage(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  const label = document.createElement('div');
  label.className = 'label';
  label.textContent = role === 'user' ? 'You' : 'capman2';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = renderMarkdown(text);
  div.appendChild(label);
  div.appendChild(bubble);
  messagesEl.insertBefore(div, anchor);
  scrollToBottom();
  return bubble;
}

function escapeHtml(s) {
  return (s || '').toString()
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function renderMarkdown(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/```([\\s\\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/`([^`\\n]+)`/g, '<code>$1</code>')
    .replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>')
    .replace(/\\*([^*\\n]+)\\*/g, '<em>$1</em>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/\\n/g, '<br>');
}

function doSend() {
  const text = inputEl.value.trim();
  if (!text || sending) return;
  sending = true;
  sendBtn.disabled = true;
  suggestionsEl.style.display = 'none';
  const userText = text;
  inputEl.value = '';
  inputEl.style.height = '42px';
  addMessage('user', userText);
  chatHistory.push({ role: 'user', content: userText });

  const thinkingDiv = document.createElement('div');
  thinkingDiv.className = 'msg assistant thinking';
  thinkingDiv.innerHTML = '<div class="label">capman2</div><div class="bubble">Searching your knowledge...</div>';
  messagesEl.insertBefore(thinkingDiv, anchor);
  scrollToBottom();

  fetch('/chat/message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages: chatHistory })
  })
  .then(r => r.json())
  .then(d => {
    thinkingDiv.remove();
    const reply = d.reply || '(empty response)';
    addMessage('assistant', reply);
    chatHistory.push({ role: 'assistant', content: reply });
    sending = false;
    sendBtn.disabled = false;
    inputEl.focus();
  })
  .catch(err => {
    thinkingDiv.remove();
    addMessage('assistant', 'Error: ' + err.message);
    sending = false;
    sendBtn.disabled = false;
  });
}
</script>
</body>
</html>"""
