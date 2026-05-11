/**
 * Content script — extracts page text and search result snippets.
 * Runs at document_idle, then again on SPA URL changes and after delays
 * (so dynamically rendered content like Google search results is captured).
 */
(function () {
  let lastSentUrl = "";
  let lastSentTime = 0;

  function extractHeadings() {
    return Array.from(document.querySelectorAll("h1, h2, h3"))
      .map(el => el.textContent.trim())
      .filter(Boolean)
      .slice(0, 10);
  }

  function extractBodyExcerpt() {
    const selectors = ["article", "main", "[role='main']", ".content", ".post-body", "#readme", "#content"];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.textContent.trim().length > 100) {
        return el.textContent.trim().replace(/\s+/g, " ").slice(0, 4000);
      }
    }
    return (document.body?.textContent?.trim() || "").replace(/\s+/g, " ").slice(0, 3000);
  }

  function extractSearchResults() {
    // Generic: any link list inside results-like containers
    const selectors = [
      "div.g h3", "div.MjjYud h3",       // Google
      "li.b_algo h2",                     // Bing
      "article h2", ".result__title",     // DuckDuckGo
    ];
    const out = [];
    for (const sel of selectors) {
      document.querySelectorAll(sel).forEach(el => {
        const text = el.textContent.trim();
        if (text) out.push(text);
      });
      if (out.length > 0) break;
    }
    return out.slice(0, 10);
  }

  function getServerUrl() {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get(["serverUrl"], (data) => {
          resolve((data.serverUrl || "http://localhost:7331").replace(/\/$/, ""));
        });
      } catch (_) {
        resolve("http://localhost:7331");
      }
    });
  }

  async function sendPageText(reason) {
    const url = window.location.href;
    if (url.startsWith("chrome://") || url.startsWith("about:")) return;

    // Throttle: don't resend the same URL within 8s
    const now = Date.now();
    if (url === lastSentUrl && now - lastSentTime < 8000) return;
    lastSentUrl = url;
    lastSentTime = now;

    try {
      const apiBase = await getServerUrl();
      const payload = {
        url,
        title: document.title,
        excerpt: extractBodyExcerpt(),
        headings: extractHeadings(),
        search_results: extractSearchResults(),
        reason: reason || "load",
      };
      await fetch(`${apiBase}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "page_text",
          app: "Browser",
          window_title: document.title,
          payload,
          sensor_id: "browser_relay",
        }),
      });
    } catch (_) {}
  }

  // Initial: send right away, then again after 1.5s so dynamic content has loaded
  sendPageText("initial");
  setTimeout(() => sendPageText("after-render"), 1500);
  setTimeout(() => sendPageText("after-settle"), 4000);

  // Re-capture on SPA navigation (URL changes without page reload)
  let lastUrl = window.location.href;
  new MutationObserver(() => {
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      setTimeout(() => sendPageText("spa-nav"), 1000);
    }
  }).observe(document, { subtree: true, childList: true });

  // Re-capture when tab gets visible
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      setTimeout(() => sendPageText("visibility"), 500);
    }
  });
})();
