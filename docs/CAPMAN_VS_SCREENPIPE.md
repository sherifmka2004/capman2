# capman2 vs. screenpipe — an honest, extensive comparison

Both are open-source, local-first "capture everything I do on my computer" tools.
They are **not really competitors** — they sit at different points on the same
pipeline and are arguably complementary. This doc lays out exactly how they
differ, where each wins, and how you'd use them together.

> Short version: **screenpipe records the most *raw signal* (24/7 screen video +
> audio, OCR'd and transcribed) and gives you a searchable archive + a plugin
> platform.** **capman2 records *structured events* (URLs, searches, shell
> commands with exit codes, file edits with diffs, in-page clicks/forms,
> document navigation) and runs an LLM pipeline that extracts *how you think* —
> chain-of-thought, troubleshooting playbooks, a knowledge graph, a
> knowledge-gap profile — plus an API a coding agent can query before a task.**
> screenpipe answers *"what did I see / hear at 3pm?"*. capman2 answers *"how do
> I debug this class of problem, based on how I've done it before?"*.

---

## 1. Design philosophy

| | **screenpipe** | **capman2** |
|---|---|---|
| Core metaphor | "Your screen + mic, in a database, for AI." A 24/7 **recall layer**. | "Clone the expert's *cognitive workflow*." A **methodology engine**. |
| Primary signal | Pixels & audio → OCR/STT → text. | Typed, structured **events** (URL visit, search query, shell command + exit code, file save + diff, click, form submit, slide change…). |
| What it does with the signal | Indexes it for search; exposes it to "pipes" (plugins) you write. | Runs a **4-pass LLM analysis** per work session → summary → chain-of-thought → knowledge triples → troubleshooting playbook (for debugging sessions). |
| Retrieval model | You search, or you build a pipe. Pull. | You search/chat **and** an IDE/agent can `POST /context/suggest "I'm about to debug X"` and get back your relevant playbooks + similar past sessions. Push-ready. |
| Maturity | Mature, polished, large community, lots of pipes, funded team. | Young, single-developer, rougher edges; the cognitive layer is the bet. |
| Language / stack | Rust core + Tauri/Next.js app. | Python (`asyncio`, FastAPI), Chrome MV3 extension, shell hook. |

---

## 2. Capture modalities — who records what

| Modality | screenpipe | capman2 |
|---|---|---|
| **Continuous screen video** | ✅ per-monitor, mp4 chunks, 24/7 | ❌ — periodic + event-triggered **screenshots** only (with OCR) |
| **Screen OCR** | ✅ on every frame; native OS OCR (Apple Vision / Windows.Media.Ocr) or Tesseract | ✅ on each screenshot (Tesseract; Apple Vision on macOS) |
| **Microphone audio + transcription** | ✅ local Whisper (whisper.cpp) or cloud (Deepgram) | ❌ none |
| **System / meeting audio** | ✅ | ❌ |
| **Active window / app + focus duration** | ⚠️ inferable from OCR/UI metadata | ✅ first-class `window` sensor |
| **Browser URLs / search queries** | ⚠️ only as text on screen (OCR); no structured URL events | ✅ structured `url_visit` / `search_query` events via the extension |
| **Browser page *text* (full content, embedded)** | ⚠️ only what's visible on screen | ✅ extracts page body + headings, chunks & embeds into the vector store |
| **In-page interactions (clicks, form inputs, submits, modals, iframes)** | ❌ | ✅ `interactions.js` content script (with sensitive-field redaction) |
| **AI-chat conversations (ChatGPT/Claude/Cursor)** | ⚠️ only the visible text on screen | ⚠️ partial — captured as page text / clicks (no dedicated structured extractor yet) |
| **Shell / terminal commands** | ⚠️ only if the terminal is on screen and OCR catches it | ✅ real-time hook (bash `DEBUG` trap + zsh `preexec`) → command, cwd, **exit code**, duration, TTY/SSH context, shell PID |
| **Shell output / errors** | ⚠️ via OCR if visible | ⚠️ optional output capture; error/stack-trace tagging is roadmap |
| **File operations (create/save/delete/rename)** | ❌ | ✅ `filesystem` sensor — and attributed to **direct user action** (editor / interactive shell / focused file manager), dropping build-tool/LSP/daemon churn |
| **File content diffs ("you changed line 42 from X to Y")** | ❌ | ✅ unified diff per save (snapshot-based, or `git diff` in repos) → `code_diff` events |
| **True file *opens/reads* + the responsible process (PID/exe/signing-id)** | ❌ | ✅ optional privileged helper `capman-fsmon` — Linux (fanotify/auditd/eBPF) or macOS (Endpoint Security `eslogger`) |
| **Document navigation (slides/pages/sheets, dwell times, jump patterns)** | ❌ | ✅ `documents` sensor (PowerPoint/Keynote/Word/Excel/PDF/notes apps) |
| **Clipboard copy/paste chains** | ❌ | ✅ `clipboard` sensor |
| **Keyboard (aggregated text blocks)** | ⚠️ keystroke timing for some pipes | ✅ `keyboard` sensor (aggregated, not keylogged) |

**Takeaway:** screenpipe wins decisively on *raw multimodal recording* — it has
**audio + 24/7 video**, which capman2 has nothing comparable to. capman2 wins
decisively on *structured digital-trail* capture — terminal, files+diffs,
browser interactions, document navigation, process attribution — none of which
screenpipe records as structured events (it would only "see" them as pixels).

---

## 3. Processing & intelligence — what happens *after* capture

| | screenpipe | capman2 |
|---|---|---|
| Default post-processing | OCR + STT + embeddings; that's it. The "thinking" is left to whatever pipe/LLM you point at the data. | A pipeline runs per detected work **session** (sliding-window IDLE→ACTIVE→COOLING state machine). |
| LLM analysis built in | Optional — some pipes call an LLM (e.g. "meeting summarizer", "daily log"). Not core. | **Core.** 4 passes per qualifying session: ① summarize (haiku) → ② **chain-of-thought** extraction (sonnet): steps, decision points, methodology pattern, reusability score, knowledge gaps → ③ **knowledge triples** (haiku) for a graph → ④ **troubleshooting playbook** (sonnet, debugging sessions only): symptoms → diagnostic steps (rationale / expected signal / tool) → root cause → fix → verification. |
| Knowledge graph | ❌ | ✅ SQLite triples + Obsidian-compatible markdown nodes + edges with weights; merged across sessions. |
| "What I keep looking up" profile | ❌ | ✅ **Knowledge Gap Tracker** — clusters repeated lookups across sessions = your unmastered-concepts profile. |
| Cost / footprint of the intelligence layer | Near-zero by default (no LLM unless a pipe uses one); audio/video make *disk* the cost (GBs/day). | LLM API calls per session (configurable models / can disable); much smaller disk footprint (no video/audio) — capman2 ships a `capman storage` command + `/storage` API + a Storage tab showing the breakdown & growth rate. |

**Takeaway:** This is the heart of it. screenpipe is a *substrate* — it gives you
the data and a place to run plugins; the intelligence is BYO. capman2 is *opinionated* —
it ships the analysis that turns "what I did" into "the methodology I used", and
stores playbooks/triples/gaps as first-class objects.

---

## 4. Retrieval & integration — getting the value back out

| | screenpipe | capman2 |
|---|---|---|
| Full-text / semantic search | ✅ strong (text from OCR/STT, embeddings) | ✅ semantic search over sessions, knowledge nodes, page text, playbooks (ChromaDB) |
| Chat over your history | ✅ via pipes (e.g. an "ask" pipe) | ✅ built-in chat UI; the answer is grounded in recent sessions + URLs + commands + **file activity + diffs** + page content + playbooks + knowledge gaps |
| Timeline / browse-by-time UI | ✅ excellent — scrub your day like a video | ⚠️ a Sessions list with per-session detail (CoT steps, file ops, diffs); no video-scrub timeline |
| **Active "what's my history with X?" API for agents/IDEs** | ❌ (you'd build a pipe) | ✅ `POST /context/suggest` returns playbooks + similar sessions + related concepts + page excerpts + knowledge gaps — designed to be injected into a coding agent's system prompt before a task |
| Local HTTP API | ✅ rich, well-documented | ✅ `/events`, `/sessions`, `/query`, `/knowledge/*`, `/context/suggest`, `/storage`, `/chat`, plus the web UI |
| Obsidian / markdown export | ⚠️ via a pipe | ✅ native — knowledge nodes & playbooks are written as Obsidian-compatible markdown |

---

## 5. Extensibility

| | screenpipe | capman2 |
|---|---|---|
| Plugin system | ✅ **"pipes"** — full Next.js/TS apps that read the DB & API; there's a pipe store and templates. This is a major strength. | ⚠️ **sensor plugins** — drop a `BaseSensor` subclass in `capman/sensors/`, auto-discovered. Plus the `/events` endpoint accepts events from *any* external producer (the browser extension, the shell hook, `capman-fsmon`, or anything you write). No "app store" of plugins. |
| Browser extension | ❌ (sees the browser via screen OCR) | ✅ Chrome MV3 extension is a core component (URLs, page text, clicks, forms) |
| Feeding one tool from the other | — | capman2's `/events` endpoint could ingest screenpipe data (e.g. a pipe that POSTs OCR'd terminal text or transcribed "I said X in the meeting" as events). |

---

## 6. Privacy, platform, maturity

| | screenpipe | capman2 |
|---|---|---|
| Local-first | ✅ 100% local by default; optional cloud OCR/STT | ✅ 100% local capture & storage; LLM analysis calls out to Anthropic/OpenRouter (you control which, or disable) |
| Sensitive-data handling | ✅ PII redaction options | ✅ password/secret field redaction in the browser extension; keyboard sensor excludes password managers; `~/.capman/**` never self-monitored; file monitoring is user-action-only by design. *Note: with `forget about concerns` mode the bias is toward maximum capture — review your config.* |
| Platforms | macOS (best — ScreenCaptureKit), Windows, Linux | macOS, Linux, Windows (display sensors); Linux + macOS for the privileged deep file monitor; **headless** mode (servers/SSH) runs shell + filesystem + browser-relay only |
| Footprint | Disk-heavy (video + audio chunks → many GB/day); CPU for OCR/STT | Disk-light (no video/audio; ~MB/day typical); LLM API cost per session |
| Maturity / ecosystem | Mature, polished desktop app, active community, funded | Young, single-dev, CLI + web UI, rougher; the cognitive layer is the differentiator, not the polish |
| License | MIT | (project license — see repo) |

---

## 7. Where each one clearly wins

**Pick screenpipe if you want:**
- A literal **recording of your screen and meetings** you can scrub and search ("what was on that Slack message at 2:47?", "summarize the standup").
- **Audio / meeting transcription** — capman2 doesn't do this at all.
- A **mature plugin ecosystem** ("pipes") and a polished desktop timeline UI.
- A general-purpose **context substrate** to build your own AI tools on.

**Pick capman2 if you want:**
- To capture the **structured digital trail** — terminal commands with exit codes, file edits *with diffs*, browser searches/clicks/forms, document navigation, process attribution — not just pixels of them.
- The system to **extract methodology**: chain-of-thought per session, **replayable troubleshooting playbooks** (symptom→diagnostic step→fix→verify), a **knowledge graph**, and a **"what I keep looking up" profile**.
- An **API a coding agent / IDE can query before a task** ("what's my history debugging 502s from nginx?") and get back the right playbook.
- Built-in **storage accounting** and a small disk footprint.
- To run it **headless on a server** (capture shell + files + browser-relay with no GUI).

---

## 8. Could you run both? Yes — and they compose well

They overlap almost not at all in *what they store*. A realistic setup:
- **screenpipe** = the 24/7 audio/video recall layer (meetings, "what did I see").
- **capman2** = the cognitive-workflow layer (terminal + files + browser → playbooks/CoT/graph).
- Optional glue: a small **screenpipe pipe** that POSTs salient text (e.g. OCR'd
  errors, transcribed decisions) to capman2's `POST /events`, so screenpipe's
  raw signal feeds capman2's analysis. Conversely, capman2's `/context/suggest`
  could be surfaced inside a screenpipe pipe.

---

## 9. capman2's honest weaknesses vs. screenpipe

- **No audio at all.** No meeting transcription, no "what did I say". screenpipe owns this.
- **No continuous screen video** — only periodic/event screenshots, so the "scrub my day like a movie" experience isn't there.
- **Younger & rougher** — fewer contributors, less polish, smaller ecosystem; no plugin store.
- **LLM dependency for the headline feature** — the cognitive extraction needs an API key and incurs per-session cost (capture works without it; analysis doesn't).
- **Browser-structured capture needs the extension installed**; screenpipe gets browser content "for free" via screen OCR (lower fidelity, but zero setup).

## screenpipe's honest weaknesses vs. capman2

- **It doesn't understand your workflow** — it's a searchable archive; turning "what happened" into "the methodology I used / a replayable playbook / a knowledge graph" is not something it does (you'd build it as a pipe, and even then you'd be reimplementing capman2's analysis layer).
- **No structured terminal / filesystem / browser-interaction capture** — it sees these only as pixels, so you can't query "every `kubectl` command I ran while debugging this and the diff I applied after".
- **No active context-retrieval API for agents** — there's no `context/suggest` equivalent.
- **Heavy on disk** — video + audio is GB/day; capman2 is MB/day.
- **No process attribution / no "only what *I* did" filtering** — it records everything that appears on screen regardless of who/what caused it.

---

*Bottom line:* if the goal is to **clone how a domain expert thinks and
troubleshoots** — and to feed that to future-you or a coding agent — that's
exactly what capman2 is built for and screenpipe isn't. If the goal is a **24/7
local recording of your screen and meetings you can search and build on**,
screenpipe is the more mature, more complete tool. Different jobs.
