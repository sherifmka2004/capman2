# Competitive Analysis & Differentiation Strategy

## The Landscape

| Product | Type | Captures | Extracts Workflow? | Open Source | Self-hosted | Cross-platform |
|---------|------|----------|---------------------|-------------|-------------|----------------|
| **Rewind.ai** | Commercial | Screen, audio, text | ❌ Q&A only | ❌ | ❌ Cloud | macOS/Win |
| **Microsoft Recall** | OS-bundled | Screenshots only | ❌ | ❌ | ✅ Local | Win11+ Copilot+ PCs |
| **Apple Intelligence** | OS-bundled | Apps only | ❌ | ❌ | ✅ Local | macOS 15+ |
| **screenpipe** | OSS | Screen, audio, OCR | ❌ Search only | ✅ | ✅ | ✅ |
| **Khoj** | OSS | Documents, notes | ❌ RAG only | ✅ | ✅ | ✅ |
| **Memex (Worldbrain)** | OSS extension | Browsing only | ❌ | ✅ | ✅ | Browser only |
| **Obsidian + plugins** | Free + plugins | Manual notes | ❌ Manual | Partial | ✅ | ✅ |
| **mem.ai** | Commercial | Manual notes | ❌ | ❌ | ❌ | Web/mobile |
| **DEVONthink** | Commercial | Saved docs | ❌ | ❌ | ✅ | macOS only |
| **Cursor / Continue** | Commercial / OSS | Code only | ❌ Code suggest | Partial | Partial | IDE only |
| **capman2** | OSS | Everything + cognitive | **✅ 3-pass CoT** | ✅ | ✅ | ✅ |

## Where Everyone Else Falls Short

**Nobody else extracts the cognitive workflow.** They all stop at "what happened" — capman2 is the only system that asks "*how* did you think through this, and *what was the methodology*?"

But on closer audit, even capman2's current cognitive layer has gaps. Here's what's missing to truly clone an expert:

### Gap 1: Missing-Knowledge Detection (HIGH PRIORITY)
Currently the `knowledge_gaps_revealed` field exists in the CoT output, but it's never **aggregated across sessions**. An expert isn't just defined by what they know — they're defined by *what they didn't have to look up*. We need a persistent "knowledge gap profile" that tracks recurring lookups (sign of unmastered concept) vs. one-off (genuine unknown).

### Gap 2: Troubleshooting Playbooks (HIGH PRIORITY)
The current Pass 2 produces a generic chain-of-thought. For debugging/troubleshooting sessions specifically, we need a structured **playbook**: symptom → diagnostic step → expected result → fix → verification. This is what makes methodology *replicable* — not prose.

### Gap 3: No Active Context Retrieval (HIGH PRIORITY)
The captured knowledge sits passive. There's no API for an IDE, coding agent, or chatbot to ask "*I'm about to debug X — what's my history with similar problems?*" and get back the right playbook + relevant past sessions.

### Gap 4: No Error / Stack Trace Awareness (MEDIUM)
Shell output is captured raw, but we don't detect errors. A Python traceback, a 500 response, an `npm ERR!` — these are session triggers and need to be tagged so they show up in playbooks.

### Gap 5: No Code Change Capture (MEDIUM)
File save events exist, but no diff is captured. Without before/after, we lose the ability to say "*you fixed it by changing line 42 from X to Y*."

### Gap 6: No AI-Tool Conversation Capture (MEDIUM)
When the user asks ChatGPT or Claude a question, that's part of their cognitive workflow. We capture the URL but not the conversation. Need a content-script enhancement for chat.openai.com / claude.ai / cursor / etc.

### Gap 7: No Tool-Specific Context (LOW)
A URL like `https://github.com/foo/bar/issues/123` is just a URL to us. It should be enriched with "GitHub issue #123 in foo/bar" and tied to other GitHub activity.

## Implementation Plan (this PR)

Implementing Tier 1 — the features that lock in differentiation:

1. **`capman/knowledge/gaps.py`** — Knowledge Gap Tracker
   - SQLite table `knowledge_gaps` (concept, lookup_count, sessions[], first/last seen)
   - Updated on every Pass 2 — clusters knowledge_gaps_revealed across sessions
   - Endpoint: `GET /knowledge/gaps?top=20` returns user's top recurring lookups

2. **4th LLM pass — Troubleshooting Playbook** (`capman/pipeline/prompts.py`, `analyzer.py`)
   - Runs after Pass 2 if `problem_type ∈ {debugging, troubleshooting}`
   - Output: structured playbook (symptoms, diagnostic_steps, fix, verification)
   - Saved as markdown at `~/.capman/playbooks/{slug}.md`
   - Indexed in ChromaDB for semantic retrieval

3. **`capman/pipeline/error_detect.py`** — Stack trace / error detector
   - Runs in enricher on every `shell_output` event
   - Pattern matches Python tracebacks, JS stack traces, Go panics, HTTP errors
   - Tags events with `error_type` + `error_signature`

4. **`capman/sensors/code_diff.py`** — Code change capture
   - Hooks into existing `filesystem` sensor's save events
   - Computes unified diff before/after for code files
   - Emits `CODE_DIFF` event

5. **`capman/api/routes/context.py`** — Active context retrieval
   - `POST /context/suggest` — input: free-text task description
   - Returns: relevant playbooks + knowledge nodes + similar past sessions
   - Designed to be injected into IDE/coding-agent system prompts

This turns capman2 from a passive knowledge store into an **actionable methodology engine** — the only thing on the market that can not just remember what you did but tell future-you (or future-AI) *how* to do it again.
