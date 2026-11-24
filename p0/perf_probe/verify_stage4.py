"""Stage 4 A/B verification: draft_off (0.0s) vs draft_on (1.2s) on live 1x WebSocket."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine/src"))
sys.path.insert(0, str(REPO_ROOT / "p0/perf_probe"))
sys.path.insert(0, str(REPO_ROOT / "p0/tier2_mt"))

from dataset import CACHE_ROOT, D1_PCM, percentile  # noqa: E402
from subtitle_service.cli import token_path  # noqa: E402
from subtitle_service.config import load_settings, load_token  # noqa: E402
from subtitle_service.replay import replay  # noqa: E402
from verify_m1_pro_hq_live import ResourceSampler, find_server_pid, launched_server  # noqa: E402
from verify_stage2 import FRAME_64MS_BYTES, active_seek_64ms, analyze_stage2_latencies  # noqa: E402

OUT_DIR = REPO_ROOT / "p0/evidence/stage4-20261124"
ARCHIVE_DIR = REPO_ROOT / "docs/experiments/stage4-2026-11-24"
CFG_PATH = REPO_ROOT / "engine/config/profiles.toml"
SUITE_PATH = REPO_ROOT / "p0/fixtures/suite.json"


def analyze_stage4_extra(trace_path: Path, model_jsonl: Path, session_id: str) -> dict:
    events = [json.loads(ln) for ln in trace_path.read_text().splitlines() if ln.strip()]
    captions_by_seq = {
        e["event"]["seq"]: e["event"]
        for e in events
        if e.get("event", {}).get("type") == "caption"
    }
    m_rows = [
        json.loads(ln) for ln in model_jsonl.read_text().splitlines()
        if ln.strip() and json.loads(ln).get("session_id") == session_id
    ]
    ready_t = next((m["t"] for m in m_rows if m.get("stage") == "ready"), None)
    draft_events = [m for m in m_rows if m.get("stage") == "draft_sent"]
    first_delta_by_seq: dict[int, float] = {}
    for m in m_rows:
        if m.get("stage") == "ws_delta_sent":
            sq = m.get("seq")
            if sq is not None and sq not in first_delta_by_seq:
                first_delta_by_seq[sq] = m["t"]

    onset_to_first_delta_s = []
    if ready_t is not None:
        for sq, cap in sorted(captions_by_seq.items()):
            t_d = first_delta_by_seq.get(sq)
            if t_d is not None:
                onset_to_first_delta_s.append(round((t_d - ready_t) - (cap["start_sample"] / 16000.0), 3))

    return {
        "draft_sent_count": len(draft_events),
        "onset_to_first_delta_p50_s": percentile(onset_to_first_delta_s, 0.50),
        "onset_to_first_delta_p95_s": percentile(onset_to_first_delta_s, 0.95),
        "draft_samples": [
            {
                "start_s": round(d["start_sample"] / 16000.0, 2),
                "end_s": round(d["end_sample"] / 16000.0, 2),
                "text_ja": d.get("text_ja", ""),
                "text_zh": d.get("text_zh", ""),
            }
            for d in draft_events[:12]
        ],
    }


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    suite = {x["id"]: x for x in json.loads(SUITE_PATH.read_text())}
    d1_audio = D1_PCM.read_bytes()
    base_toml = CFG_PATH.read_text(encoding="utf-8").replace(
        'cache_root = "../../.model-cache"',
        f'cache_root = "{CACHE_ROOT}"',
    )

    runs = {}
    for label, interval in [("draft_off", 0.0), ("draft_on", 1.2)]:
        run_dir = OUT_DIR / label
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg_file = run_dir / "profiles.toml"
        toml_text = base_toml.replace("draft_interval_s = 1.2", f"draft_interval_s = {interval}")
        cfg_file.write_text(toml_text, encoding="utf-8")
        config = load_settings(cfg_file)
        with launched_server(cfg_file, config, run_dir):
            pid = find_server_pid(config.server.port)
            assert pid is not None
            token = load_token(token_path(), create=False)
            res_map = {}
            with ResourceSampler(pid) as sampler:
                for cid in ["csj-reproduction", "csj-task", "wmf-04", "silence"]:
                    audio = (SUITE_PATH.parent / suite[cid]["file"]).read_bytes()
                    tp = run_dir / f"{cid}.jsonl"
                    with tp.open("w", buffering=1) as trace:
                        r = asyncio.run(replay(config, "m1-pro-nemo", token, cid, audio, trace, frame_bytes=FRAME_64MS_BYTES))
                    sid = json.loads(tp.read_text().splitlines()[0])["event"]["session_id"]
                    res_map[cid] = r | analyze_stage2_latencies(tp, run_dir / "model.jsonl", sid) | analyze_stage4_extra(tp, run_dir / "model.jsonl", sid)

                d1_tp = run_dir / "d1.jsonl"
                with d1_tp.open("w", buffering=1) as trace:
                    d1_r = asyncio.run(replay(config, "m1-pro-nemo", token, "d1-ep01-45s", d1_audio, trace, frame_bytes=FRAME_64MS_BYTES))
                d1_sid = json.loads(d1_tp.read_text().splitlines()[0])["event"]["session_id"]
                res_map["d1-ep01-45s"] = d1_r | analyze_stage2_latencies(d1_tp, run_dir / "model.jsonl", d1_sid) | analyze_stage4_extra(d1_tp, run_dir / "model.jsonl", d1_sid)

                csj_audio = (SUITE_PATH.parent / suite["csj-reproduction"]["file"]).read_bytes()
                seek_r = asyncio.run(active_seek_64ms(config, "m1-pro-nemo", token, csj_audio, run_dir))

            runs[label] = {
                "results": res_map,
                "seek_50ms": seek_r,
                "resources": {
                    "peak_rss_mib": max(sampler.rss_mib) if sampler.rss_mib else 0.0,
                    "mean_cpu_pct": round(sum(sampler.cpu_pct) / max(1, len(sampler.cpu_pct)), 1),
                    "peak_cpu_pct": max(sampler.cpu_pct) if sampler.cpu_pct else 0.0,
                },
            }

    parity = {
        cid: runs["draft_off"]["results"][cid]["texts"] == runs["draft_on"]["results"][cid]["texts"]
        for cid in ["csj-reproduction", "csj-task", "wmf-04", "d1-ep01-45s"]
    }
    summary = {"runs": runs, "finalized_exact_parity": parity}
    (ARCHIVE_DIR / "stage4_verification.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("STAGE4_PARITY:", json.dumps(parity), flush=True)
    for cid in ["d1-ep01-45s", "csj-reproduction", "csj-task", "wmf-04"]:
        a = runs["draft_off"]["results"][cid]
        b = runs["draft_on"]["results"][cid]
        print(
            f"{cid}: onset->delta P50 {a['onset_to_first_delta_p50_s']}s -> {b['onset_to_first_delta_p50_s']}s | "
            f"final P95 {a['final_e2e_p95_s']}s -> {b['final_e2e_p95_s']}s | drafts={b['draft_sent_count']}",
            flush=True,
        )
    print("STAGE4_DONE", flush=True)


if __name__ == "__main__":
    main()
