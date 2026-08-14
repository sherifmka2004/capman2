"""
Extractor — parses LLM JSON output into Triple and ChainOfThought objects.
Thin layer over the analyzer; lives here for clear separation of concerns.
"""
from capman.pipeline.analyzer import SessionAnalyzer  # re-export for convenience
from capman.events import Triple, ChainOfThought  # noqa: F401
