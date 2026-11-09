"""Evidence arithmetic, exact-output adjudication reuse, and readable DOM joins."""
import importlib.util
import json

from .freeze import PLAN, digest, dump, rows


def evidence_helper():
    spec = importlib.util.spec_from_file_location("p2_evidence_tools", PLAN / "scripts/evidence_tools.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def readable_cues(captions, events):
    result = []
    for caption in captions:
        related = sorted((e for e in events if (e.get("session_id"), e.get("seq")) ==
                          (caption["session_id"], caption["seq"])), key=lambda e: e["t"])
        intervals = []
        opened = None
        for event in related:
            if event["stage"] == "dom_visible":
                opened = event["t"]
            elif event["stage"] in ("dom_removed", "dom_hidden") and opened is not None:
                intervals.append((opened, event["t"]))
                opened = None
        required = max(600, sum(ch.isalnum() for ch in caption["text_zh"]) / 12 * 1000)
        readable = next(((start, end) for start, end in intervals if end-start >= required), None)
        longest = max((end-start for start, end in intervals), default=None)
        result.append(caption | dict(readable=readable is not None, duration_ms=longest,
                      required_ms=required, first_readable_visible_ms=readable[0] if readable else None,
                      incomplete_visibility_interval=opened is not None))
    return result


def score_baseline(run):
    asr = json.loads((run / "baseline/asr-parity.json").read_text())
    mt = json.loads((run / "baseline/mt-parity.json").read_text())
    if not asr["exact_parity"] or not all(x["exact_parity"] for x in mt.values()):
        raise ValueError("cannot reuse historical judgments for changed text")
    reference = json.loads((run / "reference-manifest.json").read_text())
    verdict = json.loads((run / "inputs/verdict.json").read_text())
    report = dict(schema_version=1, evidence_kind="measured", registered_route="R",
                  reference_status="ai_reviewed", unit_ids=[f"{u['clip']}/{u['group']}" for u in reference["units"]],
                  protected_unit_ids=reference["protected_unit_ids"],
                  registration_ref=dict(experiment_id="B0", registry_sha256=digest(run / "locks/registry.json")),
                  rounds=[])
    pending = [u["unit"] for u in json.loads((run / "pending-listen.json").read_text())]
    for seed in (11, 29):
        usable = verdict["outcomes"][f"m0-{seed}"]["usable"]
        report["rounds"].append(dict(round_id=f"seed{seed}", baseline_usable=usable, candidate_usable=usable,
                                     protected_losses=[], uncertain_units=pending, new_critical_errors=[]))
    dump(run / "baseline/score-input.json", report)
    result = evidence_helper().evaluate_offline(report)
    result.update(role="baseline_control_not_candidate", exact_output_reproduction=True,
                  verdict_reused_only_after_exact_parity=True, ready_for_candidate_screening=True)
    dump(run / "baseline/score.json", result)
    return result
