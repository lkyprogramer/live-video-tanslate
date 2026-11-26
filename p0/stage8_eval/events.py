"""Stage 8 event validation and cross-domain clock anchor helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from subtitle_service.quality_types import AudioSpan

from .contracts import resolve_schema_path

VALID_CLOCK_DOMAINS = frozenset({"server_monotonic", "browser_performance"})


def validate_event(e: dict[str, Any], *, schema_path: Path | None = None) -> list[str]:
    """Validate a Stage8Event dictionary against schema and causal AudioSpan/clock rules."""
    errors: list[str] = []
    if not isinstance(e, dict):
        return ["event must be a JSON object"]

    resolved = resolve_schema_path(schema_path)
    if resolved is not None:
        root = json.loads(resolved.read_text(encoding="utf-8"))
        ev_schema = {
            "$schema": root.get("$schema"),
            "$defs": root.get("$defs", {}),
            "$ref": "#/$defs/Event",
        }
        validator = Draft202012Validator(ev_schema)
        for err in sorted(validator.iter_errors(e), key=lambda x: list(x.absolute_path)):
            loc = ".".join(str(p) for p in err.absolute_path) or "<root>"
            errors.append(f"schema:{loc}: {err.message}")

    domain = e.get("clock_domain")
    if domain not in VALID_CLOCK_DOMAINS:
        errors.append(f"invalid clock_domain: {domain!r}")

    span = e.get("span")
    if isinstance(span, dict):
        key = e.get("key") or {}
        try:
            AudioSpan(
                session_id=str(key.get("session_id") or "event"),
                epoch=int(key.get("epoch", 0) or 0),
                target_start=int(span["target_start"]),
                target_end=int(span["target_end"]),
                input_start=int(span["input_start"]),
                input_end=int(span["input_end"]),
                captured_end=int(span["captured_end"]),
                sample_rate=int(span["sample_rate"]),
            )
        except Exception as exc:
            errors.append(f"span:{exc}")

    return errors


def estimate_clock_anchor(b0_ns: int, s1_ns: int, s2_ns: int, b3_ns: int) -> dict[str, Any]:
    """Estimate server_monotonic - browser_performance offset and worst-case RTT uncertainty."""
    rtt_ns = max(0, (int(b3_ns) - int(b0_ns)) - (int(s2_ns) - int(s1_ns)))
    offset_ns = ((int(s1_ns) - int(b0_ns)) + (int(s2_ns) - int(b3_ns))) // 2
    server_dwell_ns = max(0, int(s2_ns) - int(s1_ns))
    uncertainty_ms = round((rtt_ns / 2.0 + server_dwell_ns * 0.5) / 1_000_000.0, 4)
    return {
        "offset_ns": offset_ns,
        "rtt_ns": rtt_ns,
        "uncertainty_ms": uncertainty_ms,
        "valid": uncertainty_ms <= 20.0,
    }


def cross_domain_delta_ms(
    start_event: dict[str, Any],
    end_event: dict[str, Any],
    *,
    anchor: dict[str, Any] | None = None,
    max_uncertainty_ms: float = 20.0,
) -> float:
    """Compute signed delta (end - start) in ms, refusing unanchored cross-domain subtraction."""
    d_start = start_event.get("clock_domain")
    d_end = end_event.get("clock_domain")
    if d_start not in VALID_CLOCK_DOMAINS or d_end not in VALID_CLOCK_DOMAINS:
        raise ValueError(f"unsupported clock_domain pair: {d_start!r}, {d_end!r}")

    t_start = int(start_event["ts_ns"])
    t_end = int(end_event["ts_ns"])
    if d_start == d_end:
        return (t_end - t_start) / 1_000_000.0

    if anchor is None or "offset_ns" not in anchor:
        raise ValueError("cross-domain clock subtraction requires valid anchor")
    unc = float(anchor.get("uncertainty_ms", float("inf")))
    if unc > max_uncertainty_ms:
        raise ValueError(
            f"clock anchor uncertainty {unc}ms exceeds max_uncertainty_ms={max_uncertainty_ms}ms"
        )

    offset_ns = int(anchor["offset_ns"])
    s_ns = t_start if d_start == "server_monotonic" else (t_start + offset_ns)
    e_ns = t_end if d_end == "server_monotonic" else (t_end + offset_ns)
    return (e_ns - s_ns) / 1_000_000.0
