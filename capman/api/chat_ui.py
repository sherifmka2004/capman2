"""Returns the chat UI HTML as a string."""

CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>capman2 — Knowledge Chat</title>
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
    padding: 14px 20px;
    background: #111;
    border-bottom: 1px solid #222;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  header h1 { font-size: 16px; font-weight: 600; color: #fff; }
  header p  { font-size: 12px; color: #666; }
  .badge {
    background: #22c55e22;
    color: #22c55e;
    border: 1px solid #22c55e44;
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 11px;
    margin-left: auto;
  }

  #messages {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .msg { display: flex; flex-direction: column; max-width: 780px; animation: fadein 0.2s ease; }
  .msg.user      { align-self: flex-end;   align-items: flex-end; }
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
  .assistant .bubble { background: #1a1a1a; border: 1px solid #2a2a2a; color: #e0e0e0; border-bottom-left-radius: 4px; }
  .label { font-size: 11px; color: #444; margin-bottom: 4px; padding: 0 4px; }
  .thinking .bubble { color: #555; font-style: italic; }

  .bubble code { background: #0f0f0f; border: 1px solid #333; border-radius: 4px; padding: 1px 5px; font-family: monospace; font-size: 12px; color: #a5f3fc; }
  .bubble pre  { background: #0f0f0f; border: 1px solid #222; border-radius: 8px; padding: 12px; overflow-x: auto; margin: 8px 0; }
  .bubble pre code { background: none; border: none; padding: 0; }
  .bubble strong { color: #fff; }
  .bubble em     { color: #94a3b8; }
  .bubble h3     { color: #7dd3fc; margin: 8px 0 4px; font-size: 14px; }

  #suggestions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 0 20px 14px;
    background: #111;
  }
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
  button#send {
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
  button#send:hover    { background: #1d4ed8; }
  button#send:disabled { background: #1e3a6a; color: #555; cursor: not-allowed; }

  #scroll-anchor { height: 1px; }
  @keyframes fadein { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; } }
</style>
</head>
<body>

<header>
  <div>
    <h1>capman2 Knowledge Chat</h1>
    <p>Ask anything about your captured sessions, workflows, and knowledge</p>
  </div>
  <div class="badge" id="status">connecting...</div>
</header>

<div id="messages">
  <div class="msg assistant">
    <div class="label">capman2</div>
    <div class="bubble">Hi! I have access to everything captured from your computer activity — searches, URLs visited, terminal commands, documents navigated, and the LLM-extracted chain-of-thought workflows from each work session.

Ask me anything about what you have been working on.</div>
  </div>
  <div id="scroll-anchor"></div>
</div>

<div id="suggestions">
  <span class="chip">What have I been working on today?</span>
  <span class="chip">What URLs did I visit recently?</span>
  <span class="chip">What did I search for about networking?</span>
  <span class="chip">What is my typical troubleshooting workflow?</span>
  <span class="chip">Summarise all my sessions</span>
</div>

<div id="input-area">
  <textarea id="input" placeholder="Ask about your captured knowledge...  (Enter to send, Shift+Enter for new line)" rows="1"></textarea>
  <button id="send" type="button">Send</button>
</div>

<script>
var inputEl       = document.getElementById('input');
var sendBtn       = document.getElementById('send');
var messagesEl    = document.getElementById('messages');
var anchor        = document.getElementById('scroll-anchor');
var statusEl      = document.getElementById('status');
var suggestionsEl = document.getElementById('suggestions');
var chatHistory       = [];
var sending       = false;

// Health check
fetch('/health')
  .then(function(r) { return r.json(); })
  .then(function() {
    statusEl.textContent = 'connected';
    statusEl.style.color = '#22c55e';
  })
  .catch(function() {
    statusEl.textContent = 'offline';
    statusEl.style.color = '#ef4444';
  });

// Suggestion chips
suggestionsEl.addEventListener('click', function(e) {
  if (e.target.classList.contains('chip')) {
    inputEl.value = e.target.textContent;
    doSend();
  }
});

// Enter to send, Shift+Enter for newline
inputEl.addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    doSend();
  }
});

// Auto-resize textarea
inputEl.addEventListener('input', function() {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});

// Send button
sendBtn.addEventListener('click', function() {
  doSend();
});

function scrollToBottom() {
  anchor.scrollIntoView({ behavior: 'smooth' });
}

function addMessage(role, text) {
  var div = document.createElement('div');
  div.className = 'msg ' + role;

  var label = document.createElement('div');
  label.className = 'label';
  label.textContent = role === 'user' ? 'You' : 'capman2';

  var bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = renderMarkdown(text);

  div.appendChild(label);
  div.appendChild(bubble);
  messagesEl.insertBefore(div, anchor);
  scrollToBottom();
  return bubble;
}

function renderMarkdown(text) {
  var out = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/```([\\s\\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/`([^`\\n]+)`/g, '<code>$1</code>')
    .replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>')
    .replace(/\\*([^*\\n]+)\\*/g, '<em>$1</em>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/\\n/g, '<br>');
  return out;
}

function doSend() {
  var text = inputEl.value.trim();
  if (!text || sending) return;

  sending = true;
  sendBtn.disabled = true;
  suggestionsEl.style.display = 'none';

  var userText = text;
  inputEl.value = '';
  inputEl.style.height = '42px';

  addMessage('user', userText);
  chatHistory.push({ role: 'user', content: userText });

  // Thinking indicator
  var thinkingDiv = document.createElement('div');
  thinkingDiv.className = 'msg assistant thinking';
  thinkingDiv.innerHTML = '<div class="label">capman2</div><div class="bubble">Searching your knowledge...</div>';
  messagesEl.insertBefore(thinkingDiv, anchor);
  scrollToBottom();

  fetch('/chat/message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages: chatHistory })
  })
  .then(function(resp) { return resp.json(); })
  .then(function(data) {
    thinkingDiv.remove();
    var reply = data.reply || '(empty response)';
    addMessage('assistant', reply);
    chatHistory.push({ role: 'assistant', content: reply });
    sending = false;
    sendBtn.disabled = false;
    inputEl.focus();
  })
  .catch(function(err) {
    thinkingDiv.remove();
    addMessage('assistant', 'Error: ' + err.message + '. Is the capman server running?');
    sending = false;
    sendBtn.disabled = false;
    inputEl.focus();
  });
}
</script>
</body>
</html>"""
