"""
Document content capture — reads "what the user actually saw" inside slides,
pages, sheets, and notes, gated by an attention policy so quick scroll-throughs
are silently dropped.

Public API:
  - DocumentView, ExtractedContent  (immutable domain models)
  - AttentionPolicy, DwellAttentionPolicy
  - ContentExtractor, AppModelExtractor, OcrScreenExtractor, ContentExtractionChain
  - DocumentContentTracker
  - build_content_tracker(config, emit, adapter)  ← the factory used by sensors
"""
from capman.document.attention import AttentionPolicy, DwellAttentionPolicy
from capman.document.extractors import (
    AppModelExtractor,
    ContentExtractionChain,
    ContentExtractor,
    OcrScreenExtractor,
    build_extraction_chain,
)
from capman.document.model import DocumentView, ExtractedContent
from capman.document.tracker import DocumentContentTracker, build_content_tracker

__all__ = [
    "AppModelExtractor",
    "AttentionPolicy",
    "ContentExtractionChain",
    "ContentExtractor",
    "DocumentContentTracker",
    "DocumentView",
    "DwellAttentionPolicy",
    "ExtractedContent",
    "OcrScreenExtractor",
    "build_content_tracker",
    "build_extraction_chain",
]
