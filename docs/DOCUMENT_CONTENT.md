# Document content capture

capman2's document sensor already captures *navigation* — which slide / page /
sheet / note the user moved to, when, and for how long. The **content capture**
layer goes one step deeper: it stores the *text the user actually saw* on each
slide / page / sheet / note, embeds it in the vector store, and makes it
searchable from the chatbot.

This is opt-in (defaulted *on*) and gated by an **attention policy** so quick
scroll-throughs never get captured. The user-visible promise is:

> If you actually paused to read it, capman has it. If you skimmed past it,
> capman ignored it.

---

## What gets captured

| App family             | Item kind | What's stored as `text` (best-effort)                          |
|------------------------|-----------|----------------------------------------------------------------|
| PowerPoint / Keynote / Impress | `slide`   | Slide title + body text (or OCR of the slide)         |
| Word / Pages / Writer  | `page`    | Visible page text (or OCR)                                     |
| PDF viewers            | `page`    | Visible page text (or OCR)                                     |
| Excel / Numbers / Calc | `sheet`   | Visible cell range (or OCR)                                    |
| Apple Notes / Obsidian / OneNote / Notion | `note` | Note body                                |

For each captured unit you get a `DOC_CONTENT` event:

```json
{
  "type": "doc_content",
  "payload": {
    "doc_type": "presentation",
    "doc_name": "Q4 strategy.pptx",
    "doc_path": "/Users/me/Documents/Q4 strategy.pptx",
    "app": "Microsoft PowerPoint",
    "item_kind": "slide",
    "item_index": 12,
    "item_label": "Pricing model — proposed",
    "text": "…300-char excerpt for browsing…",
    "text_chars": 300,
    "full_chars_indexed": 1842,
    "dwell_s": 7.4,
    "revisit_count": 1,
    "source": "app_model",
    "content_hash": "ab12cd34ef5678ab"
  }
}
```

The full text is embedded into ChromaDB (`type=doc`, chunked with overlap) so
the chatbot can pull a paragraph-sized slice when relevant.
SQLite keeps just the 300-char excerpt + `full_chars_indexed` to stay slim.

---

## Attention policy — "looked at" vs "scrolled past"

The discriminator is **deferred capture**:

1. The document sensor fires `note_navigation(state, …)` every time the user
   lands on a new unit.
2. The tracker schedules the (potentially expensive) extraction
   `content_min_attention_s` seconds in the future, then *cancels* it the
   moment the user moves on.
3. So a quick scroll-through extracts nothing — the timer never fires.
4. Re-visits also count: if the user comes back to a slide
   `content_revisit_threshold` times even briefly (≥ 1.5 s), it's captured.

The policy lives in `capman/document/attention.py` as a small ABC
(`AttentionPolicy`); a smarter signal (eye-tracking, mouse activity, zoom
level, …) can drop in without touching the tracker.

---

## Extraction strategy — Strategy + Chain-of-Responsibility

Content extraction is a chain of pluggable extractors
(`capman/document/extractors.py`):

| Extractor          | How                                                           | When it shines                            |
|--------------------|---------------------------------------------------------------|-------------------------------------------|
| `AppModelExtractor`| Calls `adapter.get_document_visible_text(app, title)`         | High fidelity; per-OS / per-app          |
| `OcrScreenExtractor`| Screenshot via `mss`, OCR via the existing `OCREngine`       | Universal fallback                        |

The chain tries each `available()` extractor in order
(`content_extractor_order`) and returns the first non-empty result. The
`AppModelExtractor` is a no-op until a platform adapter implements
`get_document_visible_text` (PlatformAdapter exposes the hook with a default
of `None`).

---

## Configuration

```toml
[sensors.documents]
capture_content              = true
content_min_attention_s      = 4.0
content_revisit_threshold    = 2
content_max_items_per_doc    = 80
content_max_chars            = 8000
content_use_app_model        = true
content_use_ocr_fallback     = true
content_extractor_order      = ["app_model", "ocr"]
```

Disable content capture entirely:

```toml
capture_content = false
```

Make the attention bar stricter (requires 8 s dwell):

```toml
content_min_attention_s = 8.0
```

OCR-only (skip the app-model hook):

```toml
content_extractor_order = ["ocr"]
```

---

## How the chatbot uses it

`/chat/message` builds a context section called *"Relevant Document Content
(slides / pages / sheets the user actually read)"* by issuing a
`vs.search(question, types=["doc"])` against the vector store. So you can ask:

- "What did I read on slide 12 of the Q4 strategy deck?"
- "Find the pages where pricing was discussed."
- "Summarize what I looked at in that PDF yesterday."

…and the LLM gets paragraph-sized excerpts of the actual text rather than just
"slide 12 of Q4 strategy.pptx".

---

## Storage cost

Per dwelled-on unit: ~0.5–4 KiB in SQLite (slim payload + 300-char excerpt) +
~3–10 KiB in ChromaDB (chunked text + 384-d embedding). The
`content_max_items_per_doc` cap (default 80) keeps a 500-page PDF the user
spent 20 minutes in from filling the index.

---

## Threading & non-blocking guarantees

Everything that can block (screenshot, OCR, AppleScript, AT-SPI calls) runs
inside `loop.run_in_executor(None, …)` from the tracker's
`_deferred_capture` coroutine, so the document sensor's poll loop and the
event pipeline are never stalled by a slow extractor.

---

## Tests

`tests/unit/test_document_content.py` covers:
- `DwellAttentionPolicy` thresholds + revisit logic
- `DocumentView.from_doc_state` mapping for slides / pages / sheets / notes
- `ContentExtractionChain` returning the first non-empty extractor
- `DocumentContentTracker`:
  - quick scroll-through → no emit
  - dwell ≥ threshold → exactly one emit with the right payload
  - dedup by content hash
  - per-doc cap honored
