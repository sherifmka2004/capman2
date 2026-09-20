"""Product manifest — the single source of truth for what a product may send.

One TOML file per product declares its identity, retention windows, session
thresholds, funnel steps, success criteria and every event with its props.
The collector accepts **only** what a manifest declares, and a manifest may
only declare prop types from a closed vocabulary:

    enum · int · bool · bucket · short_string

There is deliberately **no free-text type**. That omission is the privacy
boundary: free text cannot be declared, cannot be stored, and therefore can
never reach the LLM analysis passes.

Manifest shape (TOML)::

    [product]
    key         = "acme"                 # tenant becomes user_id = "product:acme"
    description = "One-line domain context injected into LLM prompts."
    glossary    = { widget = "the main interactive surface" }

    [retention]
    raw_events_days = 45
    episodes_days   = 180

    [session]
    idle_threshold_s = 30
    cool_period_s    = 45
    hard_break_s     = 1800
    min_events       = 3

    [steps]
    order = ["land", "start", "edit", "done"]

    [outcomes]
    success_when = ["export_result.ok == true"]

    [[events]]
    name  = "widget_click"
    step  = "edit"
    props = { target = { type = "enum", values = ["save", "cancel"] },
              n = { type = "int", max = 20 } }
"""
from __future__ import annotations

import json
import logging
import re
import tomllib
from pathlib import Path
from typing import Any, Iterable

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)

#: The closed prop-type vocabulary. No free-text type exists — by design.
PROP_TYPES: frozenset[str] = frozenset({"enum", "int", "bool", "bucket", "short_string"})

#: Product keys double as tenant ids and (sanitized) Postgres role names.
KEY_RE = re.compile(r"^[a-z0-9_]+$")

#: Event and prop names share the same conservative alphabet.
NAME_RE = re.compile(r"^[a-z0-9_]+$")

#: Hard cap for any short_string prop value — the privacy boundary's second lock.
MAX_SHORT_STRING = 64

#: Applied when a short_string prop declares no explicit pattern.
DEFAULT_SHORT_STRING_PATTERN = r"^[a-z0-9_.\-/]+$"

_SUCCESS_EXPR_RE = re.compile(r"^([a-z0-9_]+)\.([a-z0-9_]+)\s*==\s*(.+)$")

_KNOWN_SECTIONS = frozenset({"product", "retention", "session", "steps", "outcomes", "events"})
_PRODUCT_KEYS = frozenset({"key", "description", "glossary"})
_RETENTION_KEYS = frozenset({"raw_events_days", "episodes_days"})
_SESSION_KEYS = frozenset({"idle_threshold_s", "cool_period_s", "hard_break_s", "min_events"})
_STEPS_KEYS = frozenset({"order"})
_OUTCOME_KEYS = frozenset({"success_when"})
_EVENT_KEYS = frozenset({"name", "step", "props"})


class ManifestError(ValueError):
    """A manifest or an incoming event violates the declared schema."""


def _fmt_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ())) or "<root>"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


class PropSpec(BaseModel):
    """One declared prop of one declared event."""

    model_config = ConfigDict(extra="forbid")

    type: str
    values: list[str] | None = None       # enum: the closed set of members
    max: int | None = None                # int: inclusive upper bound
    buckets: list[float] | None = None    # bucket: ascending boundaries
    pattern: str | None = None            # short_string: allowlist regex
    optional: bool = True

    @model_validator(mode="after")
    def _check_by_type(self) -> "PropSpec":
        t = self.type
        if t not in PROP_TYPES:
            raise ValueError(
                f"unknown prop type '{t}' (allowed: {sorted(PROP_TYPES)}); "
                "free-text props are forbidden by design"
            )
        if t == "enum" and not self.values:
            raise ValueError("enum prop requires non-empty 'values'")
        if t == "bucket":
            if not self.buckets:
                raise ValueError("bucket prop requires non-empty 'buckets'")
            if list(self.buckets) != sorted(self.buckets):
                raise ValueError("bucket boundaries must be ascending")
        if t == "short_string" and self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"invalid short_string pattern: {exc}") from exc
        return self


class EventSpec(BaseModel):
    """One declared event: a closed name, an optional funnel step, closed props."""

    model_config = ConfigDict(extra="forbid")

    name: str
    step: str = ""
    props: dict[str, PropSpec] = {}

    @field_validator("name", "step")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if v and not NAME_RE.match(v):
            raise ValueError(f"'{v}' must match {NAME_RE.pattern}")
        return v


