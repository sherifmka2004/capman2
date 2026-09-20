/**
 * Client-side friction detectors — product-independent primitives.
 *
 * PRIVACY RULE: element identity comes ONLY from `data-cw` attributes, never
 * from DOM text. A button labelled "Delete: <person>" must never leak the
 * label — cwId() returns undefined for elements without data-cw, and no
 * detector reads textContent, innerText, value, or aria-label.
 */

export const RAGE_WINDOW_MS = 1500;
export const RAGE_MIN_CLICKS = 3;
export const DEAD_CLICK_TIMEOUT_MS = 2000;
export const RETRY_WINDOW_MS = 10_000;
export const RETRY_MIN_COUNT = 3;
export const BACKTRACK_MIN_DROP = 2;
export const SLOW_FIRST_ACTION_MS = 30_000;
export const FIELD_ABANDON_MIN_FOCUS_MS = 5000;
export const FIELD_ABANDON_GRACE_MS = 5000;

export type EmitFn = (name: string, props: Record<string, unknown>) => void;

export function cwId(target: EventTarget | null): string | undefined {
  if (!(target instanceof Element)) return undefined;
  const holder = target.closest("[data-cw]");
  return holder?.getAttribute("data-cw") ?? undefined;
}

export class RageClickDetector {
  private hits = new Map<string, number[]>();

  constructor(private emit: EmitFn) {}

  onClick(element: string | undefined, nowMs: number): void {
    if (!element) return;
    const window = this.hits.get(element) ?? [];
    window.push(nowMs);
    while (window.length && nowMs - window[0] > RAGE_WINDOW_MS) window.shift();
    this.hits.set(element, window);
    if (window.length >= RAGE_MIN_CLICKS) {
      this.emit("rage_click", { element, n: window.length });
    }
  }
}

export class DeadClickWatcher {
  private timer: ReturnType<typeof setTimeout> | undefined;
  private observer: MutationObserver | undefined;
  private mutated = false;

  constructor(private emit: EmitFn) {}

  onClick(element: string | undefined): void {
    this.cancel();
    if (!element || typeof MutationObserver === "undefined") return;
    this.mutated = false;
    this.observer = new MutationObserver(() => {
      this.mutated = true;
    });
    this.observer.observe(document.body, { childList: true, subtree: true, attributes: true });
    this.timer = setTimeout(() => {
      if (!this.mutated) this.emit("dead_click", { element });
      this.cancel();
    }, DEAD_CLICK_TIMEOUT_MS);
  }

  cancel(): void {
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = undefined;
    this.observer?.disconnect();
    this.observer = undefined;
  }
}

export class RetryDetector {
  private seen = new Map<string, number[]>();
  private coolingDown = new Set<string>();

  constructor(private emit: EmitFn) {}

  onEvent(name: string, nowMs: number): void {
    const window = this.seen.get(name) ?? [];
    window.push(nowMs);
    while (window.length && nowMs - window[0] > RETRY_WINDOW_MS) window.shift();
    this.seen.set(name, window);
    if (this.coolingDown.has(name)) {
      if (window.length < RETRY_MIN_COUNT) this.coolingDown.delete(name);
      return;
    }
    if (window.length >= RETRY_MIN_COUNT) {
      this.emit("retry", { action: name, n: window.length });
      this.coolingDown.add(name);
    }
  }
}

export class BacktrackTracker {
  private maxIdx = -1;

  constructor(
    private emit: EmitFn,
    private steps: Record<string, string>,
    private stepOrder: string[],
  ) {}

  onEvent(name: string): void {
    const step = this.steps[name];
    if (!step) return;
    const idx = this.stepOrder.indexOf(step);
    if (idx < 0) return;
    if (this.maxIdx >= 0 && this.maxIdx - idx >= BACKTRACK_MIN_DROP) {
      this.emit("backtrack", { from_step: this.stepOrder[this.maxIdx], to_step: step });
    }
    if (idx > this.maxIdx) this.maxIdx = idx;
  }
}

export class FieldAbandonWatcher {
  private focusStarts = new WeakMap<Element, number>();
  private pending = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(private emit: EmitFn) {
    document.addEventListener(
      "focusin",
      (e) => {
        const el = e.target;
        if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
          this.focusStarts.set(el, Date.now());
        }
      },
      true,
    );
    document.addEventListener(
      "focusout",
      (e) => {
        const el = e.target;
        if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLTextAreaElement)) return;
        const field = cwId(el);
        if (!field) return;
        // Boolean emptiness check only — the value itself is never read out.
        if (!el.value.length) return;
        const started = this.focusStarts.get(el) ?? Date.now();
        if (Date.now() - started < FIELD_ABANDON_MIN_FOCUS_MS) return;
        const form = el.closest("form");
        const timer = setTimeout(() => {
          this.pending.delete(field);
          this.emit("field_abandon", { field });
        }, FIELD_ABANDON_GRACE_MS);
        this.pending.set(field, timer);
        form?.addEventListener(
          "submit",
          () => {
            const t = this.pending.get(field);
            if (t !== undefined) clearTimeout(t);
            this.pending.delete(field);
          },
          { once: true },
        );
      },
      true,
    );
  }
}
