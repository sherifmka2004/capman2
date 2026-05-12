# Competitive Analysis & Differentiation Strategy

## The Landscape

| Product | Type | Captures | Extracts Workflow? | Open Source | Self-hosted | Cross-platform |
|---------|------|----------|---------------------|-------------|-------------|----------------|
| **Rewind.ai** | Commercial | Screen, audio, text | ❌ Q&A only | ❌ | ❌ Cloud | macOS/Win |
| **Microsoft Recall** | OS-bundled | Screenshots only | ❌ | ❌ | ✅ Local | Win11+ Copilot+ PCs |
| **Apple Intelligence** | OS-bundled | Apps only | ❌ | ❌ | ✅ Local | macOS 15+ |
| **screenpipe** | OSS | 24/7 screen video + audio, OCR/STT | ❌ Search + BYO pipes | ✅ | ✅ | ✅ |
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

## Status — Tier 1 shipped

The differentiating features above are now implemented:

1. ✅ **Knowledge Gap Tracker** — `capman/knowledge/gaps.py`, `knowledge_gaps` table, `GET /knowledge/gaps`, surfaced in the chat context and the Knowledge Gaps tab.
2. ✅ **4th LLM pass — Troubleshooting Playbook** — `PASS4_TROUBLESHOOTING_PLAYBOOK` in `prompts.py`, runs for debugging/troubleshooting/review sessions; `playbooks` table + Obsidian markdown + ChromaDB index; `GET /knowledge/playbooks`, Playbooks tab.
3. ⏳ **Error / stack-trace detector** — still roadmap (raw `shell_output` is captured; tagging not yet wired).
4. ✅ **Code-change capture** — built into the rewritten `filesystem` sensor: unified diffs (snapshot-based or `git diff`) → `CODE_DIFF` events, rendered in the LLM narrative and the Sessions tab. See [FILE_MONITORING.md](FILE_MONITORING.md).
5. ✅ **Active context retrieval** — `POST /context/suggest` (`capman/api/routes/context.py`) → playbooks + similar sessions + related concepts + page excerpts + knowledge gaps.

Plus, beyond the original Tier-1 list: real-time **shell hook**, **document-navigation** sensor, **in-page interaction** capture, **direct-user-action file attribution**, the privileged **`capman-fsmon`** deep file monitor (Linux fanotify/auditd/eBPF + macOS Endpoint Security), and a **storage calculator** (`/storage`, `capman storage`, Storage tab).

This turns capman2 from a passive knowledge store into an **actionable methodology engine** — not just remembering what you did, but telling future-you (or future-AI) *how* to do it again.

---

## Deep dive: capman2 vs. screenpipe

The closest open-source comparison. screenpipe is a 24/7 screen-video + audio
recorder (OCR/STT → searchable archive + "pipes" plugin platform); capman2 is a
structured-event capturer + LLM cognitive-workflow extractor. They overlap almost
not at all and are arguably complementary. Full breakdown:
**[CAPMAN_VS_SCREENPIPE.md](CAPMAN_VS_SCREENPIPE.md)**.
