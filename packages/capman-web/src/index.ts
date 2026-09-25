/**
 * capman-web — framework-agnostic, privacy-first behavior SDK.
 *
 * Hard privacy gates, in order:
 *  1. DNT / GPC → the client is a permanent no-op.
 *  2. Identity is a per-tab sessionStorage UUID (cw_sid) — fresh per tab,
 *     gone on close, never linked to any persistent product-side id, UNLESS
 *     the host app deliberately calls `identify(email)` (see below).
 *  3. Only manifest-declared event names and props are accepted by the
 *     collector; this SDK adds queueing and friction detection, nothing more.
 *  4. No DOM text, field values, URLs-with-query, or free text ever leaves
 *     `track()` — `identify()` is the one deliberate, opt-in exception,
 *     gated behind its own `identifyEndpoint` config and writing to the
 *     collector's separate identity side table, never into event props.
 */
import {
  BacktrackTracker,
  DeadClickWatcher,
  FieldAbandonWatcher,
  RageClickDetector,
  RetryDetector,
  SLOW_FIRST_ACTION_MS,
  cwId,
} from "./friction.js";
import type { CapmanWebClient, CapmanWebConfig, CwEvent } from "./types.js";

export * from "./types.js";
export { cwId } from "./friction.js";

const SID_KEY = "cw_sid";
const SERVER_BATCH_CAP = 50;

function respectsPrivacySignals(): boolean {
  const nav = navigator as Navigator & { msDoNotTrack?: string; globalPrivacyControl?: boolean };
  return (
    nav.doNotTrack === "1" ||
    nav.doNotTrack === "yes" ||
    nav.msDoNotTrack === "1" ||
    (window as unknown as { doNotTrack?: string }).doNotTrack === "1" ||
    nav.globalPrivacyControl === true
  );
}

function visitId(): string {
  try {
    const existing = sessionStorage.getItem(SID_KEY);
    if (existing) return existing;
    const fresh =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
    sessionStorage.setItem(SID_KEY, fresh);
    return fresh;
  } catch {
    return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
  }
}

function noopClient(): CapmanWebClient {
  return {
    track() {},
    trackError() {},
    flush() {},
    identify() {},
    sid: "",
    enabled: false,
  };
}

export function createCapmanWeb(config: CapmanWebConfig): CapmanWebClient {
  if (respectsPrivacySignals()) return noopClient();

  const bufferSize = config.bufferSize ?? 20;
  const flushIntervalMs = config.flushIntervalMs ?? 10_000;
  const maxBufferEvents = config.maxBufferEvents ?? 200;
  const autoFriction = config.autoFriction ?? true;

  const sid = visitId();
  const t0 = performance.now();
  let seq = 0;
  let buffer: CwEvent[] = [];
  let firstActionChecked = false;

  const push = (name: string, props: Record<string, unknown>): void => {
    const t_rel_ms = Math.round(performance.now() - t0);
    buffer.push({ seq: seq++, t_rel_ms, name, props });
    if (buffer.length > maxBufferEvents) {
      buffer = buffer.slice(buffer.length - maxBufferEvents);
    }
  };

  const client: CapmanWebClient = {
    sid,
    enabled: true,
    track(name, props = {}) {
      if (!firstActionChecked) {
        firstActionChecked = true;
        const elapsed = Math.round(performance.now() - t0);
        if (elapsed > SLOW_FIRST_ACTION_MS) {
          push("time_to_first_action", { ms: elapsed });
        }
      }
      push(name, props);
      retryDetector?.onEvent(name, performance.now() - t0);
      backtrackTracker?.onEvent(name);
      if (buffer.length >= bufferSize) client.flush();
    },
    trackError(codeClass) {
      push("error_shown", { code_class: codeClass });
    },
    identify(email) {
      if (!config.identifyEndpoint) return;
      const trimmed = (email || "").trim();
      if (!trimmed) return;
      const dedupeKey = `cw_identified:${sid}`;
      try {
        if (sessionStorage.getItem(dedupeKey) === trimmed) return;
        sessionStorage.setItem(dedupeKey, trimmed);
      } catch {
        // sessionStorage unavailable — fall through and send anyway
      }
      void fetch(config.identifyEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sid, email: trimmed }),
        keepalive: true,
      }).catch(() => {});
    },
    flush() {
      if (!buffer.length) return;
      const events = buffer;
      buffer = [];
      for (let i = 0; i < events.length; i += SERVER_BATCH_CAP) {
        const chunk = events.slice(i, i + SERVER_BATCH_CAP);
        const payload = JSON.stringify({ sid, events: chunk });
        let sent = false;
        if (typeof navigator.sendBeacon === "function") {
          sent = navigator.sendBeacon(
            config.endpoint,
            new Blob([payload], { type: "application/json" }),
          );
        }
        if (!sent) {
          void fetch(config.endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: payload,
            keepalive: true,
          }).catch(() => {});
        }
      }
    },
  };

  const emitReserved = (name: string, props: Record<string, unknown>) => push(name, props);

  let rageDetector: RageClickDetector | undefined;
  let deadWatcher: DeadClickWatcher | undefined;
  let retryDetector: RetryDetector | undefined;
  let backtrackTracker: BacktrackTracker | undefined;

  if (autoFriction) {
    rageDetector = new RageClickDetector(emitReserved);
    deadWatcher = new DeadClickWatcher(emitReserved);
    retryDetector = new RetryDetector(emitReserved);
    new FieldAbandonWatcher(emitReserved);
    document.addEventListener(
      "click",
      (e) => {
        const element = cwId(e.target);
        const now = performance.now() - t0;
        rageDetector?.onClick(element, now);
        deadWatcher?.onClick(element);
      },
      { capture: true, passive: true },
    );
  }
  if (config.steps && config.stepOrder) {
    backtrackTracker = new BacktrackTracker(emitReserved, config.steps, config.stepOrder);
  }

  const interval = setInterval(() => client.flush(), flushIntervalMs);
  const onHidden = () => {
    if (document.visibilityState === "hidden") client.flush();
  };
  document.addEventListener("visibilitychange", onHidden);
  window.addEventListener("pagehide", () => client.flush(), { once: true });
  window.addEventListener("beforeunload", () => {
    clearInterval(interval);
    document.removeEventListener("visibilitychange", onHidden);
  });

  return client;
}
