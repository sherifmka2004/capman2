---
target: capman/api/chat_ui.py
total_score: 15
p0_count: 2
p1_count: 2
timestamp: 2026-09-01T21-59-34Z
slug: capman-api-chat-ui-py
---
## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|---|---:|---|
| 1 | Visibility of system status | 2/4 | A connection badge exists, but capture, analysis, scope, and freshness are not visible. |
| 2 | Match between system and real world | 2/4 | Technical terms are appropriate for power users, but the primary task is not expressed in user language. |
| 3 | User control and freedom | 2/4 | Tabs provide navigation, but users cannot choose a private/team scope or control what context is used. |
| 4 | Consistency and standards | 2/4 | The base component vocabulary is consistent, but tabs, cards, modal, and inline controls form an uneven system. |
| 5 | Error prevention | 1/4 | The current UI has no visible boundary between raw personal capture and publishable team knowledge. |
| 6 | Recognition rather than recall | 2/4 | Eight equal top-level choices require users to remember which tool applies. |
| 7 | Flexibility and efficiency | 1/4 | No visible command palette, shortcuts, pinning, or compact sidebar mode for power users. |
| 8 | Aesthetic and minimalist design | 1/4 | The dense horizontal tab bar, generic cards, and equal visual weight obscure the main task. |
| 9 | Error recognition and recovery | 1/4 | Loading/error states are basic text; missing data and failed actions have no useful recovery path. |
| 10 | Help and documentation | 1/4 | The greeting is an opaque capability dump instead of situational guidance. |
| **Total** | | **15/40** | **Foundational IA redesign required** |

## Anti-Patterns Verdict

The current interface does not look overdesigned; it looks like an early developer tool assembled from familiar dark-dashboard defaults. The main issue is not visual polish but undifferentiated information architecture: Chat, Playbooks, Knowledge Gaps, Sessions, Storage, Context Suggest, Brain Map, and Settings all compete as peers.

The deterministic scan found one `side-tab` warning at `capman/api/chat_ui.py:278`: the Context Suggest card uses a 3px blue left border, a recognizable generic-dashboard accent. It should become a standard evidence/result row with hierarchy through typography and spacing.

## Overall Impression

Capman has a valuable, unusual core: private captured work becoming reusable methodology. The UI currently presents it as a collection of utilities. The redesign should instead make the product feel like an evidence-led personal work library with a clearly separate, deliberately published team library.

## What's Working

- A restrained dark surface is appropriate for prolonged technical investigation.
- The existing chat input gives the product an immediately understandable entry point.
- Sessions, playbooks, gaps, and graph data already map to meaningful product concepts; they need hierarchy, not reinvention.

## Priority Issues

### [P0] No privacy or collaboration scope

**Why it matters:** The product promise is personal-first and selectively collaborative, but the interface has no visible distinction between a person's raw history and a team-safe derived library.

**Fix:** Add a persistent scope switcher: `Personal` and `Team Library`. Personal is default. Team Library contains only published/redacted playbooks, concepts, and evidence summaries. Publishing must open a reviewable preview showing exactly what crosses the boundary.

**Suggested command:** `$impeccable shape privacy-first navigation and team library`

### [P0] Eight equal top-level destinations

**Why it matters:** Users must decide among too many capabilities before Capman has helped them. This violates working-memory limits and weakens the primary action.

**Fix:** Replace horizontal tabs with a collapsible left sidebar: Home, Ask Capman, Sessions, Playbooks, Knowledge. Put Storage and Settings in a lower utility group. Move Context Suggest into session/task detail. Make Brain Map a Knowledge sub-view rather than a primary destination.

**Suggested command:** `$impeccable shape dashboard information architecture`

### [P1] The home experience is a blank chat rather than a work surface

**Why it matters:** A technical user opening Capman needs immediate orientation: what was captured, what is ready to reuse, and what needs attention.

**Fix:** Create a Home view with Recent Work, Proven Methods, Needs Attention, and capture health. Use concise evidence-led rows, not a wall of equally styled metric cards.

**Suggested command:** `$impeccable craft personal work home`

### [P1] Evidence is hidden behind generic cards and modals

**Why it matters:** Capman's differentiator is methodology with provenance. A generic card grid makes source sessions, confidence, and recency feel secondary.

**Fix:** Use a session detail layout inspired by an investigation view: conclusion at top; approach timeline and evidence links below; reusable playbook/publish actions in a stable side rail. Prefer inline detail panels to modal-first flows.

**Suggested command:** `$impeccable craft evidence-led session detail`

### [P2] Accessibility and power-user feedback are incomplete

**Why it matters:** The user base includes keyboard-first technical users and non-technical professionals. Dense navigation needs visible focus, shortcuts, clear empty states, and calm motion.

**Fix:** Build semantic nav/buttons, a command palette, clear focus states, WCAG AA tokens, skeleton/loading and empty states, and reduced-motion behavior.

**Suggested command:** `$impeccable harden dashboard interaction states`

## Persona Red Flags

**Alex, technical power user:** No keyboard-oriented navigation or command entry is visible. They must use an eight-item top tab strip to move between investigation tools, then infer where Context Suggest or Brain Map belongs.

**Maya, non-technical professional:** The opening greeting lists raw technical capabilities instead of answering "what should I do here?" Terms such as Brain Map and Context Suggest do not explain their outcome.

**Team reader:** There is no Team Library, publish review, or scope indicator. They cannot know whether a playbook is safe, approved, current, or personal.

## Minor Observations

- Replace emoji navigation icons with one consistent icon set and text labels.
- Remove decorative card-hover lift where it does not signal elevation or navigation.
- Do not use a graph animation as ambient decoration; graph exploration should be task-led and filterable.
- Replace the contextual result card's blue side stripe with standard list hierarchy.

## Phase 1 implementation note (2026-09-02)

The first approved slice is now implemented in `capman/api/chat_ui.py`:

- The eight-item horizontal strip is replaced by a semantic Personal-first sidebar.
- Personal scope is explicit and private by default; Team Library is disabled with a reason until roles, tenancy, redaction, and publish review exist.
- Home is the default route and renders live recent sessions, proven playbooks, and knowledge-gap attention state.
- Navigation uses native buttons, visible keyboard focus, a skip link, Ctrl/Cmd+K to focus Ask Capman, and reduced-motion behavior.
- Mobile adapts the sidebar to a horizontally scrollable navigation rail and preserves the same information architecture.
- The Brain Map hidden-state specificity issue found during mobile review was fixed with `#view-brain.hidden { display: none; }`.

The remaining P1 evidence-detail work and the backend Team Library controls remain intentionally open in the backlog.

## Questions to Consider

- Should a user open Capman to ask a question, resume a session, or review what became reusable yesterday?
- What confidence/evidence threshold must an artifact meet before the Publish to Team Library action appears?
- Can non-technical users see the same evidence, translated into plain-language labels, without weakening the technical view?
