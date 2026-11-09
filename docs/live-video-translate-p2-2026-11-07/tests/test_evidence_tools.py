"""Checks for false promotion and missing-latency errors; all values are synthetic.

Some fixtures deliberately declare measured evidence to exercise that input branch;
the test data are never actual model measurements.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    ids = [f"u{i:02}" for i in range(34)]
    return {
        "schema_version": 1, "evidence_kind": "synthetic", "registered_route": "Q",
        "unit_ids": ids, "reference_status": "ai_reviewed", "protected_unit_ids": ids[:2],
        "rounds": [
            {"round_id": "seed11", "baseline_usable": ids[:20],
             "candidate_usable": ids[:23], "new_critical_errors": [],
             "protected_losses": [], "uncertain_units": []},
            {"round_id": "seed29", "baseline_usable": ids[:18],
             "candidate_usable": ids[:21], "new_critical_errors": [],
             "protected_losses": [], "uncertain_units": []},
        ],
    }


def timing_fixture(rows, evidence_kind="synthetic"):
    return {"schema_version": 1, "evidence_kind": evidence_kind,
            "unit_ids": [row["unit_id"] for row in rows], "rows": rows,
            **({"manifest_sha256": "0" * 64, "scope_label": "test-only"}
               if evidence_kind == "measured" else {})}


class EvidenceToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "scripts/evidence_tools.py"
        cls.mod = None
        if path.exists():
            spec = importlib.util.spec_from_file_location("evidence_tools", path)
            cls.mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.mod)

    def api(self):
        self.assertIsNotNone(self.mod, "Evidence evaluator has not been implemented")
        return self.mod

    def timing_result(self, report):
        try:
            return self.api().summarize_timing(report)
        except ValueError as exc:
            self.fail(f"valid manifest-backed timing report was rejected: {exc}")

    def test_two_round_net_gain_is_only_screen_pass(self):
        result = self.api().evaluate_offline(fixture())
        self.assertEqual(result["screen_decision"], "quality_screen_pass")
        self.assertEqual(result["net_gains"], [3, 3])
        self.assertFalse(result["release_eligible"])

    def test_synthetic_data_cannot_be_real_evidence(self):
        result = self.api().evaluate_offline(fixture())
        self.assertEqual(result["evidence_status"], "synthetic_only")

    def test_q_plus_two_cannot_switch_to_resource_route(self):
        report = fixture()
        report["rounds"][0]["candidate_usable"] = report["unit_ids"][:22]
        report["rounds"][1]["candidate_usable"] = report["unit_ids"][:20]
        result = self.api().evaluate_offline(report)
        self.assertEqual(result["screen_decision"], "quality_screen_not_passed")
        self.assertEqual(result["registered_route"], "Q")
        self.assertFalse(result["release_eligible"])

    def test_r_equal_quality_is_only_resource_screen_eligible(self):
        report = fixture()
        report["registered_route"] = "R"
        for row in report["rounds"]:
            row["candidate_usable"] = row["baseline_usable"][:]
        result = self.api().evaluate_offline(report)
        self.assertEqual(result["screen_decision"], "resource_screen_eligible")
        self.assertEqual(result.get("registered_route"), "R")
        self.assertFalse(result["release_eligible"])

    def test_r_plus_three_stays_in_the_registered_resource_route(self):
        report = fixture()
        report["registered_route"] = "R"
        self.assertEqual(self.api().evaluate_offline(report)["screen_decision"],
                         "resource_screen_eligible")

    def test_missing_or_invalid_registered_route_rejected(self):
        for route in (None, "", "q", "other"):
            with self.subTest(route=route):
                report = fixture()
                if route is None:
                    del report["registered_route"]
                else:
                    report["registered_route"] = route
                with self.assertRaises(ValueError):
                    self.api().evaluate_offline(report)

    def test_r_cannot_hide_a_losing_round(self):
        report = fixture()
        report["registered_route"] = "R"
        report["rounds"][1]["candidate_usable"] = report["unit_ids"][:17]
        self.assertEqual(self.api().evaluate_offline(report)["screen_decision"], "failed")

    def test_one_winning_round_cannot_mask_losing_round(self):
        report = fixture()
        report["rounds"][1]["candidate_usable"] = report["unit_ids"][:17]
        self.assertEqual(self.api().evaluate_offline(report)["screen_decision"], "failed")

    def test_critical_error_on_previously_failed_unit_blocks_promotion(self):
        report = fixture()
        report["rounds"][0]["new_critical_errors"] = [{"unit_id": "u33", "kind": "negation_reversal"}]
        self.assertEqual(self.api().evaluate_offline(report)["screen_decision"], "failed")

    def test_protected_loss_blocks_equal_count_trade(self):
        report = fixture()
        report["rounds"][0]["protected_losses"] = ["u00"]
        self.assertEqual(self.api().evaluate_offline(report)["screen_decision"], "failed")

    def test_unknown_and_duplicate_units_rejected(self):
        for replacement in (["u00", "u00"], ["not-in-manifest"]):
            report = fixture()
            report["rounds"][0]["candidate_usable"] = replacement
            with self.assertRaises(ValueError):
                self.api().evaluate_offline(report)

    def test_uncertainty_cannot_be_reported_as_certain_gain(self):
        report = fixture()
        report["rounds"][0]["uncertain_units"] = ["u22"]
        self.assertEqual(self.api().evaluate_offline(report)["screen_decision"], "provisional")

    def test_uncertainty_does_not_hide_a_failed_quality_threshold(self):
        report = fixture()
        for row in report["rounds"]:
            row["candidate_usable"] = row["baseline_usable"][:]
            row["uncertain_units"] = ["u33"]
        result = self.api().evaluate_offline(report)
        self.assertEqual(result["screen_decision"], "quality_screen_not_passed")
        self.assertFalse(result["arithmetic_eligible"])
        self.assertTrue(result["reference_pending"])

    def test_uncertain_passing_arithmetic_remains_provisional(self):
        report = fixture()
        report["rounds"][0]["uncertain_units"] = ["u33"]
        result = self.api().evaluate_offline(report)
        self.assertTrue(result["arithmetic_eligible"])
        self.assertTrue(result["reference_pending"])
        self.assertEqual(result["screen_decision"], "provisional")

    def test_protected_loss_cannot_be_hidden_by_an_empty_declaration(self):
        report = fixture()
        report["rounds"][0]["candidate_usable"].remove("u00")
        report["rounds"][0]["candidate_usable"].append("u24")
        self.assertEqual(self.api().evaluate_offline(report)["screen_decision"], "failed")

    def test_wrong_seed_pair_cannot_replace_the_frozen_rounds(self):
        report = fixture()
        report["rounds"][1]["round_id"] = "seed12"
        with self.assertRaises(ValueError):
            self.api().evaluate_offline(report)

    def test_measured_score_requires_a_registration_reference(self):
        report = fixture()
        report["evidence_kind"] = "measured"
        with self.assertRaises(ValueError):
            self.api().evaluate_offline(report)

    def test_non_object_score_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api().evaluate_offline([])

    def test_late_match_is_retained_but_excluded_from_the_fifteen_second_view(self):
        result = self.timing_result(timing_fixture([
            {"unit_id": "late", "end_ms": 1000, "stable_correct_ms": 17000}]))
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["late_units"], ["late"])
        self.assertEqual(result["denominator"], 1)
        self.assertTrue(result["all_units_p95_unbounded"])

    def test_measured_timing_requires_a_frozen_manifest_reference(self):
        report = timing_fixture([{"unit_id": "u", "end_ms": 0,
                                  "stable_correct_ms": 1}], "measured")
        del report["manifest_sha256"]
        with self.assertRaises(ValueError):
            self.api().summarize_timing(report)

    def test_missing_results_remain_in_latency_denominator(self):
        rows = [{"unit_id": f"u{i}", "end_ms": 1000, "stable_correct_ms": 2000 if i < 18 else None}
                for i in range(20)]
        result = self.timing_result(timing_fixture(rows))
        self.assertEqual(result["within_5s"], 18)
        self.assertEqual(result["denominator"], 20)
        self.assertEqual(result["matched_p95_ms"], 1000)
        self.assertIsNone(result["all_units_p95_ms"])
        self.assertTrue(result["all_units_p95_unbounded"])
        self.assertFalse(result["formal_tail_claim_eligible"])

    def test_negative_latency_is_retained_and_counted_separately(self):
        result = self.timing_result(timing_fixture([
            {"unit_id": "u", "end_ms": 2000, "stable_correct_ms": 1500}]))
        self.assertEqual(result["matched_p95_ms"], -500)
        self.assertEqual(result["early_units"], 1)

    def test_timing_requires_a_frozen_unit_manifest(self):
        report = timing_fixture([{"unit_id": "u", "end_ms": 1000, "stable_correct_ms": None}])
        del report["unit_ids"]
        with self.assertRaises(ValueError):
            self.api().summarize_timing(report)

    def test_omitted_timing_row_cannot_shrink_the_denominator(self):
        report = timing_fixture([
            {"unit_id": "ok", "end_ms": 1000, "stable_correct_ms": 2000},
            {"unit_id": "missing", "end_ms": 1000, "stable_correct_ms": None},
        ])
        report["rows"] = report["rows"][:1]
        with self.assertRaises(ValueError):
            self.api().summarize_timing(report)

    def test_unknown_timing_unit_rejected(self):
        report = timing_fixture([{"unit_id": "u", "end_ms": 1000, "stable_correct_ms": 2000}])
        report["rows"][0]["unit_id"] = "outside-manifest"
        with self.assertRaises(ValueError):
            self.api().summarize_timing(report)

    def test_duplicate_timing_row_rejected(self):
        report = timing_fixture([{"unit_id": "u", "end_ms": 1000, "stable_correct_ms": 2000}])
        report["rows"].append(report["rows"][0].copy())
        with self.assertRaises(ValueError):
            self.api().summarize_timing(report)

    def test_duplicate_timing_manifest_unit_rejected(self):
        report = timing_fixture([{"unit_id": "u", "end_ms": 1000, "stable_correct_ms": 2000}])
        report["unit_ids"].append("u")
        with self.assertRaises(ValueError):
            self.api().summarize_timing(report)

    def test_all_missing_timing_rows_keep_the_manifest_denominator(self):
        result = self.timing_result(timing_fixture([
            {"unit_id": "a", "end_ms": 1000, "stable_correct_ms": None},
            {"unit_id": "b", "end_ms": 2000, "stable_correct_ms": None},
        ]))
        self.assertEqual(result["denominator"], 2)
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["within_15s"], 0)
        self.assertIsNone(result["matched_p50_ms"])
        self.assertIsNone(result["matched_p95_ms"])
        self.assertIsNone(result["all_units_p95_ms"])
        self.assertTrue(result["all_units_p95_unbounded"])
        self.assertFalse(result["release_eligible"])
        self.assertIn("arithmetic", result["scope"])

    def test_timing_schema_and_evidence_kind_are_required(self):
        for key, replacement in (("schema_version", None), ("schema_version", 2),
                                 ("evidence_kind", None), ("evidence_kind", "unknown")):
            with self.subTest(key=key, replacement=replacement):
                report = timing_fixture([{"unit_id": "u", "end_ms": 1000,
                                          "stable_correct_ms": None}])
                if replacement is None:
                    del report[key]
                else:
                    report[key] = replacement
                with self.assertRaises(ValueError):
                    self.api().summarize_timing(report)

    def test_legacy_timing_row_list_is_rejected_without_a_manifest(self):
        with self.assertRaises(ValueError):
            self.api().summarize_timing([
                {"unit_id": "u", "end_ms": 1000, "stable_correct_ms": 2000}])

    def test_synthetic_timing_cannot_claim_formal_tail_evidence(self):
        report = timing_fixture([
            {"unit_id": f"u{i}", "end_ms": 1000, "stable_correct_ms": 2000,
             "timing_reference": "human_verified"} for i in range(20)])
        result = self.timing_result(report)
        self.assertFalse(result["formal_tail_claim_eligible"])
        self.assertEqual(result["evidence_status"], "synthetic_only")

    def test_measured_human_timing_is_still_not_a_release_decision(self):
        report = timing_fixture([
            {"unit_id": f"u{i}", "end_ms": 1000, "stable_correct_ms": 2000,
             "timing_reference": "human_verified"} for i in range(20)], "measured")
        result = self.timing_result(report)
        self.assertFalse(result["formal_tail_claim_eligible"])
        self.assertTrue(result["tail_sample_count_sufficient"])
        self.assertTrue(result["all_references_human_declared"])
        self.assertEqual(result["matched_p95_ms"], 1000)
        self.assertFalse(result["release_eligible"])

    def test_empty_timing_manifest_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api().summarize_timing(timing_fixture([]))

    def test_empty_events_are_not_valid_evidence(self):
        self.assertTrue(self.api().validate_events([]))

    def test_unknown_event_name_cannot_bypass_caption_checks(self):
        rows = [
            {"event": "pcm_accepted", "session_id": "s", "t_ns": 1, "accepted_samples": 16000},
            {"event": "caption_emti", "session_id": "s", "t_ns": 2, "seq": 1,
             "start_sample": 0, "end_sample": 999999},
        ]
        self.assertTrue(self.api().validate_events(rows))

    def test_telemetry_only_stream_needs_a_contract_event(self):
        self.assertTrue(self.api().validate_events([
            {"event": "resource_sample", "session_id": "s", "t_ns": 1}]))

    def test_documented_telemetry_names_are_allowed_with_contract_events(self):
        names = ["capture", "pcm_consumed", "vad_release", "asr_enqueue", "asr_start",
                 "asr_end", "source_commit", "mt_enqueue", "mt_start", "mt_first_token",
                 "mt_end", "ast_preprocess", "ast_encoder", "ast_prefill", "ast_decode",
                 "caption_filtered", "ws_receive", "dom_visible", "dom_hidden", "dom_removed",
                 "drain", "worker_return", "resource_sample"]
        rows = [{"event": "pcm_accepted", "session_id": "s", "t_ns": 0,
                 "accepted_samples": 16000}]
        rows.extend({"event": name, "session_id": "s", "t_ns": i}
                    for i, name in enumerate(names, 1))
        self.assertEqual(self.api().validate_events(rows), [])

    def test_padding_may_not_extend_real_caption_range(self):
        rows = [
            {"event": "pcm_accepted", "session_id": "s", "t_ns": 1, "accepted_samples": 16000},
            {"event": "caption_emit", "session_id": "s", "t_ns": 2, "seq": 1,
             "start_sample": 0, "end_sample": 30400},
        ]
        self.assertTrue(self.api().validate_events(rows))

    def test_old_session_output_after_cancel_rejected(self):
        rows = [
            {"event": "pcm_accepted", "session_id": "s", "t_ns": 1, "accepted_samples": 16000},
            {"event": "cancel", "session_id": "s", "t_ns": 2},
            {"event": "caption_emit", "session_id": "s", "t_ns": 3, "seq": 1,
             "start_sample": 0, "end_sample": 10000},
        ]
        self.assertTrue(self.api().validate_events(rows))

    def test_valid_session_and_new_epoch_do_not_cross_contaminate(self):
        rows = [
            {"event": "pcm_accepted", "session_id": "a", "t_ns": 1, "accepted_samples": 16000},
            {"event": "caption_emit", "session_id": "a", "t_ns": 2, "seq": 1,
             "start_sample": 0, "end_sample": 10000},
            {"event": "cancel", "session_id": "a", "t_ns": 3},
            {"event": "pcm_accepted", "session_id": "b", "t_ns": 4, "accepted_samples": 3200},
            {"event": "caption_emit", "session_id": "b", "t_ns": 5, "seq": 1,
             "start_sample": 0, "end_sample": 3200},
        ]
        self.assertEqual(self.api().validate_events(rows), [])

    def test_cli_rejects_invalid_manifest_with_nonzero_status(self):
        self.api()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "score.json"
            report = fixture()
            report["rounds"] = report["rounds"][:1]
            path.write_text(json.dumps(report))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/evidence_tools.py"), "gate", str(path)],
                                    capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_cli_events_reports_partial_scope_and_checked_counts(self):
        rows = [
            {"event": "capture", "session_id": "s", "t_ns": 0},
            {"event": "pcm_accepted", "session_id": "s", "t_ns": 1, "accepted_samples": 16000},
            {"event": "caption_emit", "session_id": "s", "t_ns": 2, "seq": 1,
             "start_sample": 0, "end_sample": 16000},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/evidence_tools.py"),
                                     "events", str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data.get("checked_contract_events"), 2)
        self.assertEqual(data.get("ignored_telemetry_events"), 1)
        self.assertIn("partial", data.get("scope", ""))
        self.assertFalse(data["release_eligible"])

    def test_cli_empty_events_is_rejected_with_nonzero_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.jsonl"
            path.write_text("")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/evidence_tools.py"),
                                     "events", str(path)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["valid"])


if __name__ == "__main__":
    unittest.main()
