/**
 * capman2 browser extension — background service worker.
 * Tracks tab lifecycle, URL visits, and detects search queries.
 */
import { postEvent } from "./utils/api.js";
import { detectSearch } from "./utils/search.js";

// Track tab open time for duration calculation
const tabOpenTimes = new Map();

chrome.tabs.onCreated.addListener((tab) => {
  tabOpenTimes.set(tab.id, Date.now());
  postEvent("tab_open", {
    tab_id: tab.id,
    url: tab.url || "",
    title: tab.title || "",
  });
});

chrome.tabs.onRemoved.addListener((tabId, removeInfo) => {
  const openedAt = tabOpenTimes.get(tabId);
  const duration_s = openedAt ? (Date.now() - openedAt) / 1000 : 0;
  tabOpenTimes.delete(tabId);
  postEvent("tab_close", { tab_id: tabId, duration_s });
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete" || !tab.url) return;
  if (tab.url.startsWith("chrome://") || tab.url.startsWith("about:")) return;

  const url = tab.url;
  const title = tab.title || "";

  // Detect search query
  const search = detectSearch(url);
  if (search) {
    postEvent("search_query", {
      engine: search.engine,
      query: search.query,
      url,
      result_count: 0,
    }, "Browser", title);
  }

  // Always emit URL visit
  postEvent("url_visit", {
    url,
    title,
    referrer: "",
    visit_duration_s: 0,
  }, "Browser", title);
});

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!tab.url || tab.url.startsWith("chrome://")) return;
    postEvent("window_focus", {}, "Browser", tab.title || tab.url);
  } catch (_) {}
});
