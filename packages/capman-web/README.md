# @capman/web

Framework-agnostic, privacy-first web-behavior SDK for [capman2](../../README.md).
Zero dependencies. Pairs with the capman2 **collector** service and a product
**manifest** — the SDK itself has no product knowledge.

## Privacy contract (hard gates)

1. **DNT / GPC honored** — if either signal is present the client is a permanent no-op.
2. **Per-visit identity** — a `sessionStorage` UUID (`cw_sid`), fresh per tab, gone on
   close. Never link it to a persistent product-side user id.
3. **Closed vocabulary** — the collector only accepts event names and props declared in
   the product manifest. There is no free-text prop type, so free text cannot be sent.
4. **Element identity from `data-cw` attributes only** — never from DOM text. A button
   labelled `Delete: <person>` contributes `data-cw="delete-btn"` or nothing at all.
   Field values are never read (only a boolean empty/non-empty check for abandon detection).

## Install

The package ships as source TypeScript — vendor it, workspace it, or copy `src/` into
your app:

```ts
import { createCapmanWeb } from "@capman/web";
import { CW_STEPS, CW_STEP_ORDER } from "./cw-generated"; // from capman web codegen
```

## Use

```ts
const cw = createCapmanWeb({
  endpoint: "/cw/collect",     // same-origin proxy route → collector
  steps: CW_STEPS,             // enables backtrack detection
  stepOrder: CW_STEP_ORDER,
});

cw.track("page_view", { path: "/pricing" });
cw.track("widget_click", { target: "save" });
cw.trackError("http_5xx");     // reserved friction event
cw.flush();                    // usually unnecessary — flushes are automatic
```

Mark up elements you want friction detection on:

```html
<button data-cw="export-btn">Export</button>
```

## Flushing

Events queue in an in-memory ring buffer (default cap 200) and flush via
`navigator.sendBeacon` (fetch-keepalive fallback) when:

- the buffer reaches `bufferSize` (default 20),
- `flushIntervalMs` elapses (default 10s),
- the tab goes hidden (`visibilitychange`) or unloads (`pagehide`).

Each event carries `{seq, t_rel_ms, name, props}`; `seq` is monotonic so ordering
survives out-of-order delivery. Batches are chunked to the server's 50-event cap.

## Built-in friction primitives (no manifest entries needed)

| Event | Trigger |
|---|---|
| `rage_click` | ≥3 clicks on the same `data-cw` element within 1.5s |
| `dead_click` | click with no DOM mutation within 2s |
| `retry` | same event name ≥3× within 10s |
| `backtrack` | funnel step drops ≥2 positions (needs `steps`/`stepOrder`) |
| `field_abandon` | `data-cw` field focused ≥5s, non-empty, no submit within 5s |
| `time_to_first_action` | first tracked event >30s after load |
| `zero_results`, `error_shown` | product emits explicitly (`trackError` helper) |

The analyst re-derives these server-side, so a buggy or hostile client cannot
invent or hide friction.

## Codegen

```bash
capman web codegen manifest.toml --lang ts --out src/cw-generated.ts
capman web codegen manifest.toml --lang py --out app/cw_events.py
```

Emits the event-name union, prop types, step map and step order from the same
manifest the collector validates against — client and server cannot drift.

## Development

```bash
npx -p typescript tsc --noEmit   # typecheck (no build step; ships as source)
```
