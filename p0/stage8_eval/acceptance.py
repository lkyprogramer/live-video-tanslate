"""Stage 8 T7 release gate evaluator (strict FAIL/BLOCKED/INCONCLUSIVE/PASS contract)."""

from __future__ import annotations

from typing import Any


CANONICAL_VERDICTS = frozenset({"PASS", "FAIL", "INCONCLUSIVE", "BLOCKED"})
_PRECEDENCE = {"FAIL": 3, "BLOCKED": 2, "INCONCLUSIVE": 1, "PASS": 0}


def _worst_verdict(verdicts: list[str]) -> str:
    return max(verdicts, key=lambda v: _PRECEDENCE[v]) if verdicts else "BLOCKED"


def _eval_g0_identity(report: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    roles = report.get("roles") or {}
    if "deployed" not in roles or "candidate" not in roles:
        reasons.append("MISSING_REQUIRED_BASELINES_DEPLOYED_OR_CANDIDATE")
        return "BLOCKED", reasons

    runs = [roles["deployed"], roles["candidate"]]
    if "subtitles_off" in roles:
        runs.append(roles["subtitles_off"])

    expected_th = report.get("thresholds_sha256")
    media_hashes = {r.get("media_sha256") for r in runs if r.get("media_sha256")}
    if len(media_hashes) > 1:
        reasons.append("MEDIA_HASH_MISMATCH")
        return "FAIL", reasons

    for r in runs:
        th = r.get("thresholds_sha256")
        if th and expected_th and th != expected_th:
            reasons.append("THRESHOLDS_HASH_MISMATCH")
            return "FAIL", reasons
        if int(r.get("holdout_reuse_count", 1) or 1) > 1:
            reasons.append("HOLDOUT_REUSED_MORE_THAN_ONCE")
            return "FAIL", reasons
        if r.get("all_timeouts_filtered") or int(r.get("dropped_timeouts", 0) or 0) > 0:
            reasons.append("TIMEOUTS_FILTERED_FROM_DENOMINATOR")
            return "FAIL", reasons

    for role_name in ("deployed", "candidate"):
        dom = roles[role_name].get("dom_events") or {}
        if int(dom.get("total", 0) or 0) <= 0 or (
            int(dom.get("streaming", 0) or 0) + int(dom.get("final", 0) or 0) <= 0
        ):
            reasons.append(f"MISSING_DOM_EVENTS_{role_name.upper()}")
            return "BLOCKED", reasons

    return "PASS", ["G0_IDENTITY_AND_DOM_OK"]


def _eval_g1_lifecycle(cand: dict[str, Any], thresholds: dict[str, Any]) -> tuple[str, list[str]]:
    lc = cand.get("lifecycle") or {}
    cfg = thresholds.get("lifecycle") or {}
    checks = [
        ("new_disconnects", int(cfg.get("new_disconnects_max", 0)), "LIFECYCLE_NEW_DISCONNECTS"),
        ("new_overloads", int(cfg.get("new_overloads_max", 0)), "LIFECYCLE_NEW_OVERLOADS"),
        ("old_epoch_commits", int(cfg.get("old_epoch_commits_max", 0)), "LIFECYCLE_OLD_EPOCH_COMMITS"),
        ("silent_final_drops", int(cfg.get("silent_final_drops_max", 0)), "LIFECYCLE_SILENT_FINAL_DROPS"),
        ("out_of_order_final_accepts", int(cfg.get("out_of_order_final_accepts_max", 0)), "LIFECYCLE_OUT_OF_ORDER"),
    ]
    reasons = [code for field, limit, code in checks if int(lc.get(field, 0) or 0) > limit]
    if reasons:
        return "FAIL", reasons
    return "PASS", ["G1_LIFECYCLE_OK"]


def _eval_g2_latency_and_availability(
    dep: dict[str, Any],
    cand: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    promotion_tier: str,
) -> tuple[str, list[str]]:
    avail_cfg = thresholds.get("availability") or {}
    tier_cfg = thresholds.get(promotion_tier) or thresholds.get("default_quality") or {}

    cand_av = cand.get("availability") or {}
    dep_av = dep.get("availability") or {}
    cand_lat = cand.get("dom_latency") or {}
    dep_lat = dep.get("dom_latency") or {}

    if not cand_av or not cand_lat:
        return "BLOCKED", ["MISSING_UNIT_ALIGNED_DOM_AVAILABILITY_OR_LATENCY"]

    p95 = cand_lat.get("unit_end_to_final_dom_p95_ms")
    if p95 is None:
        return "FAIL", ["MISSING_FINAL_DOM_P95_TREATED_AS_INFINITE"]

    cap_ms = float(tier_cfg.get("unit_end_to_final_dom_p95_cap_ms", 1200.0))
    if float(p95) > cap_ms:
        return "FAIL", [f"FINAL_DOM_P95_EXCEEDS_CAP_{cap_ms}"]

    dep_p95 = dep_lat.get("unit_end_to_final_dom_p95_ms")
    if dep_p95 is None:
        return "BLOCKED", ["MISSING_DEPLOYED_FINAL_DOM_P95"]

    if promotion_tier == "opt_in_quality":
        max_delta = float(tier_cfg.get("latency_delta_p95_max_ms", 300.0))
    else:
        abs_cap = float(tier_cfg.get("latency_delta_p95_max_ms", 100.0))
        frac_cap = float(tier_cfg.get("latency_delta_p95_max_fraction", 0.10)) * float(dep_p95)
        max_delta = min(abs_cap, frac_cap)
    if float(p95) - float(dep_p95) > max_delta:
        return "FAIL", ["FINAL_DOM_P95_REGRESSION"]

    final_vis = cand_av.get("final_visible_rate")
    first_use = cand_av.get("first_useful_rate")
    by_2000 = cand_av.get("final_by_2000ms_rate")
    if final_vis is None or first_use is None or by_2000 is None:
        return "FAIL", ["MISSING_AVAILABILITY_METRICS"]
    if float(final_vis) < float(avail_cfg.get("final_visible_min", 0.98)):
        return "FAIL", ["FINAL_VISIBLE_BELOW_MIN"]
    if float(first_use) < float(avail_cfg.get("first_useful_min", 0.95)):
        return "FAIL", ["FIRST_USEFUL_BELOW_MIN"]
    if float(by_2000) < float(avail_cfg.get("final_by_2000ms_min", 0.95)):
        return "FAIL", ["FINAL_BY_2000MS_BELOW_MIN"]

    dep_vis = dep_av.get("final_visible_rate")
    if dep_vis is not None and (float(dep_vis) - float(final_vis)) > float(
        avail_cfg.get("coverage_max_absolute_decrease", 0.005)
    ):
        return "FAIL", ["COVERAGE_DROP_EXCEEDS_0_005"]

    return "PASS", ["G2_AVAILABILITY_AND_LATENCY_OK"]


def _eval_g3_resources(
    cand: dict[str, Any],
    report: dict[str, Any],
    thresholds: dict[str, Any],
) -> tuple[str, list[str]]:
    res_cfg = thresholds.get("resources") or {}
    if cand.get("resource_abort"):
        return "FAIL", ["CANDIDATE_RESOURCE_ABORT"]
    swap = float(cand.get("swap_growth_mib", 0.0) or 0.0)
    if swap > float(res_cfg.get("swap_growth_stop_mib", 256.0)):
        return "FAIL", ["SWAP_GROWTH_EXCEEDS_STOP_LIMIT"]
    drop_delta = (report.get("comparison") or {}).get("drop_rate_delta_vs_subtitles_off_pp")
    if drop_delta is None and "subtitles_off" not in (report.get("roles") or {}):
        return "BLOCKED", ["SUBTITLES_OFF_BASELINE_MISSING"]
    if drop_delta is not None and float(drop_delta) > float(
        res_cfg.get("video_drop_rate_increase_max_percentage_points", 1.0)
    ):
        return "FAIL", ["VIDEO_DROP_RATE_INCREASE_EXCEEDS_1PP"]
    return "PASS", ["G3_RESOURCES_OK"]


def _eval_g4_quality_and_review(
    report: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    promotion_tier: str,
) -> tuple[str, list[str]]:
    qual_cfg = thresholds.get(promotion_tier) or thresholds.get("default_quality") or {}
    human_review = report.get("human_review")
    holdout = report.get("holdout")

    if isinstance(human_review, dict):
        if int(human_review.get("protected_new_e4", 0) or 0) > int(qual_cfg.get("protected_new_e4_max", 0)):
            return "FAIL", ["PROTECTED_NEW_E4_REGRESSION"]
        if int(human_review.get("protected_key_info_regressions", 0) or 0) > int(
            qual_cfg.get("protected_key_information_regressions_max", 0)
        ):
            return "FAIL", ["PROTECTED_KEY_INFO_REGRESSION"]

    if not isinstance(holdout, dict):
        return "BLOCKED", ["HOLDOUT_MISSING"]
    if int(holdout.get("reuse_count", 1) or 1) > 1:
        return "FAIL", ["HOLDOUT_REUSED_MORE_THAN_ONCE"]
    if not isinstance(human_review, dict) or not human_review.get("verified"):
        return "INCONCLUSIVE", ["QUALITY_REVIEW_MISSING"]

    rel_red = holdout.get("critical_error_relative_reduction")
    ci_low = (holdout.get("paired_bootstrap") or {}).get("ci95_low")
    if rel_red is None or ci_low is None:
        return "INCONCLUSIVE", ["QUALITY_STATISTICAL_DATA_MISSING"]
    min_red = float(qual_cfg.get("critical_error_relative_reduction_min", 0.25))
    if float(rel_red) < 0.0:
        return "FAIL", ["CRITICAL_ERROR_INCREASED"]
    if float(rel_red) < min_red or float(ci_low) <= 0.0:
        return "INCONCLUSIVE", ["INSUFFICIENT_QUALITY_IMPROVEMENT_OR_CI_LOWER_NON_POSITIVE"]

    return "PASS", ["G4_QUALITY_AND_REVIEW_OK"]


def evaluate_gates(
    report: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    promotion_tier: str = "default_quality",
) -> dict[str, Any]:
    """Evaluate release gates; never defaults missing evidence to PASS."""
    roles = report.get("roles") or {}
    g0_status, g0_reasons = _eval_g0_identity(report)

    if "deployed" not in roles or "candidate" not in roles:
        gates = {
            "g0_identity_and_dom": {"status": g0_status, "reasons": g0_reasons},
            "g1_lifecycle": {"status": "BLOCKED", "reasons": ["MISSING_REQUIRED_BASELINES_DEPLOYED_OR_CANDIDATE"]},
            "g2_availability_and_latency": {"status": "BLOCKED", "reasons": ["MISSING_REQUIRED_BASELINES_DEPLOYED_OR_CANDIDATE"]},
            "g3_resources": {"status": "BLOCKED", "reasons": ["MISSING_REQUIRED_BASELINES_DEPLOYED_OR_CANDIDATE"]},
            "g4_quality_and_review": _eval_g4_quality_and_review(report, thresholds, promotion_tier=promotion_tier),
        }
        # Normalize g4 tuple
        g4_st, g4_rs = gates["g4_quality_and_review"]
        gates["g4_quality_and_review"] = {"status": g4_st, "reasons": g4_rs}
        verdict = _worst_verdict([g["status"] for g in gates.values()])
        return {
            "schema_version": "stage8-t7-acceptance-v1",
            "promotion_tier": promotion_tier,
            "verdict": verdict,
            "stability_verdict": _worst_verdict([g0_status]),
            "gates": gates,
        }

    dep = roles["deployed"]
    cand = roles["candidate"]
    g1_status, g1_reasons = _eval_g1_lifecycle(cand, thresholds)
    g2_status, g2_reasons = _eval_g2_latency_and_availability(dep, cand, thresholds, promotion_tier=promotion_tier)
    g3_status, g3_reasons = _eval_g3_resources(cand, report, thresholds)
    g4_status, g4_reasons = _eval_g4_quality_and_review(report, thresholds, promotion_tier=promotion_tier)

    gates = {
        "g0_identity_and_dom": {"status": g0_status, "reasons": g0_reasons},
        "g1_lifecycle": {"status": g1_status, "reasons": g1_reasons},
        "g2_availability_and_latency": {"status": g2_status, "reasons": g2_reasons},
        "g3_resources": {"status": g3_status, "reasons": g3_reasons},
        "g4_quality_and_review": {"status": g4_status, "reasons": g4_reasons},
    }
    verdict = _worst_verdict([g["status"] for g in gates.values()])
    stability_verdict = _worst_verdict([g0_status, g1_status, g3_status])
    return {
        "schema_version": "stage8-t7-acceptance-v1",
        "promotion_tier": promotion_tier,
        "verdict": verdict,
        "stability_verdict": stability_verdict,
        "gates": gates,
    }
