"""Generic web-behavior capability for capman2.

Product-agnostic by design: a product supplies a TOML *manifest* (data);
capman2 supplies collection, segmentation, clustering, analysis and
verification (code). Nothing in this package may mention a specific product.

The privacy boundary lives here: a manifest can only declare prop types from a
closed vocabulary that contains **no free-text type**, so free text is
physically unable to enter the store — and can never reach an LLM.

See docs/WEB_BEHAVIOR.md for the full contract.
"""
from __future__ import annotations

from capman.web.manifest import (
    EventSpec,
    ManifestError,
    ProductManifest,
    PropSpec,
    eval_success_when,
    load_manifest,
    load_manifests,
    tenant_id,
    validate_event,
)
from capman.web.friction import FRICTION_EVENTS, derive_friction

__all__ = [
    "EventSpec",
    "ManifestError",
    "ProductManifest",
    "PropSpec",
    "eval_success_when",
    "load_manifest",
    "load_manifests",
    "tenant_id",
    "validate_event",
    "FRICTION_EVENTS",
    "derive_friction",
]
