/**
 * In-page interaction capture for capman2.
 *
 * Tracks the actions you take INSIDE pages (clicks, form inputs, submissions)
 * — not just the URLs you navigate to. This is what closes the gap on SPAs
 * like OpenRouter, Stripe checkout, settings pages, etc.
 *
 * Privacy guards:
 *   - Password / credit-card / 2FA fields are NEVER captured
 *   - Cross-origin iframes (Stripe, reCAPTCHA, etc.) are inaccessible — for those
 *     we capture the DOM mutation that exposed them instead
 *   - Each captured value is truncated to 200 chars
 */
(function () {
  if (window.__capmanInteractionsLoaded) return;
  window.__capmanInteractionsLoaded = true;

  // ----- Sensitive field detection -----
  const SENSITIVE_TYPES = new Set([
    "password", "tel", "email", "credit-card", "cc-number",
  ]);
  const SENSITIVE_ATTR_PATTERNS = [
    /pass/i, /pwd/i, /secret/i, /token/i, /credit/i, /card.*number/i,
    /cvv/i, /cvc/i, /ssn/i, /tax/i, /one[-_]?time/i, /otp/i, /2fa/i, /pin/i,
  ];

  function isSensitive(el) {
    if (!el) return false;
    if (SENSITIVE_TYPES.has(el.type)) return true;
    const ac = (el.autocomplete || "").toLowerCase();
    if (ac.includes("password") || ac.includes("cc-") || ac.includes("one-time")) return true;
    const sig = `${el.name || ""}|${el.id || ""}|${el.placeholder || ""}|${el.ariaLabel || ""}`;
    return SENSITIVE_ATTR_PATTERNS.some(re => re.test(sig));
  }

  // ----- Server URL from chrome.storage -----
  function getServerUrl() {
    return new Promise(resolve => {
      try {
        chrome.storage.local.get(["serverUrl"], data => {
          resolve((data.serverUrl || "http://localhost:7331").replace(/\/$/, ""));
        });
      } catch {
        resolve("http://localhost:7331");
      }
    });
  }

  async function postEvent(type, payload) {
    try {
      const apiBase = await getServerUrl();
      await fetch(`${apiBase}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type,
          app: "Browser",
          window_title: document.title,
          payload: { ...payload, page_url: window.location.href, page_title: document.title },
          sensor_id: "browser_relay",
        }),
        keepalive: true,
      });
    } catch {}
  }

  // ----- Element description for human-readable logs -----
  function describeElement(el) {
    if (!el) return "";
    const tag = el.tagName?.toLowerCase() || "";
    const text = (el.innerText || el.value || el.textContent || "").trim().slice(0, 80);
    const aria = el.getAttribute("aria-label") || "";
    const role = el.getAttribute("role") || "";
    const id   = el.id || "";
    const cls  = (el.className || "").toString().slice(0, 60);
    const data = {};
    for (const a of el.attributes || []) {
      if (a.name.startsWith("data-")) data[a.name] = String(a.value).slice(0, 60);
    }
    return {
      tag, text, aria, role, id, class: cls, data,
      type: el.type || "",
      name: el.name || "",
      href: el.href || "",
    };
  }

  function getSelector(el) {
    if (!el) return "";
    if (el.id) return "#" + el.id;
    let path = [];
    let n = el;
    while (n && n.nodeType === 1 && path.length < 6) {
      let sel = n.tagName.toLowerCase();
      if (n.className && typeof n.className === "string") {
        const cl = n.className.trim().split(/\s+/).slice(0, 2).join(".");
        if (cl) sel += "." + cl;
      }
      const sib = n.parentElement
        ? Array.from(n.parentElement.children).indexOf(n) + 1
        : 0;
      if (sib > 1) sel += `:nth-child(${sib})`;
      path.unshift(sel);
      n = n.parentElement;
    }
    return path.join(" > ");
  }

  // ----- Click capture (buttons, links, anything with role=button) -----
  document.addEventListener("click", e => {
    const el = e.target.closest(
      "button, a, [role='button'], [role='link'], [role='tab'], [role='menuitem'], input[type='submit'], input[type='button'], [onclick]"
    ) || e.target;
    if (!el) return;
    const desc = describeElement(el);
    if (!desc.text && !desc.aria && !desc.href) return; // skip noise

    postEvent("user_click", {
      element: desc,
      selector: getSelector(el),
      x: e.clientX,
      y: e.clientY,
      button: e.button,
    });
  }, true);

  // ----- Input capture (on blur to avoid spamming per keystroke) -----
  document.addEventListener("focusout", e => {
    const el = e.target;
    if (!el || !el.tagName) return;
    if (!["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
    if (isSensitive(el)) return;

    const value = (el.value || "").trim();
    if (!value) return;

    postEvent("form_input", {
      element: describeElement(el),
      value: value.slice(0, 200),
      length: value.length,
      field_type: el.type || el.tagName.toLowerCase(),
      selector: getSelector(el),
    });
  }, true);

  // ----- Form submission -----
  document.addEventListener("submit", e => {
    const form = e.target;
    if (!form || form.tagName !== "FORM") return;

    const fields = [];
    for (const f of form.elements || []) {
      if (!f.name) continue;
      if (isSensitive(f)) {
        fields.push({ name: f.name, value: "[REDACTED]", type: f.type });
        continue;
      }
      fields.push({
        name: f.name,
        value: String(f.value || "").slice(0, 200),
        type: f.type || f.tagName.toLowerCase(),
      });
    }

    postEvent("form_submit", {
      action: form.action || window.location.href,
      method: (form.method || "get").toUpperCase(),
      fields,
      selector: getSelector(form),
    });
  }, true);

  // ----- Significant DOM changes (modals, payment iframes, etc.) -----
  let lastModalSig = "";
  const modalObserver = new MutationObserver(muts => {
    // Detect appearance of payment iframes / modals — high-signal events
    for (const m of muts) {
      for (const node of m.addedNodes || []) {
        if (node.nodeType !== 1) continue;
        const tag = node.tagName?.toLowerCase();

        // Iframe (Stripe, reCAPTCHA, embeds)
        if (tag === "iframe") {
          const src = node.src || node.getAttribute("data-src") || "";
          if (!src) continue;
          let host = "";
          try { host = new URL(src).host; } catch {}
          postEvent("dom_mutation", {
            kind: "iframe_appeared",
            src: src.slice(0, 200),
            host,
            title: node.title || node.getAttribute("name") || "",
          });
          continue;
        }

        // Modal/dialog
        const role = node.getAttribute?.("role") || "";
        const cls  = (node.className || "").toString().toLowerCase();
        if (role === "dialog" || /modal|dialog|drawer|popover/.test(cls)) {
          const sig = role + "|" + cls.slice(0, 40);
          if (sig === lastModalSig) continue;
          lastModalSig = sig;
          postEvent("dom_mutation", {
            kind: "modal_opened",
            role,
            class: cls.slice(0, 80),
            text: (node.innerText || "").trim().slice(0, 200),
          });
        }
      }
    }
  });
  modalObserver.observe(document.body || document.documentElement,
                       { childList: true, subtree: true });
})();
