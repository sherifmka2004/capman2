export interface CwEvent {
  seq: number;
  t_rel_ms: number;
  name: string;
  props: Record<string, unknown>;
}

export interface CapmanWebConfig {
  /** Same-origin proxy route that forwards to the collector, e.g. "/cw/collect". */
  endpoint: string;
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
  readonly sid: string;
  readonly enabled: boolean;
}
