#!/usr/bin/env python3
"""Conservative evidence arithmetic. This program never approves a model release.

It validates supplied labels; it does not establish that their semantic judgments
or timestamps are true. No network access, model execution, or repository writes.
"""
import argparse
import json
import math
import re
from pathlib import Path
import sys


CONTRACT_EVENTS = frozenset(("pcm_accepted", "caption_emit", "cancel", "finished"))
# Legal names from docs/07-telemetry.md. Their payloads are not contract-audited here.
TELEMETRY_EVENTS = frozenset((
    "capture", "pcm_consumed", "vad_release", "asr_enqueue", "asr_start", "asr_end",
    "source_commit", "mt_enqueue", "mt_start", "mt_first_token", "mt_end",
    "ast_preprocess", "ast_encoder", "ast_prefill", "ast_decode", "caption_filtered",
    "ws_receive", "dom_visible", "dom_hidden", "dom_removed", "drain", "worker_return",
    "resource_sample",
))
EVENT_SCOPE = ("partial contract checks only; permitted telemetry payloads are ignored; "
               "no full v1, clock calibration, semantic or release proof")


def unique_ids(value, allowed=None):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
        raise ValueError("unit IDs must be a list of nonempty strings")
    if len(set(value)) != len(value):
        raise ValueError("duplicate unit ID")
    result = set(value)
    if allowed is not None and not result <= allowed:
        raise ValueError("unit ID outside the frozen manifest")
    return result


def evaluate_offline(report):
    if not isinstance(report, dict):
        raise ValueError("score report must be an object")
    if report.get("schema_version") != 1:
        raise ValueError("unsupported schema_version")
    kind = report.get("evidence_kind")
    if kind not in ("measured", "synthetic"):
        raise ValueError("evidence_kind must be measured or synthetic")
    route = report.get("registered_route")
    if route not in ("Q", "R"):
        raise ValueError("registered_route must explicitly be Q or R")
    reference = report.get("reference_status")
    if reference not in ("historical", "ai_reviewed", "human_verified"):
        raise ValueError("unknown reference_status")
    if kind == "measured":
        registration = report.get("registration_ref")
        if (not isinstance(registration, dict) or not registration.get("experiment_id")
                or not re.fullmatch(r"[a-f0-9]{64}", str(registration.get("registry_sha256", "")))):
            raise ValueError("measured scores require registration_ref and registry_sha256")
    units = unique_ids(report.get("unit_ids"))
    if len(units) != 34:
        raise ValueError("this screening helper is defined for exactly 34 development units")
    rounds = report.get("rounds")
    if not isinstance(rounds, list) or len(rounds) != 2:
        raise ValueError("exactly two paired rounds are required")
    if any(not isinstance(row, dict) for row in rounds):
        raise ValueError("rounds must contain objects")
    round_ids = unique_ids([r.get("round_id") for r in rounds])
    if round_ids != {"seed11", "seed29"}:
        raise ValueError("paired rounds must be seed11 and seed29")
    protected_units = unique_ids(report.get("protected_unit_ids"), units)
    gains, counts, reasons = [], [], []
    uncertain = False
    for row in rounds:
        baseline = unique_ids(row.get("baseline_usable"), units)
        candidate = unique_ids(row.get("candidate_usable"), units)
        protected = unique_ids(row.get("protected_losses"), protected_units)
        protected |= (baseline & protected_units) - candidate
        pending = unique_ids(row.get("uncertain_units"), units)
        critical = row.get("new_critical_errors")
        if not isinstance(critical, list):
            raise ValueError("new_critical_errors must be explicitly supplied")
        for error in critical:
            if not isinstance(error, dict) or error.get("unit_id") not in units or not error.get("kind"):
                raise ValueError("critical errors require a manifest unit and an error kind")
        if critical:
            reasons.append(f"{row['round_id']}: new critical error")
        if protected:
            reasons.append(f"{row['round_id']}: protected item lost")
        uncertain |= bool(pending)
        gains.append(len(candidate) - len(baseline))
        counts.append({"round_id": row["round_id"], "baseline": len(baseline),
                       "candidate": len(candidate), "restored": sorted(candidate-baseline),
                       "lost": sorted(baseline-candidate)})
    arithmetic_eligible = not reasons and all(g >= (3 if route == "Q" else 0) for g in gains)
    if reasons or any(g < 0 for g in gains):
        decision = "failed"
    elif not arithmetic_eligible:
        decision = "quality_screen_not_passed"
    elif uncertain:
        decision = "provisional"
    elif route == "R":
        decision = "resource_screen_eligible"
    elif all(g >= 3 for g in gains):
        decision = "quality_screen_pass"
    else:
        decision = "quality_screen_not_passed"
    return {"screen_decision": decision, "registered_route": route,
            "denominator": 34, "net_gains": gains,
            "rounds": counts, "reasons": reasons,
            "arithmetic_eligible": arithmetic_eligible,
            "reference_pending": uncertain or reference != "human_verified",
            "evidence_status": "synthetic_only" if kind == "synthetic" else reference,
            "release_eligible": False,
            "scope": "offline arithmetic only; no runtime, resource, DOM, holdout or semantic proof"}


def finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def quantile(values, probability):
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * probability)-1)]


