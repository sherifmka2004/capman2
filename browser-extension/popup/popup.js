const dot = document.getElementById('dot');
const statusText = document.getElementById('status-text');
const countEl = document.getElementById('count');
const lastEl = document.getElementById('last');
const urlInput = document.getElementById('server-url');
const saveBtn = document.getElementById('save-btn');

// Load saved state
chrome.storage.local.get(['serverUrl', 'eventCount', 'lastEvent', 'connected'], (data) => {
  urlInput.value = data.serverUrl || 'http://localhost:7331';
  countEl.textContent = data.eventCount || 0;
  lastEl.textContent = data.lastEvent || '—';
  setStatus(data.connected);
});

function setStatus(connected) {
  if (connected) {
    dot.className = 'dot';
    statusText.textContent = 'Connected';
  } else {
    dot.className = 'dot off';
    statusText.textContent = 'Disconnected';
  }
}

saveBtn.addEventListener('click', () => {
  const url = urlInput.value.trim().replace(/\/$/, '');
  chrome.storage.local.set({ serverUrl: url });
  // Ping health endpoint
  fetch(`${url}/health`)
    .then(r => r.json())
    .then(() => {
      chrome.storage.local.set({ connected: true });
      setStatus(true);
    })
    .catch(() => {
      chrome.storage.local.set({ connected: false });
      setStatus(false);
    });
});
