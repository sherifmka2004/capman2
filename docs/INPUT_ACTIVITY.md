# Input Activity — clicks · scroll · heatmap · idle

`KeyboardSensor` has always captured aggregated keystrokes (with PII
redaction). This doc covers the **mouse / scroll / move / idle** layer that
turns previously raw input into useful, LLM-readable signal — without
ballooning storage.

## What gets captured

| Event              | When                                                          | Payload                                                                                                  |
|--------------------|---------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| `MOUSE_CLICK`      | Every click (one event)                                       | `{button, x, y, element?: {role, label, value?, app}}`                                                   |
| `MOUSE_SCROLL`     | One per *coalesced burst* (debounced, 800 ms quiet ⇒ closed) | `{direction, ticks, duration_s, delta_total, dx, dy, start_x, start_y, end_x, end_y}`                    |
| `MOUSE_HEATMAP_TICK` | One per active app per minute                              | `{app, minute_bucket, grid: {"row,col": count}, grid_size, screen_size}`                                 |
| `IDLE_START`       | No key/click/scroll for `idle_threshold_s`                    | `{last_input_ts}`                                                                                        |
| `IDLE_END`         | First input after an `IDLE_START`                             | `{idle_started_at, idle_duration_s}`                                                                     |

`KEYSTROKE` is unchanged; the keyboard sensor only adds one line — bumping
`activity_context.record_input_activity()` so the AFK detector sees
keystrokes. PII redaction (password-window blocklist + `exclude_apps`) is
**preserved**.

## Element-under-cursor

Clicks gain a `{role, label}` (and optional `value`, `app`) read off the OS
accessibility tree:

| OS       | Backend                                        | Optional dep                |
|----------|------------------------------------------------|-----------------------------|
| macOS    | `AXUIElementCopyElementAtPosition`             | PyObjC `ApplicationServices`|
| Linux    | AT-SPI `getAccessibleAtPoint`                  | `pyatspi`                   |
| Windows  | UIAutomation `ControlFromPoint`                | `uiautomation`              |

When the dep isn't installed (or accessibility permission was denied), the
adapter silently returns `None` and the click is recorded with raw
coordinates only — clicks-without-element are not rendered to the LLM (they
would just be noise). The accessibility call is wrapped in a 50 ms hard
ceiling per click in `MouseSensor._resolve_element_at`, so a slow tree-walk
can't stall the listener thread.

## Scroll coalescing — design

A wheel spin produces dozens of pynput ticks per second; storing each one
would be useless and expensive. `_ScrollCoalescer` (`capman/sensors/mouse.py`)
collapses them:

1. Each tick records `{direction, dx, dy, ts, x, y}` into an in-memory burst.
2. A direction switch (e.g. up → down) **closes** the previous burst
   immediately and starts a new one.
3. The sensor's main loop calls `flush_if_due(now)` once a second; if no tick
   has hit for `scroll_debounce_ms` (default 800), the burst is closed and
   emitted.
4. Bursts shorter than `scroll_min_burst_ticks` (default 3) are silently
   dropped — wheel jitter.

## Move heatmap — design

Mouse-move events would be the highest-volume signal in the system. We never
store them per-event. `_MoveHeatmap` aggregates them into a per-app,
per-minute grid (`100 × 100` cells across the virtual screen, so each cell is
roughly a 19 × 11 px square on a 1920×1080 display).

- One `MOUSE_HEATMAP_TICK` per app per minute hits SQLite. The payload's
  `grid` dict is sparse: only cells that received any moves are listed.
- A workday produces a few thousand ticks at most (active minutes × distinct
  apps per minute), versus millions if we stored each move.
- Mouse-move events explicitly **do not** bump the AFK timer — passive cursor
  jitter from cats, vibrations, or kernel events shouldn't keep the user
  "active".

## Idle / AFK — heuristic

Ported from ActivityWatch's `aw-watcher-afk`:

```
IF time_since_last_input() >= idle_threshold_s AND not currently_idle:
    emit IDLE_START
ELIF currently_idle AND time_since_last_input() < idle_threshold_s:
    emit IDLE_END   // user came back
```