def summarize_timing(report):
    if not isinstance(report, dict):
        raise ValueError("timing requires an object with a frozen unit manifest")
    if report.get("schema_version") != 1:
        raise ValueError("unsupported schema_version")
    kind = report.get("evidence_kind")
    if kind not in ("measured", "synthetic"):
        raise ValueError("evidence_kind must be measured or synthetic")
    if kind == "measured" and (
            not re.fullmatch(r"[a-f0-9]{64}", str(report.get("manifest_sha256", "")))
            or not isinstance(report.get("scope_label"), str) or not report["scope_label"]):
        raise ValueError("measured timing requires manifest_sha256 and scope_label")
    if report.get("match_window_ms", 15000) != 15000:
        raise ValueError("this helper uses the frozen 15000ms match window")
    units = unique_ids(report.get("unit_ids"))
    if not units:
        raise ValueError("timing unit manifest must be nonempty")
    rows = report.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("timing rows must be nonempty")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("timing rows must be objects")
    present = unique_ids([r.get("unit_id") for r in rows], units)
    if present != units:
        raise ValueError("timing rows must exactly cover the frozen manifest; missing unit IDs: "
                         + ", ".join(sorted(units - present)))
    delays, all_delays, late_units = [], [], []
    for row in rows:
        end = finite_number(row.get("end_ms"), "end_ms")
        if end < 0:
            raise ValueError("end_ms must be nonnegative")
        if row.get("timing_reference", "unknown") not in (
                "historical", "ai_reviewed", "human_verified", "unknown"):
            raise ValueError("unknown timing_reference")
        arrival = row.get("stable_correct_ms")
        if arrival is None:
            all_delays.append(math.inf)
        else:
            arrival = finite_number(arrival, "stable_correct_ms")
            if arrival < 0:
                raise ValueError("stable_correct_ms must be nonnegative")
            delay = arrival - end
            if delay > 15000:
                late_units.append(row["unit_id"])
                all_delays.append(math.inf)
            else:
                delays.append(delay)
                all_delays.append(delay)
    overall = quantile(all_delays, .95)
    return {"denominator": len(units), "matched": len(delays),
            "within_2s": sum(d <= 2000 for d in all_delays),
            "within_3s": sum(d <= 3000 for d in all_delays),
            "within_5s": sum(d <= 5000 for d in all_delays),
            "within_15s": sum(d <= 15000 for d in all_delays),
            "matched_p50_ms": quantile(delays, .50),
            "matched_p95_ms": quantile(delays, .95),
            "all_units_p95_ms": overall if math.isfinite(overall) else None,
            "all_units_p95_unbounded": not math.isfinite(overall),
            "early_units": sum(d < 0 for d in delays),
            "late_units": late_units, "match_window_ms": 15000,
            "tail_sample_count_sufficient": len(delays) >= 20,
            "all_references_human_declared": all(
                r.get("timing_reference") == "human_verified" for r in rows),
            "formal_tail_claim_eligible": False,
            "evidence_status": "synthetic_only" if kind == "synthetic" else "measured",
            "quantile_method": "nearest_rank; unmatched=+infinity; early values retained",
            "release_eligible": False,
            "scope": "timing arithmetic only; no clock calibration, semantic, runtime or release proof"}


def validate_events(rows):
    """Check normalized host-clock events; does not replace the v1 contract suite."""
    if not isinstance(rows, list) or not rows:
        return ["events must be a nonempty list"]
    errors, sessions, contract_count = [], {}, 0
    for index, row in enumerate(rows, 1):
        prefix = f"row {index}"
        if not isinstance(row, dict):
            errors.append(f"{prefix}: event must be an object")
            continue
        event = row.get("event")
        if not isinstance(event, str) or event not in CONTRACT_EVENTS | TELEMETRY_EVENTS:
            errors.append(f"{prefix}: unknown event name")
            continue
        contract_count += event in CONTRACT_EVENTS
        sid = row.get("session_id")
        if not isinstance(sid, str) or not sid:
            errors.append(f"{prefix}: missing session_id")
            continue
        state = sessions.setdefault(sid, {"accepted": 0, "seq": 0, "end": 0,
                                          "closed": False, "t_ns": -1})
        try:
            t = finite_number(row.get("t_ns"), "t_ns")
            if t < 0 or t < state["t_ns"]:
                raise ValueError("events must be normalized and sorted by host monotonic time")
            state["t_ns"] = t
            if event == "pcm_accepted":
                accepted = row.get("accepted_samples")
                if type(accepted) is not int or accepted < state["accepted"] or state["closed"]:
                    raise ValueError("invalid accepted sample counter")
                state["accepted"] = accepted
            elif event in ("cancel", "finished"):
                state["closed"] = True
            elif event == "caption_emit":
                start, end, seq = (row.get(k) for k in ("start_sample", "end_sample", "seq"))
                if state["closed"]:
                    raise ValueError("caption after cancellation or finish")
                if any(type(v) is not int for v in (start, end, seq)):
                    raise ValueError("caption range and seq must be integers")
                if not 0 <= start < end <= state["accepted"]:
                    raise ValueError("caption range exceeds real accepted audio")
                if seq != state["seq"] + 1 or end < state["end"]:
                    raise ValueError("duplicate/out-of-order caption")
                state["seq"], state["end"] = seq, end
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
    if not contract_count:
        errors.append("at least one contract event is required; telemetry alone is insufficient")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("gate", "timing", "events"))
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "events":
            data = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
            errors = validate_events(data)
            names = [row.get("event") for row in data if isinstance(row, dict)
                     and isinstance(row.get("event"), str)]
            result = {"valid": not errors, "errors": errors, "release_eligible": False,
                      "checked_contract_events": sum(name in CONTRACT_EVENTS for name in names),
                      "ignored_telemetry_events": sum(name in TELEMETRY_EVENTS for name in names),
                      "scope": EVENT_SCOPE}
            code = 0 if not errors else 2
        else:
            data = json.loads(args.input.read_text())
            result = evaluate_offline(data) if args.command == "gate" else summarize_timing(data)
            code = 0
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return code
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"valid": False, "error": str(exc), "release_eligible": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
