export interface CwEvent {
  seq: number;
  t_rel_ms: number;
  name: string;
  props: Record<string, unknown>;
}

export interface CapmanWebConfig {
  /** Same-origin proxy route that forwards to the collector, e.g. "/cw/collect". */
  endpoint: string;
  /**
   * Same-origin proxy route that forwards to the collector's /identify, e.g.
   * "/cw/identify". Optional — omit it and `identify()` becomes a no-op.
   * Linking this tab's anonymous sid to a real email is a deliberate,
   * separate opt-in: it does not change what `track()` sends or how the
   * event pipeline validates it (see capman/web/manifest.py) — it only
   * writes to the collector's dedicated identity side table.
   */
  identifyEndpoint?: string;
  /** Flush when this many events are queued. Default 20 (server batch cap is 50). */
  bufferSize?: number;
  /** Periodic flush interval. Default 10_000 ms. */
  flushIntervalMs?: number;
  /** Ring-buffer capacity; oldest events are dropped past this. Default 200. */
  maxBufferEvents?: number;
  /** Event name -> funnel step (emitted by `capman web codegen`). Enables backtrack detection. */
  steps?: Record<string, string>;
  /** Funnel order (emitted by `capman web codegen`). */
  stepOrder?: string[];
  /** Auto friction detectors (rage/dead click, retry, field abandon). Default true. */
  autoFriction?: boolean;
}

export interface CapmanWebClient {
  track(name: string, props?: Record<string, unknown>): void;
  trackError(codeClass: string): void;
  flush(): void;
  /**
   * Link this tab's sid to a real email, e.g. once after the visitor is
   * known to be authenticated. Call at most as often as the identity can
   * actually change (e.g. once per session) — every call is a network
   * request and a write. No-ops if `identifyEndpoint` was not configured
   * or DNT/GPC put this client in permanent no-op mode.
   */
  identify(email: string): void;
  readonly sid: string;
  readonly enabled: boolean;
}
