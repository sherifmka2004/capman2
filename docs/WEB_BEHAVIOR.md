# capman2 web behavior — anonymous intent & friction analysis

Generic, product-agnostic web-behavior analytics built on capman2's reasoning
half: the 4-pass LLM pipeline that turns a captured session into an
evidence-linked, reusable playbook. The capture half (desktop sensors) is
**not** part of this path — `KEYSTROKE`, `FORM_INPUT`, `PAGE_TEXT` and
`SCREENSHOT` event types are never used here.

A **product** supplies a manifest (data). capman2 supplies collection,
segmentation, clustering, analysis and verification (code). Adding a product
means writing a manifest and provisioning a tenant — no capman2 code changes.

```
product frontend                      Railway                       Railway (cron 15m)
┌──────────────────┐   same-origin   ┌──────────────┐             ┌─────────────────┐
│ capman-web SDK   │ ─ POST /cw/collect ► proxy route ► collector ─►│  Postgres (RLS) │
│  manifest-typed  │   (strips IP,   │ POST /collect│  validate   │  events         │
│  sendBeacon      │    adds key)    └──────────────┘  vs manifest│  web_episodes   │
└──────────────────┘                                            └────────┬────────┘
                                     analyst worker                      │
        SessionDetector(per sid) → signature → cluster → select → 4-pass LLM
                                                                            │
                                     session_analyses · playbooks · knowledge_gaps
                                                                            │
                                                                     Superset dashboards
```

## Concept mapping

| capman2 desktop concept | web-behavior meaning |
|---|---|
| `Event` | one semantic UI action (manifest-declared name + props) |
| `SessionDetector` session | one **task attempt** (episode), not a "visit" |
| Pass 1 `problem_statement` / `approach_description` | user **intent** / the path they actually tried |
| Pass 2 `ChainOfThought` | where the attempt broke and why |
| Pass 4 `playbooks` | **friction playbook**: symptoms → root_cause → fix → verification |
| `knowledge_gaps` | **unmet intent** — repeated attempts with no supported path |
| `knowledge_triples` | friction → cause → fix graph |

## Privacy contract (the hard boundary)

**The collector accepts only what a manifest declares, and a manifest may not
declare a free-text field.** The prop-type vocabulary is closed:

| type | declaration | accepted values | stored as |
|---|---|---|---|
| `enum` | `values = [...]` | declared members only | the member |
| `int` | `max = N` | integers ≤ N | the int |
| `bool` | — | true/false | the bool |
| `bucket` | `buckets = [1, 5, 10]` | any number | bucket **label** (`"5-10"`) |
| `short_string` | `pattern = "^..."` (default `^[a-z0-9_.\-/]+$`) | ≤64 chars matching pattern | the string |

There is no `text` type — that omission is the boundary. Free text is
physically unable to enter the store, so it can never reach the LLM.

Additional guarantees:

- **Never leaves the browser:** field values, document content, filenames,
  uploaded bytes, prompts/outputs, email, name. No screenshots, no session
  replay, no keystrokes.
- **Identity:** per-visit `sessionStorage` UUID (`cw_sid`), fresh per tab,
  gone on close. Stored server-side as `sid_hash` (SHA-256, truncated) on
  episodes. Deliberately unlinkable to any persistent product-side id.
- **IP** is dropped at the product's own same-origin proxy route and never
  forwarded to the collector.
- **DNT/GPC** honored — the SDK no-ops.
- **Element identity** comes only from `data-cw` attributes, never DOM text.
- **Retention:** raw `events` 45 days, `web_episodes` 180 days, derived
  analyses/playbooks/gaps indefinitely (no personal data). Manifest-configurable.
- **Second line of defence:** every LLM *output* string passes through
  `capman.knowledge.privacy.redact_derived_text` before storage.

## The manifest

One TOML file per product (spec + validation in `capman/web/manifest.py`):

```toml
[product]
key         = "acme"          # tenant becomes user_id = "product:acme"
description = "One-line domain context injected into LLM prompts."
glossary    = { widget = "the main interactive surface" }

[retention]
raw_events_days = 45
episodes_days   = 180

[session]                     # web cadence overrides of the desktop defaults
idle_threshold_s = 30
cool_period_s    = 45
hard_break_s     = 1800
min_events       = 3

[steps]                       # ordered funnel; drives abandoned_at_<step>
order = ["land", "start", "edit", "done"]

[outcomes]
success_when = ["export_result.ok == true"]

[[events]]
name  = "widget_click"
step  = "edit"
props = { target = { type = "enum", values = ["save", "cancel"] },
          n = { type = "int", max = 20 } }
```

Worked example: [`examples/manifests/nuralto.toml`](../examples/manifests/nuralto.toml).

### Codegen — client and collector cannot drift

```bash
capman web codegen examples/manifests/nuralto.toml --lang ts --out src/cw-generated.ts
capman web codegen examples/manifests/nuralto.toml --lang py --out app/cw_events.py
```

## Components