class ProductManifest(BaseModel):
    """Everything product-specific the pipeline knows, declared as data."""

    model_config = ConfigDict(extra="forbid")

    key: str
    description: str = ""
    glossary: dict[str, str] = {}
    # retention
    raw_events_days: int = 45
    episodes_days: int = 180
    # session detection overrides (web cadence differs from desktop)
    idle_threshold_s: float = 30.0
    cool_period_s: float = 45.0
    hard_break_s: float = 1800.0
    min_events: int = 3
    # funnel
    steps: list[str] = []
    success_when: list[str] = []
    events: dict[str, EventSpec] = {}

    @field_validator("key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        if not KEY_RE.match(v):
            raise ValueError(f"product key '{v}' must match {KEY_RE.pattern}")
        return v

    @field_validator("steps")
    @classmethod
    def _check_steps(cls, v: list[str]) -> list[str]:
        for s in v:
            if not NAME_RE.match(s):
                raise ValueError(f"step '{s}' must match {NAME_RE.pattern}")
        if len(set(v)) != len(v):
            raise ValueError("steps.order contains duplicates")
        return v

    @model_validator(mode="after")
    def _check_events(self) -> "ProductManifest":
        for name, spec in self.events.items():
            if spec.name != name:
                raise ValueError(f"event key '{name}' does not match its name '{spec.name}'")
            if spec.step and spec.step not in self.steps:
                raise ValueError(
                    f"event '{name}' declares step '{spec.step}' which is not in steps.order"
                )
        return self


def bucket_label(value: float, buckets: list[float]) -> str:
    """Map a numeric value onto its bucket label. Deterministic, lossy by design.

    buckets=[1, 5, 10] → 0.5 ⇒ "<1", 1 ⇒ "1-5", 7 ⇒ "5-10", 42 ⇒ ">=10".
    """
    if value < buckets[0]:
        return f"<{_num(buckets[0])}"
    for lo, hi in zip(buckets, buckets[1:]):
        if lo <= value < hi:
            return f"{_num(lo)}-{_num(hi)}"
    return f">={_num(buckets[-1])}"


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def load_manifest(path: str | Path) -> ProductManifest:
    """Parse and validate one product manifest TOML file."""
    p = Path(path)
    try:
        with open(p, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ManifestError(f"manifest not found: {p}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"invalid TOML in {p}: {exc}") from None

    if not isinstance(raw, dict):
        raise ManifestError(f"{p}: manifest must be a TOML table")

    unknown = set(raw) - _KNOWN_SECTIONS
    if unknown:
        raise ManifestError(f"{p}: unknown section(s): {sorted(unknown)}")

    product = _section(raw, "product", _PRODUCT_KEYS, p)
    retention = _section(raw, "retention", _RETENTION_KEYS, p)
    session = _section(raw, "session", _SESSION_KEYS, p)
    steps_sec = _section(raw, "steps", _STEPS_KEYS, p)
    outcomes = _section(raw, "outcomes", _OUTCOME_KEYS, p)

    if "key" not in product:
        raise ManifestError(f"{p}: [product] requires 'key'")

    events = _parse_events(raw.get("events") or [], p)

    try:
        manifest = ProductManifest(
            key=product["key"],
            description=product.get("description", ""),
            glossary=product.get("glossary", {}),
            raw_events_days=retention.get("raw_events_days", 45),
            episodes_days=retention.get("episodes_days", 180),
            idle_threshold_s=session.get("idle_threshold_s", 30.0),
            cool_period_s=session.get("cool_period_s", 45.0),
            hard_break_s=session.get("hard_break_s", 1800.0),
            min_events=session.get("min_events", 3),
            steps=steps_sec.get("order", []),
            success_when=outcomes.get("success_when", []),
            events=events,
        )
    except ValidationError as exc:
        raise ManifestError(f"{p}: {_fmt_validation_error(exc)}") from None

    # success_when expressions must reference declared events/props.
    for expr in manifest.success_when:
        ev, prop, _want = _parse_success_expr(expr, p)
        spec = manifest.events.get(ev)
        if spec is None:
            raise ManifestError(f"{p}: success_when references undeclared event '{ev}'")
        if prop not in spec.props:
            raise ManifestError(
                f"{p}: success_when references undeclared prop '{ev}.{prop}'"
            )

    logger.debug("Loaded manifest '%s' from %s (%d events)", manifest.key, p, len(events))
    return manifest


def _section(raw: dict, name: str, allowed: frozenset[str], path: Path) -> dict:
    sec = raw.get(name) or {}
    if not isinstance(sec, dict):
        raise ManifestError(f"{path}: [{name}] must be a table")
    unknown = set(sec) - allowed
    if unknown:
        raise ManifestError(f"{path}: [{name}] has unknown key(s): {sorted(unknown)}")
    return sec


def _parse_events(events_raw: Any, path: Path) -> dict[str, EventSpec]:
    if not isinstance(events_raw, list):
        raise ManifestError(f"{path}: [[events]] must be an array of tables")
    events: dict[str, EventSpec] = {}
    for i, ev in enumerate(events_raw):
        if not isinstance(ev, dict):
            raise ManifestError(f"{path}: [[events]] entry {i} must be a table")
        unknown = set(ev) - _EVENT_KEYS
        if unknown:
            raise ManifestError(f"{path}: [[events]] entry {i} has unknown key(s): {sorted(unknown)}")
        if "name" not in ev:
            raise ManifestError(f"{path}: [[events]] entry {i} requires 'name'")
        props_raw = ev.get("props") or {}
        if not isinstance(props_raw, dict):
            raise ManifestError(f"{path}: event '{ev['name']}' props must be a table")
        try:
            spec = EventSpec(name=ev["name"], step=ev.get("step", ""), props=props_raw)
        except ValidationError as exc:
            raise ManifestError(f"{path}: event '{ev['name']}': {_fmt_validation_error(exc)}") from None
        if spec.name in events:
            raise ManifestError(f"{path}: duplicate event '{spec.name}'")
        events[spec.name] = spec
    return events


def load_manifests(paths: Iterable[str | Path]) -> dict[str, ProductManifest]:
    """Load several manifests, keyed by product key. Duplicate keys are fatal."""
    out: dict[str, ProductManifest] = {}
    for path in paths:
        m = load_manifest(path)
        if m.key in out:
            raise ManifestError(f"duplicate product key '{m.key}' (second manifest: {path})")
        out[m.key] = m
    return out


def tenant_id(manifest: ProductManifest) -> str:
    """The RLS tenant key for a product: ``product:<key>``."""
    return f"product:{manifest.key}"


def _parse_success_expr(expr: str, path: Path | None = None) -> tuple[str, str, Any]:
    m = _SUCCESS_EXPR_RE.match(expr.strip())
    if not m:
        where = f"{path}: " if path else ""
        raise ManifestError(
            f"{where}invalid success_when expression {expr!r} "
            "(expected '<event>.<prop> == <json-literal>')"
        )
    event, prop, literal = m.group(1), m.group(2), m.group(3).strip()
    try:
        want = json.loads(literal)
    except json.JSONDecodeError as exc:
        where = f"{path}: " if path else ""
        raise ManifestError(f"{where}success_when literal {literal!r} is not valid JSON: {exc}") from None
    return event, prop, want


def eval_success_when(expr: str, events: list[dict]) -> bool:
    """True when any event in the episode satisfies ``<event>.<prop> == <literal>``.

    Each event dict needs ``name`` and ``props`` keys.
    """
    event, prop, want = _parse_success_expr(expr)
    for e in events:
        if e.get("name") != event:
            continue
        if (e.get("props") or {}).get(prop) == want:
            return True
    return False


def _normalize_prop(event: str, key: str, ps: PropSpec, value: Any) -> Any:
    """Validate one prop value against its spec and return its stored form."""
    if ps.type == "enum":
        if not isinstance(value, str) or value not in (ps.values or []):
            raise ManifestError(
                f"event '{event}': prop '{key}' must be one of {ps.values}, got {value!r}"
            )
        return value

    if ps.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ManifestError(f"event '{event}': prop '{key}' must be an int, got {value!r}")
        if ps.max is not None and value > ps.max:
            raise ManifestError(
                f"event '{event}': prop '{key}' value {value} exceeds declared max {ps.max}"
            )
        return value

    if ps.type == "bool":
        if not isinstance(value, bool):
            raise ManifestError(f"event '{event}': prop '{key}' must be a bool, got {value!r}")
        return value

    if ps.type == "bucket":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ManifestError(f"event '{event}': prop '{key}' must be numeric, got {value!r}")
        return bucket_label(float(value), ps.buckets or [])

    # short_string — the only string type, and the tightest one.
    if not isinstance(value, str):
        raise ManifestError(f"event '{event}': prop '{key}' must be a string, got {value!r}")
    if len(value) > MAX_SHORT_STRING:
        raise ManifestError(
            f"event '{event}': prop '{key}' exceeds {MAX_SHORT_STRING} chars "
            f"(got {len(value)}); free text is not accepted"
        )
    pattern = ps.pattern or DEFAULT_SHORT_STRING_PATTERN
    if not re.match(pattern, value):
        raise ManifestError(
            f"event '{event}': prop '{key}' value {value!r} fails allowlist pattern {pattern!r}"
        )
    return value


def validate_event(manifest: ProductManifest, name: str, props: dict) -> dict:
    """Validate an incoming event against the manifest; return normalized props.

    Accepts manifest-declared events plus the reserved product-independent
    friction events (see capman.web.friction). Rejects unknown names, unknown
    prop keys, oversized strings, enum/pattern violations and out-of-range
    ints. Raises ManifestError with a precise message on any violation.
    """
    from capman.web.friction import FRICTION_EVENTS  # deferred: friction imports this module

    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ManifestError(f"invalid event name {name!r}")
    if not isinstance(props, dict):
        raise ManifestError(f"event '{name}': props must be an object")

    if name in manifest.events:
        spec_props: dict[str, PropSpec] = manifest.events[name].props
    elif name in FRICTION_EVENTS:
        spec_props = FRICTION_EVENTS[name]
    else:
        raise ManifestError(f"unknown event '{name}' for product '{manifest.key}'")

    for key in props:
        if key not in spec_props:
            raise ManifestError(f"event '{name}': unknown prop '{key}'")

    out: dict[str, Any] = {}
    for key, ps in spec_props.items():
        if key not in props:
            if not ps.optional:
                raise ManifestError(f"event '{name}': missing required prop '{key}'")
            continue
        out[key] = _normalize_prop(name, key, ps, props[key])
    return out