The pipeline treats `IDLE_START` as a hard session-break
(`capman/pipeline/session.py:_HARD_BREAK`), so a 20-minute lunch doesn't get
glued to the morning's debugging session.

## LLM narrative — what surfaces

`build_event_narrative` (`capman/pipeline/prompts.py`) renders:

| Event                | Rendered when                                            | Format                                                            |
|----------------------|----------------------------------------------------------|-------------------------------------------------------------------|
| `MOUSE_CLICK`        | `element.label` is non-empty                              | `[+MM:SS] CLICK   \| <app>     \| <role> "<label>"`                |
| `MOUSE_SCROLL`       | `ticks > 20` OR `duration_s > 5`                          | `[+MM:SS] SCROLL  \| <app>     \| <dir> burst (<dur>s, <ticks>×, Δ<delta>px)` |
| `IDLE_START`         | always                                                    | `[+MM:SS] AFK     \| —          \| user went idle`                  |
| `IDLE_END`           | always                                                    | `[+MM:SS] AFK     \| —          \| user returned (away <dur>s)`     |
| `MOUSE_HEATMAP_TICK` | never                                                     | (data-only event, used by `/storage` and a future heatmap UI)     |

## Chatbot context

`/chat/message` adds an *"Active vs AFK Periods (last 7 days)"* section,
pairing `IDLE_START`/`IDLE_END` rows and computing `active_h`/`idle_h` per
day. So you can ask:

- *"How many hours did I actually work yesterday?"*
- *"What was my longest focus block this week?"*
- *"What did I click on in PyCharm right before lunch?"*

## Configuration

```toml
[sensors]
enabled = ["window", "screenshot", "keyboard", "mouse", "clipboard",
           "shell", "filesystem", "browser_relay", "documents", "idle"]

[sensors.mouse]
track_clicks               = true
resolve_element_at_click   = true
element_lookup_timeout_ms  = 50
track_scroll               = true
scroll_debounce_ms         = 800
scroll_min_burst_ticks     = 3
track_move_heatmap         = true
heatmap_grid               = 100
heatmap_flush_interval_s   = 60

[sensors.idle]
enabled                    = true
idle_threshold_s           = 180
poll_interval_s            = 5
```

Disable mouse capture entirely:
```toml
[sensors]
enabled = ["window", "screenshot", "keyboard", "clipboard", "shell",
           "filesystem", "browser_relay", "documents", "idle"]
```

OCR-style "what did I click on" only — disable scroll + heatmap:
```toml
[sensors.mouse]
track_clicks       = true
resolve_element_at_click = true
track_scroll       = false
track_move_heatmap = false
```

## Storage cost

| Stream            | Per workday                                | Notes                                    |
|-------------------|--------------------------------------------|------------------------------------------|
| `MOUSE_CLICK`     | ~2–6 KiB × clicks (a few hundred per day)  | Element label dominates the size         |
| `MOUSE_SCROLL`    | ~0.4 KiB × bursts (tens to a few hundred)  | One burst is hundreds of raw ticks       |
| `MOUSE_HEATMAP_TICK` | ~0.5–2 KiB × (active_minutes × apps)    | Sparse grid; tiny on focused work        |
| `IDLE_*`          | Negligible (a few pairs per day)           | —                                         |

The Storage tab in the chat UI breaks these out under their own row so you
can see the overhead at a glance.

## References

- [ActivityWatch / `aw-watcher-afk`](https://github.com/ActivityWatch/aw-watcher-afk) — the AFK heuristic
- [ActivityWatch / `aw-watcher-input`](https://github.com/ActivityWatch/aw-watcher-input) — input-event aggregation reference
- [pynput](https://pypi.org/project/pynput/) — cross-platform listener (already in capman2)
- macOS AX docs: `AXUIElementCopyElementAtPosition`
- AT-SPI: `pyatspi.Component.getAccessibleAtPoint`
- Windows: `uiautomation.ControlFromPoint`