| Path | Role |
|---|---|
| `capman/web/manifest.py` | manifest schema + `validate_event` (the boundary) |
| `capman/web/friction.py` | reserved friction events + server-side re-derivation |
| `capman/web/codegen.py` | manifest → TS types / Python Literals |
| `packages/capman-web/` | browser SDK (ring buffer, sendBeacon, DNT/GPC, friction detectors) |
| `services/collector/` | FastAPI: `POST /collect`, `GET /health` — **write-only**, key-authenticated |
| `services/analyst/` | cron worker: segment → signature → select → LLM passes → verify |
| `capman/pipeline/web_analyzer.py` | 4-pass episode analyzer (reuses desktop backend plumbing) |
| `capman/pipeline/prompts_web.py` | product-noun-free prompt templates + `build_web_narrative` |
| `deploy/postgres/init/002_web_behavior.sql` | `web_episodes`, `playbook_verifications` (FORCE RLS) |

### The collector is NOT the capman daemon

`capman/api/server.py` has **no authentication** and exposes reads/controls
(`/query`, `/export`, `/sessions`, `/settings/stop`). It must never be deployed
publicly. The collector is a separate application with exactly one write route,
a per-product shared secret (`X-CW-Key`), per-sid rate limits, batch caps and
manifest validation. Verify after deploy: `/query`, `/export`, `/sessions`,
`/settings/stop` must all 404 on the collector's public URL.

### Analyst pipeline (every 15 min)

1. **segment** — pull unassigned events per tenant, one `SessionDetector`
   instance **per sid** (the detector is a single-subject state machine;
   sharing it would merge two visitors). Idle gaps > `idle+cool` split
   episodes (historical replay of `check_timeouts`). Outcome labels:
   `succeeded` / `abandoned_at_<step>` / `errored` / `bounced`.
2. **signature** — run-length-encode the event-name sequence, hash it. Two
   signatures: exact, and coarse (`first3+last3`) for fuzzy clustering.
3. **select** — LLM budget policy: one representative per cluster with ≥3
   occurrences not analyzed this week; every friction-flagged episode
   (capped/day); every novel signature (capped/day). Everything else is
   counted, not read.
4. **analyze** — Pass 1 intent (haiku, every selected episode) → Pass 2 chain
   of thought (sonnet, severity ≥ 0.5 **and** cluster frequency ≥ 3) → Pass 3
   triples (haiku) → Pass 4 **friction playbook** (sonnet, frequency ×
   severity above threshold). Unmet intent → `knowledge_gaps`.
5. **verify** — for each matured playbook (14-day window elapsed), recompute
   the cluster's episode rate before vs after `created_at`; write a
   `playbook_verifications` row (`improved` / `unchanged` / `regressed` /
   `insufficient_volume`) and resolve linked gaps on `improved`.

`python run.py --dry-run` reports the episode counts, selection and LLM call
estimate for a day's data **without writing or calling** — run it before
enabling a product key.

**Cost at 50–500 visits/day:** ~100–1,000 episodes/day → after clustering,
~20–60 LLM calls/day, mostly haiku. Cents per day.

**Cadence caveat:** at this traffic a friction cluster needs 1–2 weeks to
accumulate enough occurrences for a confident verification verdict. Analysis
runs every 15 minutes; the decide-and-ship loop is bi-weekly. Reviewing daily
means chasing noise.

## Deployment

1. Provision a **separate** Postgres (e.g. Railway service `capman-pg`) —
   behavior volume must not share the product's app DB.
2. Apply `deploy/postgres/init/001_capman_multi_user.sql` +
   `002_web_behavior.sql` (auto on first boot via compose; `psql -f` for
   existing volumes).
3. `./bootstrap.sh add-product <key>` → tenant `product:<key>`, role `cw_<key>`.
4. Deploy `services/collector` (Dockerfile + railway.toml included) with env:
   `CAPMAN_WEB_MANIFESTS` (comma-separated paths), `CAPMAN_WEB_KEYS`
   (`<key>=<secret>,...`), `CAPMAN_PG_DSN`.
5. Deploy `services/analyst` as a cron (`*/15 * * * *`, no inbound port) with
   `CAPMAN_WEB_MANIFESTS`, `CAPMAN_PG_DSN`, and an LLM key
   (`ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY`).
6. In the product frontend: vendor `packages/capman-web`, generate types from
   the manifest, add a **same-origin** proxy route that strips the client IP,
   injects `X-CW-Key` server-side and forwards to the collector (keeps CORS
   and ad-block loss out of the picture).
7. Superset dashboards over `web_episodes`, `session_analyses`, `playbooks`,
   `knowledge_gaps`: First-Action Fork, Step Funnel by signature, Friction
   Leaderboard (frequency × severity — the work queue), Open Playbooks,
   Verification Tracker.

## Tests

```bash
.venv/bin/python -m pytest tests/unit/test_web_manifest.py tests/unit/test_web_friction.py \
    tests/unit/test_web_codegen.py services/collector/tests services/analyst/tests -q
```

Covers: the closed type vocabulary (free-text declaration attempts fail),
validation rejections (unknown names/keys, oversized strings/batches, enum and
pattern violations), PII-shaped payloads rejected with nothing written,
two-manifest tenant routing, golden segmentation (including a two-visitor
interleaved log proving per-sid isolation), signature stability, selection
budgets, verification verdict boundaries, and a dry-run cycle.
