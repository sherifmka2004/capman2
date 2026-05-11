const DEFAULT_BASE = "http://localhost:7331";

async function getBase() {
  return new Promise((resolve) => {
    chrome.storage.local.get(['serverUrl'], (data) => {
      resolve(data.serverUrl || DEFAULT_BASE);
    });
  });
}

export async function postEvent(type, payload = {}, app = "Browser", windowTitle = "") {
  try {
    const base = await getBase();
    const resp = await fetch(`${base}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, app, window_title: windowTitle, payload, sensor_id: "browser_relay" }),
    });
    if (resp.ok) {
      // Update count + last event time in storage
      chrome.storage.local.get(['eventCount'], (data) => {
        chrome.storage.local.set({
          eventCount: (data.eventCount || 0) + 1,
          lastEvent: new Date().toLocaleTimeString(),
          connected: true,
        });
      });
    }
  } catch (_) {
    chrome.storage.local.set({ connected: false });
  }
}
