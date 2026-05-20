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

  /* ── Brain Map ── */
  #view-brain {
    flex: 1; overflow: hidden; display: flex; flex-direction: column;
    background: radial-gradient(ellipse at 45% 45%, #0e0b1e 0%, #060409 55%, #020204 100%);
  }
  .brain-wrap { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
  .brain-hdr {
    display: flex; align-items: center; padding: 10px 20px; flex-shrink: 0;
    border-bottom: 1px solid #1a1530; font-size: 13px; color: #9d84c9; font-weight: 600;
  }
  .brain-hdr button {
    margin-left: auto; background: none; border: 1px solid #2a2245; color: #666;
    padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: 11px;
  }
  .brain-hdr button:hover { color: #aaa; border-color: #444; }
  .brain-stage {
    flex: 1; display: flex; align-items: center; justify-content: center;
    padding: 8px 16px; overflow: hidden; min-height: 0;
  }
  #brain-svg { width: 100%; max-width: 980px; height: auto; overflow: visible; display: block; }
  .brain-footer {
    display: flex; justify-content: center; gap: 28px; padding: 7px 20px;
    border-top: 1px solid #1a1530; flex-shrink: 0; font-size: 11px; color: #3d3a52;
  }
  .brain-footer span::before { content: "· "; color: #2a2245; }
  @keyframes brainBreathe { 0%,100%{transform:scale(1)} 50%{transform:scale(1.013)} }
  @keyframes domPulse     { 0%,100%{opacity:var(--bop)} 50%{opacity:calc(var(--bop)*1.5)} }
  @keyframes connFlow     { to{stroke-dashoffset:-20} }
  @keyframes labelIn      { from{opacity:0;transform:translateY(5px)} to{opacity:1;transform:none} }
  @keyframes ringPulse    { 0%{r:10;opacity:.7} 100%{r:30;opacity:0} }
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
  <div class="tab" data-view="storage">💾 Storage <span class="tab-count" id="count-storage">—</span></div>
  <div class="tab" data-view="context">⚡ Context Suggest</div>
  <div class="tab" data-view="brain">🧠 Brain Map</div>
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

<!-- Storage view -->
<div class="view hidden" id="view-storage">
  <div id="storage-body" style="padding:20px;max-width:900px;margin:0 auto">
    <div class="empty">Loading storage usage...</div>
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

<!-- Brain Map view -->
<div class="view hidden" id="view-brain">
  <div class="brain-wrap">
    <div class="brain-hdr">
      <span>🧠 Mental Map &mdash; Real-time Knowledge Atlas</span>
      <span id="brain-updated" style="color:#4a4468;font-size:11px;margin-left:14px"></span>
      <button id="brain-refresh-btn">&#8635; Refresh</button>
    </div>
    <div class="brain-stage">
      <svg id="brain-svg" viewBox="0 0 900 480" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <radialGradient id="bgBrain" cx="40%" cy="35%" r="62%">
            <stop offset="0%"   stop-color="#24183e"/>
            <stop offset="55%"  stop-color="#16112a"/>
            <stop offset="100%" stop-color="#0c0918"/>
          </radialGradient>
          <radialGradient id="bgCereb" cx="40%" cy="35%" r="62%">
            <stop offset="0%"   stop-color="#1d1632"/>
            <stop offset="100%" stop-color="#0c0918"/>
          </radialGradient>
          <radialGradient id="specular" cx="20%" cy="16%" r="48%">
            <stop offset="0%"   stop-color="#ffffff" stop-opacity="0.08"/>
            <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
          </radialGradient>
          <filter id="brainShadow" x="-18%" y="-18%" width="136%" height="136%">
            <feDropShadow dx="0" dy="10" stdDeviation="22" flood-color="#5b21b6" flood-opacity="0.22"/>
          </filter>
          <filter id="domGlow" x="-130%" y="-130%" width="360%" height="360%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="24" result="b"/>
            <feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
          <filter id="hotGlow" x="-200%" y="-200%" width="500%" height="500%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="5" result="b"/>
            <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
          <filter id="sparkGlow" x="-300%" y="-300%" width="700%" height="700%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="2.5"/>
          </filter>
          <clipPath id="brainClip">
            <path d="M 305,342 C 242,312 206,232 210,190 C 210,132 262,63 305,62 C 372,40 492,33 555,42 C 635,50 720,122 722,190 C 734,254 702,320 676,344 C 634,367 554,374 504,370 C 430,372 382,372 335,358 Z"/>
          </clipPath>
          <clipPath id="cerebClip">
            <path d="M 578,358 C 604,354 642,362 670,348 C 712,332 734,374 732,415 C 730,444 702,458 670,454 C 630,448 594,428 582,402 C 572,376 570,360 578,358 Z"/>
          </clipPath>
          <g id="spark-path-defs"></g>
        </defs>

        <!-- Ambient aura -->
        <ellipse cx="464" cy="212" rx="270" ry="190" fill="#4c1d95" opacity="0.18" style="filter:blur(45px)"/>

        <!-- Cerebellum -->
        <path d="M 578,358 C 604,354 642,362 670,348 C 712,332 734,374 732,415 C 730,444 702,458 670,454 C 630,448 594,428 582,402 C 572,376 570,360 578,358 Z"
              fill="url(#bgCereb)" stroke="#221a3a" stroke-width="1.5" filter="url(#brainShadow)"/>
        <g clip-path="url(#cerebClip)" stroke="#0c0918" stroke-width="1.2" fill="none" opacity="0.95">
          <path d="M 590,365 C 618,361 646,358 670,350"/>
          <path d="M 592,380 C 618,376 644,373 666,366"/>
          <path d="M 592,396 C 616,392 640,389 660,383"/>
          <path d="M 590,411 C 612,408 634,405 652,399"/>
          <path d="M 586,426 C 606,424 626,421 642,416"/>
          <path d="M 580,440 C 598,438 616,436 630,432"/>
          <path d="M 573,452 C 588,450 604,448 618,444"/>
        </g>

        <!-- Main hemisphere -->
        <path d="M 305,342 C 242,312 206,232 210,190 C 210,132 262,63 305,62 C 372,40 492,33 555,42 C 635,50 720,122 722,190 C 734,254 702,320 676,344 C 634,367 554,374 504,370 C 430,372 382,372 335,358 Z"
              fill="url(#bgBrain)" stroke="#221a3a" stroke-width="1.5" filter="url(#brainShadow)"
              style="transform-origin:464px 208px;animation:brainBreathe 5.5s ease-in-out infinite"/>

        <!-- Domain glow blobs (clipped inside brain) -->
        <g clip-path="url(#brainClip)" id="domain-glows"
           style="transform-origin:464px 208px;animation:brainBreathe 5.5s ease-in-out infinite"></g>

        <!-- Sulci — major (thick dark grooves) -->
        <g clip-path="url(#brainClip)" fill="none" stroke="#09071a" stroke-linecap="round"
           style="transform-origin:464px 208px;animation:brainBreathe 5.5s ease-in-out infinite">
          <path stroke-width="2.8" d="M 490,38 C 478,90 460,162 442,235 C 432,272 425,300 422,325"/>
          <path stroke-width="2.4" d="M 320,260 C 378,246 442,240 508,242 C 562,244 612,250 655,258"/>
          <path stroke-width="2.2" d="M 575,38 C 580,84 588,135 596,180"/>
          <path stroke-width="1.8" d="M 522,38 C 510,92 496,164 480,238"/>
          <path stroke-width="1.5" d="M 252,112 C 305,100 360,96 418,100 C 458,104 480,112 485,124"/>
          <path stroke-width="1.5" d="M 248,154 C 298,142 345,138 388,142 C 418,145 438,152 440,165"/>
          <path stroke-width="1.4" d="M 290,204 C 335,196 375,192 412,195 C 438,198 452,205 447,215"/>
          <path stroke-width="1.5" d="M 320,280 C 380,272 440,267 502,270 C 545,273 586,280 620,288"/>
          <path stroke-width="1.4" d="M 328,318 C 382,310 435,307 480,310 C 514,313 542,320 568,328"/>
          <path stroke-width="1.6" d="M 644,98 C 670,120 690,150 696,182 C 700,207 694,232 682,248"/>
          <path stroke-width="1.3" d="M 678,128 C 700,150 718,178 724,204"/>
          <path stroke-width="1.5" d="M 538,112 C 558,134 574,164 576,194 C 578,218 564,238 550,248"/>
          <path stroke-width="1.2" d="M 576,194 C 596,200 620,208 636,220"/>
          <path stroke-width="1.2" d="M 574,226 C 590,233 608,242 620,252"/>
          <path stroke-width="1.2" d="M 252,84 C 295,74 342,71 388,74"/>
          <path stroke-width="1.1" d="M 210,178 C 250,170 280,168 308,170"/>
        </g>

        <!-- Gyri highlights -->
        <g clip-path="url(#brainClip)" fill="none" stroke="#2c2450" stroke-width="0.7" opacity="0.55"
           stroke-linecap="round"
           style="transform-origin:464px 208px;animation:brainBreathe 5.5s ease-in-out infinite">
          <path d="M 488,60 C 476,108 460,178 445,250"/>
          <path d="M 555,55 C 558,100 562,150 568,192"/>
          <path d="M 340,265 C 395,258 452,254 510,257"/>
          <path d="M 265,130 C 315,118 368,114 425,118"/>
          <path d="M 262,172 C 308,162 350,158 390,160"/>
          <path d="M 658,112 C 682,135 700,164 706,195"/>
          <path d="M 552,128 C 568,150 580,178 582,205"/>
        </g>

        <!-- Specular (3D depth) -->
        <g clip-path="url(#brainClip)"
           style="transform-origin:464px 208px;animation:brainBreathe 5.5s ease-in-out infinite">
          <path d="M 305,342 C 242,312 206,232 210,190 C 210,132 262,63 305,62 C 372,40 492,33 555,42 C 635,50 720,122 722,190 C 734,254 702,320 676,344 C 634,367 554,374 504,370 C 430,372 382,372 335,358 Z"
                fill="url(#specular)"/>
        </g>

        <!-- Neural connections (JS-generated) -->
        <g id="brain-connections" fill="none" stroke-linecap="round"></g>

        <!-- Neuron sparks (JS-generated) -->
        <g id="brain-sparks"></g>

        <!-- Hotspot markers (JS-generated, inside breathing group) -->
        <g id="brain-hotspots"
           style="transform-origin:464px 208px;animation:brainBreathe 5.5s ease-in-out infinite"></g>

        <!-- Label cards in margins (JS-generated) -->
        <g id="brain-labels" font-family="system-ui,-apple-system,sans-serif"></g>

        <!-- Label-to-hotspot connectors (JS-generated) -->
        <g id="brain-connectors" fill="none" stroke-linecap="round"></g>
      </svg>
    </div>
    <div class="brain-footer">
      <span id="brain-stat-sessions">—</span>
      <span id="brain-stat-domains">—</span>
      <span id="brain-stat-conns">—</span>
    </div>
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
  storage:   loadStorage,
  brain:     loadBrain,
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

// ====================================================================
// Storage tab
// ====================================================================
const STORAGE_COLORS = ['#3b82f6','#8b5cf6','#ec4899','#f59e0b','#10b981','#06b6d4','#ef4444','#6b7280','#84cc16','#a855f7'];
function loadStorage() {
  const body = document.getElementById('storage-body');
  body.innerHTML = '<div class="empty">Loading storage usage...</div>';
  fetch('/storage').then(r => r.json()).then(d => {
    document.getElementById('count-storage').textContent = d.total_human || '?';
    const comps = (d.components || []).filter(c => c.bytes > 0);
    let html = '';
    html += `<div style="display:flex;align-items:baseline;gap:12px;margin-bottom:4px">
      <span style="font-size:28px;font-weight:700;color:#fff">${escapeHtml(d.total_human || '0 B')}</span>
      <span style="color:#888">total on disk · ${(d.total_files||0).toLocaleString()} files · ${escapeHtml(d.data_dir||'')}</span>
      <button id="storage-refresh" style="margin-left:auto;background:#1e1e1e;border:1px solid #333;color:#bbb;border-radius:4px;padding:4px 10px;cursor:pointer">↻ Refresh</button></div>`;
    if (d.estimated_per_day_human) {
      html += `<div style="color:#999;margin-bottom:16px">Growth ≈ <b style="color:#ddd">${escapeHtml(d.estimated_per_day_human)}/day</b> (~${escapeHtml(d.estimated_per_month_human||'?')}/month), measured over ${d.span_days} days.</div>`;
    } else {
      html += `<div style="color:#999;margin-bottom:16px">Not enough history yet for a growth estimate.</div>`;
    }
    // stacked bar
    html += `<div style="display:flex;height:22px;border-radius:6px;overflow:hidden;border:1px solid #222;margin-bottom:8px">`;
    comps.forEach((c, i) => {
      const w = Math.max(d.total_bytes ? (100*c.bytes/d.total_bytes) : 0, 0);
      html += `<div title="${escapeHtml(c.name)}: ${escapeHtml(c.human)} (${c.pct}%)" style="width:${w}%;background:${STORAGE_COLORS[i % STORAGE_COLORS.length]}"></div>`;
    });
    html += `</div>`;
    // legend / table
    html += `<table style="width:100%;border-collapse:collapse;margin-top:12px">`;
    comps.forEach((c, i) => {
      html += `<tr style="border-bottom:1px solid #1a1a1a">
        <td style="padding:6px 8px;width:14px"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${STORAGE_COLORS[i % STORAGE_COLORS.length]}"></span></td>
        <td style="padding:6px 8px;color:#ddd">${escapeHtml(c.name)}</td>
        <td style="padding:6px 8px;color:#888;font-size:12px;font-family:monospace">${escapeHtml(c.path||'')}</td>
        <td style="padding:6px 8px;color:#888;text-align:right">${(c.files||0).toLocaleString()} files</td>
        <td style="padding:6px 8px;color:#fff;text-align:right;font-weight:600">${escapeHtml(c.human)}</td>
        <td style="padding:6px 8px;color:#888;text-align:right;width:48px">${c.pct}%</td>
      </tr>`;
    });
    html += `</table>`;
    // DB internals
    const db = d.db || {};
    html += `<h3 style="margin:24px 0 8px;color:#fff;font-size:14px">Database contents</h3>`;
    html += `<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px">`;
    const dbItems = [
      ['events','events'],['sessions','sessions'],['session_analyses','analyses'],
      ['playbooks','playbooks'],['knowledge_triples','knowledge facts'],
      ['knowledge_gaps','knowledge gaps'],['screenshots','screenshots'],
    ];
    dbItems.forEach(([k,label]) => {
      if (db[k] != null) html += `<span style="background:#161616;border:1px solid #2a2a2a;border-radius:6px;padding:6px 10px;color:#ccc"><b style="color:#fff">${(db[k]).toLocaleString()}</b> ${label}</span>`;
    });
    html += `</div>`;
    const et = d.event_types || [];
    if (et.length) {
      const max = et[0].count || 1;
      html += `<h3 style="margin:18px 0 8px;color:#fff;font-size:14px">Events by type</h3>`;
      et.forEach(e => {
        const w = Math.max(2, 100 * e.count / max);
        html += `<div style="display:flex;align-items:center;gap:10px;margin:3px 0">
          <span style="width:170px;color:#aaa;font-size:12px;text-align:right">${escapeHtml(e.type)}</span>
          <div style="flex:1;background:#141414;border-radius:3px;overflow:hidden"><div style="height:14px;width:${w}%;background:#3b82f6"></div></div>
          <span style="width:64px;color:#ddd;font-size:12px">${(e.count).toLocaleString()}</span>
        </div>`;
      });
    }
    body.innerHTML = html;
    const rb = document.getElementById('storage-refresh');
    if (rb) rb.addEventListener('click', loadStorage);
  }).catch(e => { body.innerHTML = '<div class="empty">Could not load storage info: ' + escapeHtml(String(e)) + '</div>'; });
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
      } else if (s.analyzed === 2) {
        const dur = s.ended_at && s.started_at ? Math.round(s.ended_at - s.started_at) : null;
        const durStr = dur !== null ? ` (${dur}s)` : '';
        html += `<p style="color:#888"><em>Skipped — session too short${durStr} to meet analysis threshold</em></p>`;
      } else {
        html += `<p style="color:#888"><em>Not yet analyzed</em></p>`;
      }
      const fa = d.file_activity || [];
      if (fa.length) {
        html += `<h3>File Activity <span style="color:#666;font-weight:400">(${fa.length} ops — user-driven)</span></h3>`;
        fa.forEach(f => {
          const t = new Date(f.ts * 1000).toLocaleTimeString();
          const who = f.actor ? ` <span style="color:#888">via ${escapeHtml(f.actor)}</span>` : '';
          if (f.type === 'code_diff') {
            const repo = f.repo ? ` [${escapeHtml(f.repo)}]` : '';
            html += `<div class="step-card">
              <div class="step-action">📝 ${escapeHtml(f.path)}${repo} <span style="color:#4ade80">+${f.lines_added||0}</span>/<span style="color:#f87171">-${f.lines_removed||0}</span>${who}</div>`;
            if (f.diff) {
              const lines = (f.diff || '').split('\\n').slice(0, 30);
              html += `<pre style="background:#0d0d0d;border:1px solid #222;border-radius:4px;padding:8px;overflow:auto;font-size:11px;max-height:240px">${lines.map(l => {
                let cls = '#aaa';
                if (l.startsWith('+') && !l.startsWith('+++')) cls = '#4ade80';
                else if (l.startsWith('-') && !l.startsWith('---')) cls = '#f87171';
                else if (l.startsWith('@@')) cls = '#7dd3fc';
                return '<span style="color:' + cls + '">' + escapeHtml(l) + '</span>';
              }).join('\\n')}</pre>`;
            }
            html += `</div>`;
          } else {
            const verb = {file_open:'📂 opened', file_save:'💾 saved', file_delete:'🗑️ deleted', file_rename:'🔀 renamed'}[f.type] || f.type;
            const what = f.type === 'file_rename'
              ? `${escapeHtml(f.src_path)} → ${escapeHtml(f.path)}`
              : escapeHtml(f.path);
            const cmd = f.via_command ? ` <span style="color:#666">(${escapeHtml((f.via_command||'').slice(0,60))})</span>` : '';
            html += `<div class="step-card"><div class="step-meta"><span style="color:#888">${t}</span> ${verb} ${what}${who}${cmd}</div></div>`;
          }
        });
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

// ====================================================================
// Brain Map
// ====================================================================
let _brainTimer = null;

function loadBrain() {
  fetchBrain();
  _brainTimer = setInterval(fetchBrain, 30000);
  document.getElementById('brain-refresh-btn').addEventListener('click', fetchBrain);
}

function fetchBrain() {
  fetch('/brain').then(r => r.json()).then(renderBrain).catch(e => console.warn('brain:', e));
}

const NS = 'http://www.w3.org/2000/svg';
const XL = 'http://www.w3.org/1999/xlink';

function mkEl(tag, attrs) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'xlinkHref') el.setAttributeNS(XL, 'xlink:href', v);
    else el.setAttribute(k, v);
  }
  return el;
}

function renderBrain(data) {
  // Timestamp
  const elapsed = Math.round(Date.now() / 1000 - (data.last_updated || 0));
  document.getElementById('brain-updated').textContent =
    elapsed < 5 ? 'just now' : elapsed < 60 ? elapsed + 's ago' : Math.round(elapsed/60) + 'm ago';

  // Footer
  const active = (data.categories || []).filter(c => c.weight > 0.05).length;
  document.getElementById('brain-stat-sessions').textContent = (data.total_sessions || 0) + ' sessions analysed';
  document.getElementById('brain-stat-domains').textContent = active + ' active knowledge domains';
  document.getElementById('brain-stat-conns').textContent = (data.connections || []).length + ' neural connections mapped';

  // Clear dynamic layers
  ['domain-glows','brain-connections','brain-sparks','brain-hotspots',
   'brain-labels','brain-connectors','spark-path-defs'].forEach(id => {
    const el = document.getElementById(id); if (el) el.innerHTML = '';
  });

  const cats = data.categories || [];
  const catMap = Object.fromEntries(cats.map(c => [c.id, c]));
  const glows   = document.getElementById('domain-glows');
  const hots    = document.getElementById('brain-hotspots');
  const conns   = document.getElementById('brain-connections');
  const sparks  = document.getElementById('brain-sparks');
  const defs    = document.getElementById('spark-path-defs');
  const labels  = document.getElementById('brain-labels');
  const connectors = document.getElementById('brain-connectors');

  // ── Domain glow blobs ──
  cats.forEach(cat => {
    if (cat.weight < 0.03) return;
    const r = 55 + cat.weight * 85;
    const op = 0.10 + cat.weight * 0.38;
    const speed = (cat.last_active_h !== null && cat.last_active_h < 3) ? '2.2s' : '4.5s';
    const c = mkEl('circle', {cx: cat.hotspot[0], cy: cat.hotspot[1], r, fill: cat.color, filter:'url(#domGlow)'});
    c.style.cssText = `opacity:${op};--bop:${op};animation:domPulse ${speed} ease-in-out infinite`;
    glows.appendChild(c);
  });

  // ── Hotspot markers ──
  cats.forEach(cat => {
    if (cat.weight < 0.03) return;
    const [cx, cy] = cat.hotspot;

    // Pulsing ring for hot regions
    if (cat.weight > 0.18) {
      const ring = mkEl('circle', {cx, cy, r: '10', fill: 'none', stroke: cat.color, 'stroke-width': '1.2'});
      ring.innerHTML = `
        <animate attributeName="r" from="10" to="30" dur="2.8s" repeatCount="indefinite"/>
        <animate attributeName="opacity" from="0.7" to="0" dur="2.8s" repeatCount="indefinite"/>`;
      hots.appendChild(ring);
    }

    // Core dot
    const dot = mkEl('circle', {cx, cy, r: 4.5 + cat.weight * 4.5, fill: cat.color, filter: 'url(#hotGlow)'});
    dot.style.opacity = 0.82 + cat.weight * 0.18;
    hots.appendChild(dot);

    // Small inner bright dot
    const inner = mkEl('circle', {cx, cy, r: 2.5, fill: '#ffffff'});
    inner.style.opacity = 0.35 + cat.weight * 0.3;
    hots.appendChild(inner);
  });

  // ── Neural connections + sparks ──
  (data.connections || []).forEach((conn, i) => {
    const from = catMap[conn.from], to = catMap[conn.to];
    if (!from || !to || conn.strength < 0.08 || from.weight < 0.04 || to.weight < 0.04) return;

    const [ax, ay] = from.hotspot, [bx, by] = to.hotspot;
    // Quadratic bezier biased slightly toward brain centre (464, 208)
    const mx = (ax + bx) / 2, my = (ay + by) / 2;
    const qx = mx + (464 - mx) * 0.32, qy = my + (208 - my) * 0.32;
    const d = `M ${ax},${ay} Q ${qx},${qy} ${bx},${by}`;

    // Connection line
    const path = mkEl('path', {d, stroke: from.color,
      'stroke-width': 0.7 + conn.strength * 1.6,
      'stroke-opacity': 0.12 + conn.strength * 0.28,
      'stroke-dasharray': '4 7'});
    path.style.animation = `connFlow ${2.8 + i * 0.3}s linear infinite`;
    conns.appendChild(path);

    // Hidden path for animateMotion
    const pid = `sp-${conn.from}-${conn.to}`;
    defs.appendChild(mkEl('path', {id: pid, d}));

    // Sparks
    const numSparks = conn.strength > 0.5 ? 3 : 2;
    const dur = (2.2 + conn.strength * 1.8).toFixed(1);
    for (let k = 0; k < numSparks; k++) {
      const spark = mkEl('circle', {r: '2', fill: from.color, filter: 'url(#sparkGlow)'});
      const begin = (k * parseFloat(dur) / numSparks).toFixed(2);
      const mot = mkEl('animateMotion', {dur: `${dur}s`, begin: `${begin}s`, repeatCount: 'indefinite'});
      mot.appendChild(mkEl('mpath', {xlinkHref: '#' + pid}));
      spark.appendChild(mot);
      sparks.appendChild(spark);
    }
    // Reverse spark
    if (conn.strength > 0.25) {
      const sr = mkEl('circle', {r: '1.6', fill: to.color, filter: 'url(#sparkGlow)'});
      const mr = mkEl('animateMotion', {dur: `${(parseFloat(dur)*1.4).toFixed(1)}s`,
        begin: `${(parseFloat(dur)*0.45).toFixed(2)}s`, repeatCount: 'indefinite',
        keyPoints: '1;0', keyTimes: '0;1', calcMode: 'linear'});
      mr.appendChild(mkEl('mpath', {xlinkHref: '#' + pid}));
      sr.appendChild(mr);
      sparks.appendChild(sr);
    }
  });

  // ── Label cards ──
  const W = 164, H = 96;
  cats.forEach(cat => {
    if (cat.weight < 0.02 && cat.session_count === 0) return;
    const [lx, ly] = cat.label_anchor;
    const [hx, hy] = cat.hotspot;
    const isRight = lx > 450, isTop = ly < 40;
    const alpha = Math.min(1, 0.35 + cat.weight * 1.3);
    const pct = Math.round(cat.weight * 100);
    const barW = Math.round((W - 20) * cat.weight);

    const g = mkEl('g', {});
    g.style.cssText = `opacity:${alpha};animation:labelIn 0.7s ease both`;

    // Card background
    g.appendChild(mkEl('rect', {x: lx, y: ly, width: W, height: H, rx: 8, ry: 8,
      fill: '#060410', stroke: cat.color, 'stroke-width': '0.9', 'stroke-opacity': '0.7'}));

    // Domain name
    const title = mkEl('text', {x: lx+10, y: ly+15, 'font-size': '10',
      'font-weight': '700', fill: cat.color});
    title.textContent = cat.name;
    g.appendChild(title);

    // Percentage — right-aligned
    const pctEl = mkEl('text', {x: lx+W-10, y: ly+15, 'font-size': '11',
      'font-weight': '800', fill: '#fff', 'text-anchor': 'end'});
    pctEl.textContent = pct + '%';
    g.appendChild(pctEl);

    // Progress bar track
    g.appendChild(mkEl('rect', {x: lx+10, y: ly+21, width: W-20, height: 3,
      rx: 1.5, fill: '#1a1a2e'}));
    // Progress bar fill
    if (barW > 0) {
      g.appendChild(mkEl('rect', {x: lx+10, y: ly+21, width: barW, height: 3,
        rx: 1.5, fill: cat.color, 'fill-opacity': '0.85'}));
    }

    // Sub-topics (up to 3)
    const topicList = (cat.topics || []).slice(0, 3);
    topicList.forEach((topic, j) => {
      // bullet dot
      g.appendChild(mkEl('circle', {cx: lx+13, cy: ly+35+j*14-1.5, r: 2,
        fill: cat.color, 'fill-opacity': '0.6'}));
      const t = mkEl('text', {x: lx+20, y: ly+35+j*14, 'font-size': '8.5', fill: '#8a8aaa'});
      t.textContent = topic.length > 20 ? topic.slice(0, 20) + '…' : topic;
      g.appendChild(t);
    });
    // Fill empty topic rows with placeholder dashes so layout is consistent
    for (let j = topicList.length; j < 3; j++) {
      const t = mkEl('text', {x: lx+20, y: ly+35+j*14, 'font-size': '8.5', fill: '#2a2a3e'});
      t.textContent = '—';
      g.appendChild(t);
    }

    // Meta row
    const meta = mkEl('text', {x: lx+10, y: ly+H-8, 'font-size': '8', fill: '#3a3a58'});
    let m = cat.session_count > 0 ? `${cat.session_count} session${cat.session_count>1?'s':''}` : 'no sessions';
    if (cat.last_active_h !== null)
      m += ` · ${cat.last_active_h < 1 ? '<1h' : Math.round(cat.last_active_h)+'h'} ago`;
    meta.textContent = m;
    g.appendChild(meta);

    labels.appendChild(g);

    // Connector line (nearest label edge → hotspot)
    let x1, y1;
    if (isTop)        { x1 = lx + W/2; y1 = ly + H; }
    else if (isRight) { x1 = lx;       y1 = Math.min(ly + H/2, hy + 20); }
    else              { x1 = lx + W;   y1 = Math.min(ly + H/2, hy + 20); }

    const line = mkEl('line', {x1, y1, x2: hx, y2: hy,
      stroke: cat.color, 'stroke-width': '0.75',
      'stroke-opacity': Math.min(0.5, 0.18 + cat.weight * 0.4),
      'stroke-dasharray': '3,4'});
    connectors.appendChild(line);
  });
}
</script>
</body>
</html>"""
